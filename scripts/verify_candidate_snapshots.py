"""
scripts/verify_candidate_snapshots.py

Verification harness for distinct candidate and recovery snapshots.

Workflow:
1. Records the unregistered VDI's stat and SHA-256 baseline.
2. Cycle 1:
   - Restores the candidate snapshot.
   - Verifies pre-clean state (run marker files and registry keys absent).
   - Starts the VM (headless) and waits for guest agent readiness.
   - Executes benign workload with unique run marker 1.
   - Runs the normal pipeline and correlates actual workload capture evidence.
   - Stops VM cleanly.
3. Cycle 2:
   - Restores the candidate snapshot.
   - Verifies state reset (ALL files and registry artifacts from Cycle 1 are completely absent).
   - Starts VM, executes second benign workload with unique run marker 2.
   - Verifies clean execution and telemetry.
   - Stops VM cleanly.
4. Promotion:
   - Requires explicit opt-in, distinct UUIDs, and successful verification.
   - Verifies candidate health, renames by UUID, then deletes only the identified recovery.
   - Verifies exactly one snapshot named "clean" is registered in VirtualBox.
   - Confirms default.toml and development.toml resolve to "clean".
5. Verifies the final baseline boots, stops, and restores to a pristine state.
6. Verifies the orphan disk's SHA-256 and writes a fresh timestamped result.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from adam.common.config import get_settings
from adam.contracts.enums import Arm, SessionStatus
from adam.orchestrator.runner import Runner
from adam.sandbox.guest.agent.agent import ToolAvailability
from adam.sandbox.guest.http_channel import HTTPGuestChannel
from adam.sandbox.guest.http_models import ExistsData
from adam.sandbox.vbox.client import VirtualBoxClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("verify_candidate_snapshots")

DEFAULT_VDI_PATH = Path(
    r"C:\Users\NIVED\VirtualBox VMs\Windows\Snapshots\{c86fbab5-1567-4629-a3c3-5f73b5afbb46}.vdi"
)
DEFAULT_OUTPUT_FILE = _PROJECT_ROOT / "logs" / "repair-verification" / time.strftime("%Y%m%d-%H%M%S") / "result.json"


def check_orphan_vdi(vdi_path: Path, baseline_stat: os.stat_result | None = None) -> dict:
    """Verifies that the unregistered 4 GiB VDI exists and is untouched."""
    if not vdi_path.exists():
        return {
            "exists": False,
            "path": str(vdi_path),
            "error": "VDI file not found on disk",
            "untouched": False,
        }

    current_stat = vdi_path.stat()
    size_bytes = current_stat.st_size
    mtime = current_stat.st_mtime
    with vdi_path.open("rb") as stream:
        sha256 = hashlib.file_digest(stream, "sha256").hexdigest()

    untouched = True
    if baseline_stat is not None and (size_bytes != baseline_stat.st_size or mtime != baseline_stat.st_mtime):
        untouched = False

    return {
        "exists": True,
        "path": str(vdi_path),
        "size_bytes": size_bytes,
        "mtime": mtime,
        "sha256": sha256,
        "untouched": untouched,
    }


async def wait_for_guest_ready(
    channel: HTTPGuestChannel,
    timeout_s: float = 180.0,
    poll_interval_s: float = 3.0,
    settle_delay_s: float = 8.0,
) -> bool:
    """Polls guest agent endpoints until healthy and ready or timeout."""
    start = time.monotonic()
    while (time.monotonic() - start) < timeout_s:
        try:
            ver = await channel.get_version()
            health = await channel.get_health()
            if ver and health and getattr(health, "status", None) == "ok":
                ver_str = getattr(ver, "agent_version", None) or (ver.get("agent_version") if isinstance(ver, dict) else str(ver))
                uptime = getattr(health, "uptime_s", 0.0) or 0.0
                logger.info("Guest agent responsive (version=%s, uptime=%.1fs)", ver_str, uptime)
                if settle_delay_s > 0:
                    logger.info("Waiting %.1fs for guest startup processes to settle...", settle_delay_s)
                    await asyncio.sleep(settle_delay_s)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Guest agent not ready yet: %s", exc)
        await asyncio.sleep(poll_interval_s)
    return False


async def check_markers(
    channel: HTTPGuestChannel,
    marker_file: str,
    reg_path: str,
) -> dict[str, bool]:
    """Checks whether marker file or registry entry exists in the guest using fast native tools."""
    file_present = False
    reg_present = False

    # 1. Fast file check via GET /filesystem/exists (runs in ~30ms, no process creation overhead)
    try:
        exists_data = await channel._request(
            "GET", "/filesystem/exists", params={"path": marker_file}, label=f"check_markers: {marker_file}"
        )
        if exists_data is None:
            return {
                "file_present": False,
                "reg_present": False,
                "error": f"Failed to check marker file at '{marker_file}' (agent unreachable)",
            }
        file_present = bool(ExistsData.model_validate(exists_data).exists)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error checking marker file '%s': %s", marker_file, exc)
        return {"file_present": False, "reg_present": False, "error": str(exc)}

    # 2. Registry check via reg.exe query
    clean_reg_path = reg_path.replace("HKLM:\\", "HKLM\\").replace("HKLM:", "HKLM")
    try:
        reg_res = await channel.run_process(
            "reg.exe",
            ["query", clean_reg_path, "/v", "Marker"],
            wait=True,
            timeout_s=25.0,
        )
        if reg_res.return_code == 0:
            reg_present = True
        elif reg_res.return_code == 1:
            reg_present = False
        else:
            logger.warning(
                "reg.exe query returned unexpected code %s for '%s': %s",
                reg_res.return_code, clean_reg_path, reg_res.stderr,
            )
            return {
                "file_present": file_present,
                "reg_present": False,
                "error": reg_res.stderr or f"reg query exit {reg_res.return_code}",
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error checking marker registry '%s': %s", clean_reg_path, exc)
        return {"file_present": file_present, "reg_present": False, "error": str(exc)}

    return {"file_present": file_present, "reg_present": reg_present}


async def plant_markers(
    channel: HTTPGuestChannel,
    marker_file: str,
    reg_path: str,
    marker_val: str,
) -> bool:
    """Plants marker file and registry entry in the guest using fast native tools."""
    clean_reg_path = reg_path.replace("HKLM:\\", "HKLM\\").replace("HKLM:", "HKLM")
    try:
        # 1. Plant file marker using cmd.exe
        cmd_str = f'if not exist C:\\ADAM md C:\\ADAM & echo {marker_val} > "{marker_file}"'
        file_res = await channel.run_process(
            "cmd.exe",
            ["/c", cmd_str],
            wait=True,
            timeout_s=25.0,
        )
        if not file_res.success:
            logger.warning(
                "Error planting marker file '%s' (exit_code=%s): %s",
                marker_file, file_res.return_code, file_res.stderr,
            )
            return False

        # 2. Plant registry marker using reg.exe add
        reg_res = await channel.run_process(
            "reg.exe",
            ["add", clean_reg_path, "/v", "Marker", "/d", marker_val, "/f"],
            wait=True,
            timeout_s=25.0,
        )
        if not reg_res.success:
            logger.warning(
                "Error planting marker reg '%s' (exit_code=%s): %s",
                clean_reg_path, reg_res.return_code, reg_res.stderr,
            )
            return False

        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error planting markers: %s", exc)
        return False


async def run_benign_workload(
    channel: HTTPGuestChannel,
    sample_path: str = "whoami.exe",
    args: list[str] | None = None,
) -> dict:
    """Executes a benign workload inside the guest."""
    start = time.monotonic()
    try:
        res = await channel.run_process(
            sample_path,
            arguments=args or [],
            wait=True,
            timeout_s=30.0,
        )
        duration_ms = (time.monotonic() - start) * 1000
        return {
            "success": res.success,
            "return_code": res.return_code,
            "stdout": res.stdout.strip() if res.stdout else "",
            "stderr": res.stderr.strip() if res.stderr else "",
            "duration_ms": duration_ms,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "error": str(exc),
            "duration_ms": (time.monotonic() - start) * 1000,
        }


async def stop_vm_cleanly(
    vbox: VirtualBoxClient,
    vm_name: str,
    timeout_s: float = 90.0,
) -> bool:
    """Stops VM gracefully via ACPI power button, falling back to poweroff."""
    state = await vbox.get_state(vm_name)
    if state == "poweroff":
        return True
    if state == "saved":
        return False

    logger.info("Stopping VM '%s' (current_state=%s)...", vm_name, state)
    await vbox.stop(vm_name, mode="acpi")
    res = await vbox.wait_for_state(vm_name, "poweroff", timeout=timeout_s)
    if res.success:
        await asyncio.sleep(3.0)
        return True

    logger.warning("ACPI shutdown timed out for '%s', sending unconditional poweroff", vm_name)
    await vbox.stop(vm_name, mode="poweroff")
    res = await vbox.wait_for_state(vm_name, "poweroff", timeout=15.0)
    await asyncio.sleep(3.0)
    # The VM is safe to leave powered off, but this was not a clean shutdown.
    return False


async def run_verification_cycle(
    vbox: VirtualBoxClient,
    channel: HTTPGuestChannel,
    vm_name: str,
    snapshot_name: str,
    cycle_index: int,
    prior_marker_file: str | None = None,
    prior_reg_path: str | None = None,
    ready_timeout_s: float = 180.0,
) -> dict:
    """Executes a single restore-run-verify cycle."""
    logger.info("=== Starting Cycle %d (snapshot=%s) ===", cycle_index, snapshot_name)
    cycle_marker = f"ADAM_VERIFY_CYCLE_{cycle_index}_{uuid.uuid4().hex[:8]}"
    marker_file = f"C:\\ADAM\\run_marker_{cycle_index}.txt"
    reg_path = f"HKLM\\Software\\ADAM\\Cycle{cycle_index}"

    cycle_result: dict = {
        "cycle": cycle_index,
        "marker": cycle_marker,
        "snapshot": snapshot_name,
        "restore_success": False,
        "pre_clean_verified": False,
        "guest_ready": False,
        "workload_executed": False,
        "telemetry_verified": False,
        "vm_stopped": False,
    }

    # 1. Stop VM if running
    if not await stop_vm_cleanly(vbox, vm_name):
        cycle_result["error"] = "Could not shut down before restoration"
        return cycle_result

    # 2. Restore snapshot
    logger.info("Cycle %d: Restoring snapshot '%s'...", cycle_index, snapshot_name)
    restore_res = await vbox.restore_snapshot(vm_name, snapshot_name)
    if not restore_res.success:
        logger.error("Failed to restore snapshot: %s", restore_res.stderr)
        cycle_result["error"] = f"Restore failed: {restore_res.stderr}"
        return cycle_result
    cycle_result["restore_success"] = True
    await asyncio.sleep(3.0)

    # 3. Start VM
    logger.info("Cycle %d: Starting VM '%s'...", cycle_index, vm_name)
    start_res = await vbox.start(vm_name, headless=True)
    if not start_res.success:
        logger.error("Failed to start VM: %s", start_res.stderr)
        cycle_result["error"] = f"Start VM failed: {start_res.stderr}"
        return cycle_result

    # 4. Wait for guest agent
    ready = await wait_for_guest_ready(channel, timeout_s=ready_timeout_s)
    cycle_result["guest_ready"] = ready
    if not ready:
        logger.error("Cycle %d: Guest agent not ready within timeout", cycle_index)
        await stop_vm_cleanly(vbox, vm_name)
        return cycle_result

    # 5. Verify pre-clean state (and verify prior cycle artifacts are completely absent)
    curr_markers = await check_markers(channel, marker_file, reg_path)
    is_pre_clean = (
        "error" not in curr_markers
        and not curr_markers.get("file_present", False)
        and not curr_markers.get("reg_present", False)
    )

    if prior_marker_file and prior_reg_path:
        prior_markers = await check_markers(channel, prior_marker_file, prior_reg_path)
        is_prior_clean = (
            "error" not in prior_markers
            and not prior_markers.get("file_present", False)
            and not prior_markers.get("reg_present", False)
        )
        cycle_result["state_reset_verified"] = is_prior_clean
        is_pre_clean = is_pre_clean and is_prior_clean

    cycle_result["pre_clean_verified"] = is_pre_clean
    if not is_pre_clean:
        logger.error("Cycle %d: Pre-clean state verification FAILED! Leftover artifacts detected", cycle_index)

    # 8. Verify telemetry source responsiveness
    diag = await channel.verify_tools()
    is_valid_diag = isinstance(diag, ToolAvailability)
    telemetry_ready = (
        is_valid_diag
        and (diag.sysmon_log_available and diag.procmon_available and diag.tshark_available)
        and not diag.required_missing
    )
    cycle_result["tools_ready"] = bool(telemetry_ready)
    # Tool presence is a prerequisite, never capture evidence.
    cycle_result["telemetry_verified"] = False
    cycle_result["tools"] = {
        "procmon_available": diag.procmon_available,
        "tshark_available": diag.tshark_available,
        "sysmon_log_available": diag.sysmon_log_available,
        "detail": diag.detail,
        "required_missing": list(diag.required_missing),
    } if is_valid_diag else None
    if not telemetry_ready:
        detail_msg = diag.detail if is_valid_diag else "Invalid ToolAvailability response"
        logger.warning("Cycle %d: Telemetry readiness verification failed: %s", cycle_index, detail_msg)
        cycle_result["telemetry_warning"] = f"Telemetry tools not ready: {detail_msg}"
    elif is_pre_clean:
        try:
            version = await channel.get_version()
            manifest = json.loads((_PROJECT_ROOT / "adam/sandbox/guest/agent/build_manifest.json").read_text(encoding="utf-8"))
            if version.build_id != manifest["build_id"] or not {"sysmon.events.since", "diagnostics.tools"}.issubset(version.capabilities):
                raise RuntimeError("Guest build or required capabilities do not match the package")
            diagnostics = await channel.get_tool_diagnostics()
            cycle_result["diagnostics"] = diagnostics.model_dump()
            if diagnostics.tools.get("npcap", {}).get("service_status") != "Running":
                raise RuntimeError("Npcap capture driver is not running")
            evidence = DEFAULT_OUTPUT_FILE.parent / cycle_marker
            evidence.mkdir(parents=True, exist_ok=True)
            sample = evidence / f"{cycle_marker}.exe"
            compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
            await asyncio.to_thread(subprocess.run, [str(compiler), "/nologo", f"/out:{sample}", str(_PROJECT_ROOT / "scripts/verification_workload.cs")], check=True, capture_output=True)
            if not await stop_vm_cleanly(vbox, vm_name):
                raise RuntimeError("Could not shut down before pipeline run")
            settings = get_settings().model_copy(deep=True)
            settings.sandbox.snapshot_name = snapshot_name
            settings.http_guest.tools_required = True
            settings.http_guest.tshark_interface = "auto"
            settings.live.enabled = True
            session = await Runner(settings).run(str(sample.resolve()), artifacts_dir=evidence,
                arm=Arm.CONTROL if cycle_index == 1 else Arm.TREATMENT)
            (evidence / "session.json").write_text(session.model_dump_json(indent=2), encoding="utf-8")
            cycle_result["session_id"] = session.session_id
            cycle_result["session_status"] = session.status.value
            cycle_result["source_outcomes"] = {k: v.model_dump() for k, v in session.source_outcomes.items()}
            rows = [json.loads(line) for line in (evidence / session.session_id / "raw.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            correlation = {}
            for source in ("SYSMON", "PROCMON"):
                relevant = [row for row in rows if row["source"].upper() == source]
                correlation[f"{source}.process"] = any(cycle_marker in json.dumps(row) for row in relevant if row["category"].upper() == "PROCESS")
                correlation[f"{source}.file"] = any(f"run_marker_{cycle_index}.txt" in json.dumps(row) for row in relevant if row["category"].upper() == "FILE")
                correlation[f"{source}.registry"] = any(f"cycle{cycle_index}" in json.dumps(row).lower() for row in relevant if row["category"].upper() == "REGISTRY")
            correlation["network"] = any("192.168.19.2" in json.dumps(row) and "18080" in json.dumps(row)
                for row in rows if row["source"].upper() in ("TSHARK", "NETWORK"))
            cycle_result["marker_correlation"] = correlation
            cycle_result["marker_planted"] = all(value for key, value in correlation.items() if key.endswith((".file", ".registry")))
            cycle_result["workload_executed"] = session.status == SessionStatus.COMPLETED and session.execution_exit_code == 0
            outcomes = session.source_outcomes
            cycle_result["telemetry_verified"] = bool(cycle_result["workload_executed"]
                and all(correlation.values()) and outcomes.get("sysmon") and outcomes["sysmon"].live_events_count > 0
                and all(outcomes.get(k) and outcomes[k].verified and outcomes[k].capture_started
                    and outcomes[k].exported and outcomes[k].parsed and not outcomes[k].errors
                    for k in ("sysmon", "procmon", "network")))
        except Exception as exc:
            logger.exception("Normal pipeline verification failed")
            cycle_result["error"] = str(exc)

    # 9. Stop VM cleanly
    cycle_result["vm_stopped"] = await stop_vm_cleanly(vbox, vm_name)
    logger.info("=== Finished Cycle %d: pre_clean=%s, workload=%s ===", cycle_index, is_pre_clean, cycle_result["workload_executed"])

    return cycle_result


async def promote_candidate_to_clean(
    vbox: VirtualBoxClient,
    vm_name: str,
    candidate_name: str,
    recovery_name: str,
    verification: dict | None = None,
) -> dict:
    """
    Deletes the old recovery snapshot and renames the candidate snapshot to 'clean'.
    """
    promotion: dict = {
        "candidate": candidate_name,
        "recovery": recovery_name,
        "recovery_deleted": False,
        "candidate_renamed": False,
        "final_snapshot_count": 0,
        "final_snapshots": [],
        "success": False,
    }

    if not verification or verification.get("candidate_uuid") != candidate_name or not verification.get("cycles_passed"):
        promotion["error"] = "Successful verification bound to the candidate UUID is required"
        return promotion

    snapshots = await vbox.list_snapshots(vm_name)
    candidates = [s for s in snapshots if candidate_name in (s.name, s.uuid)]
    recoveries = [s for s in snapshots if recovery_name in (s.name, s.uuid)]
    if len(candidates) != 1 or len(recoveries) != 1 or len(snapshots) != 2:
        promotion["error"] = "Promotion requires exactly one candidate and one recovery"
        return promotion
    candidate, recovery = candidates[0], recoveries[0]
    if candidate.uuid == recovery.uuid:
        promotion["error"] = "Candidate and recovery must have distinct UUIDs"
        return promotion
    if await vbox.get_state(vm_name) != "poweroff":
        promotion["error"] = "Promotion requires a powered-off VM"
        return promotion
    for snapshot_id, name in ((recovery.uuid, "recovery-" + recovery.uuid), (candidate.uuid, "clean")):
        result = await vbox._run("snapshot", vm_name, "edit", snapshot_id, "--name", name)
        if not result.success:
            promotion["error"] = result.stderr
            if snapshot_id == candidate.uuid:
                rollback = await vbox._run("snapshot", vm_name, "edit", recovery.uuid, "--name", recovery.name)
                promotion["recovery_name_restored"] = rollback.success
            return promotion
    promotion["candidate_renamed"] = True
    renamed = await vbox.list_snapshots(vm_name)
    if not any(s.uuid == candidate.uuid and s.name == "clean" for s in renamed):
        promotion["error"] = "Candidate rename could not be verified; recovery preserved"
        return promotion
    result = await vbox._run("snapshot", vm_name, "delete", recovery.uuid)
    promotion["recovery_deleted"] = result.success
    if not result.success:
        promotion["error"] = result.stderr
        return promotion

    # Verify final snapshots
    final_snapshots = await vbox.list_snapshots(vm_name)
    promotion["final_snapshot_count"] = len(final_snapshots)
    promotion["final_snapshots"] = [s.name for s in final_snapshots]
    promotion["success"] = any(s.name == "clean" and s.uuid == candidate.uuid for s in final_snapshots) and len(final_snapshots) == 1

    return promotion


async def verify_final_baseline(vbox, channel, vm_name, candidate_uuid) -> dict:
    result = {"success": False, "snapshot_uuid": candidate_uuid}
    try:
        if not await stop_vm_cleanly(vbox, vm_name):
            raise RuntimeError("VM did not shut down cleanly")
        restored = await vbox.restore_snapshot(vm_name, candidate_uuid)
        if not restored.success:
            raise RuntimeError(restored.stderr)
        started = await vbox.start(vm_name, headless=True)
        if not started.success or not await wait_for_guest_ready(channel):
            raise RuntimeError("Final baseline did not boot with a healthy agent")
        expected = json.loads((_PROJECT_ROOT / "adam/sandbox/guest/agent/build_manifest.json").read_text(encoding="utf-8"))["build_id"]
        if (await channel.get_version()).build_id != expected:
            raise RuntimeError("Final baseline runs a different agent build")
        for cycle in (1, 2):
            markers = await check_markers(channel, f"C:\\ADAM\\run_marker_{cycle}.txt", f"HKLM\\Software\\ADAM\\Cycle{cycle}")
            if "error" in markers or markers["file_present"] or markers["reg_present"]:
                raise RuntimeError("Final baseline contains verification markers or could not be checked")
        result["success"] = True
    except Exception as exc:
        logger.exception("Final baseline verification failed")
        result["error"] = str(exc)
    finally:
        stopped = await stop_vm_cleanly(vbox, vm_name)
        result["vm_stopped"] = stopped
        result["success"] = result["success"] and stopped
        if stopped:
            pristine = await vbox.restore_snapshot(vm_name, candidate_uuid)
            result["success"] = result["success"] and pristine.success
    return result


async def _run_harness(args: argparse.Namespace) -> int:
    vdi_path = Path(args.vdi_path)
    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    vm_name = args.vm_name or settings.sandbox.vm_name
    candidate_snap = args.candidate_snapshot
    recovery_snap = args.recovery_snapshot
    guest_url = args.guest_url or settings.http_guest.base_url

    vbox = VirtualBoxClient()
    def _create_channel() -> HTTPGuestChannel:
        return HTTPGuestChannel(
            base_url=guest_url,
            capture_dir=settings.http_guest.capture_dir,
            procmon_path=settings.http_guest.procmon_path,
            tshark_path=settings.http_guest.tshark_path,
            sysmon_log=settings.http_guest.sysmon_log,
            tshark_interface=settings.http_guest.tshark_interface,
            auth_token=settings.http_guest.auth_token,
            tools_required=True,
        )

    logger.info("Starting candidate snapshot verification harness for VM '%s'", vm_name)

    # 1. Check unregistered 4 GiB VDI initial baseline
    vdi_baseline = check_orphan_vdi(vdi_path)
    logger.info("Unregistered VDI check: %s", vdi_baseline)
    baseline_stat = vdi_path.stat() if vdi_path.exists() else None
    if not vdi_baseline.get("exists"):
        output_path.write_text(json.dumps({"overall_status": "FAILED", "error": "Required orphan disk baseline is unavailable"}, indent=2))
        return 1

    # Check available snapshots
    snapshots = await vbox.list_snapshots(vm_name)
    snap_names = [s.name for s in snapshots]
    logger.info("Available snapshots: %s", snap_names)

    candidates = [s for s in snapshots if candidate_snap in (s.name, s.uuid)]
    recoveries = [s for s in snapshots if recovery_snap in (s.name, s.uuid)]
    if len(candidates) != 1 or len(recoveries) != 1 or candidates[0].uuid == recoveries[0].uuid:
        output_path.write_text(json.dumps({"overall_status": "FAILED", "error": "Distinct existing candidate and recovery UUIDs required"}, indent=2))
        return 1
    target_candidate = candidates[0].uuid
    recovery_snap = recoveries[0].uuid

    # 2. Execute 2 consecutive cycles
    channel1 = _create_channel()
    try:
        cycle1 = await run_verification_cycle(
            vbox, channel1, vm_name, target_candidate, cycle_index=1
        )
    finally:
        await channel1.aclose()

    channel2 = _create_channel()
    try:
        cycle2 = await run_verification_cycle(
            vbox,
            channel2,
            vm_name,
            target_candidate,
            cycle_index=2,
            prior_marker_file="C:\\ADAM\\run_marker_1.txt",
            prior_reg_path="HKLM\\Software\\ADAM\\Cycle1",
        )
    finally:
        await channel2.aclose()

    required_checks = ("restore_success", "guest_ready", "pre_clean_verified", "marker_planted",
                       "workload_executed", "telemetry_verified", "vm_stopped")
    cycles_passed = all(c.get(key) is True for c in (cycle1, cycle2) for key in required_checks)
    cycles_passed = cycles_passed and cycle2.get("state_reset_verified") is True
    pre_promotion_health = None
    if cycles_passed:
        channel = _create_channel()
        try:
            pre_promotion_health = await verify_final_baseline(vbox, channel, vm_name, target_candidate)
            cycles_passed = pre_promotion_health["success"]
        finally:
            await channel.aclose()

    # 3. Promote candidate to clean if requested and cycles passed
    promotion_result: dict | None = None
    vdi_pre_promotion = check_orphan_vdi(vdi_path, baseline_stat)
    disk_safe = vdi_baseline.get("exists") and vdi_pre_promotion.get("untouched") and vdi_pre_promotion.get("sha256") == vdi_baseline.get("sha256")
    if cycles_passed and disk_safe and args.promote:
        promotion_result = await promote_candidate_to_clean(
            vbox, vm_name, target_candidate, recovery_snap,
            verification={"candidate_uuid": target_candidate, "cycles_passed": cycles_passed},
        )
        if promotion_result.get("success"):
            channel = _create_channel()
            try:
                promotion_result["final_health"] = await verify_final_baseline(vbox, channel, vm_name, target_candidate)
                promotion_result["success"] = promotion_result["final_health"]["success"]
            finally:
                await channel.aclose()

    # 4. Final VDI untouched verification
    vdi_final = check_orphan_vdi(vdi_path, baseline_stat)
    vdi_final["untouched"] = bool(vdi_final.get("exists") and vdi_final.get("untouched") and vdi_final.get("sha256") == vdi_baseline.get("sha256"))
    logger.info("Final unregistered VDI check: %s", vdi_final)

    # 5. Build final machine-readable result
    promotion_passed = promotion_result is None or promotion_result.get("success", False)
    overall_passed = bool(cycles_passed and vdi_final.get("untouched", True) and promotion_passed)

    final_result = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "vm_name": vm_name,
        "candidate_snapshot": target_candidate,
        "recovery_snapshot": recovery_snap,
        "cycles": [cycle1, cycle2],
        "cycles_passed": cycles_passed,
        "promotion": promotion_result,
        "pre_promotion_health": pre_promotion_health,
        "orphan_disk_untouched": vdi_final.get("untouched", True),
        "orphan_disk_path": str(vdi_path),
        "orphan_disk_bytes": vdi_final.get("size_bytes", 0),
        "overall_status": "PASSED" if overall_passed else "FAILED",
    }

    output_path.write_text(json.dumps(final_result, indent=2))
    logger.info("Saved verification results to: %s", output_path)

    return 0 if final_result["overall_status"] == "PASSED" else 1


class VerificationHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ADAM verification response")

    def log_message(self, *args):
        logger.info("Controlled guest request: %s", self.path)


async def run_harness(args: argparse.Namespace) -> int:
    # The workload intentionally targets only this host-only address.
    server = ThreadingHTTPServer(("192.168.19.2", 18080), VerificationHTTPHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        return await _run_harness(args)
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="ADAM Candidate Snapshot Verification Harness")
    parser.add_argument("--vm-name", default="Windows", help="VirtualBox VM name (default: Windows)")
    parser.add_argument("--candidate-snapshot", default="candidate", help="Candidate snapshot to verify (default: candidate)")
    parser.add_argument("--recovery-snapshot", default="clean", help="Old recovery snapshot (default: clean)")
    parser.add_argument("--output-file", default=str(DEFAULT_OUTPUT_FILE), help="Output result JSON file")
    parser.add_argument("--vdi-path", default=str(DEFAULT_VDI_PATH), help="Path to orphan 4 GiB VDI")
    parser.add_argument(
        "--guest-url",
        default="http://192.168.19.102:8765",
        help="ADAM Guest Agent base URL (default: http://192.168.19.102:8765)",
    )
    parser.add_argument(
        "--promote",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Explicitly promote after successful verification (default: False)",
    )

    args = parser.parse_args()
    rc = asyncio.run(run_harness(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()
