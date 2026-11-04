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
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from adam.common.config import get_settings
from adam.common.power import keep_system_awake
from adam.contracts.enums import Arm, SessionStatus, Source
from adam.orchestrator.persistence import RawEventWriter
from adam.orchestrator.runner import Runner
from adam.sandbox.guest.http_channel import HTTPGuestChannel
from adam.sandbox.shutdown import shutdown_normally
from adam.sandbox.vbox.client import VirtualBoxClient
from scripts.vm_acceptance import (
    RESET_FILE,
    RESET_KEY,
    inspect_markers,
    markers_absent,
    markers_match,
    powershell,
    validate_acceptance,
    write_markers,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("verify_candidate_snapshots")

DEFAULT_VDI_PATH = Path(
    r"C:\Users\NIVED\VirtualBox VMs\Windows\Snapshots\{c86fbab5-1567-4629-a3c3-5f73b5afbb46}.vdi"
)
DEFAULT_OUTPUT_FILE = _PROJECT_ROOT / "logs" / "repair-verification" / time.strftime("%Y%m%d-%H%M%S") / "result.json"
CONTROLLED_REQUESTS: list[dict] = []


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
    timeout_s: float = 300.0,
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


async def check_markers(channel, marker_file: str, reg_path: str) -> dict:
    return await inspect_markers(channel, marker_file, reg_path)


async def plant_markers(channel, marker_file: str, reg_path: str, marker_val: str) -> bool:
    return await write_markers(channel, marker_file, reg_path, marker_val)


async def prepare_reset_witness(vbox, channel, vm_name: str, candidate_uuid: str) -> dict:
    """Plant and read back a witness AFTER Runner has finished its restoration."""
    witness = {"success": False, "candidate_uuid": candidate_uuid, "token": uuid.uuid4().hex,
               "file": RESET_FILE, "registry": RESET_KEY}
    try:
        if await vbox.get_state(vm_name) != "poweroff":
            raise RuntimeError("Reset witness requires completed, powered-off Runner teardown")
        started = await vbox.start(vm_name, headless=True)
        if not started.success or not await wait_for_guest_ready(channel):
            raise RuntimeError("Could not boot current state for reset witness")
        if not markers_absent(await check_markers(channel, RESET_FILE, RESET_KEY)):
            raise RuntimeError("Reset witness was already present or unreadable")
        if not await plant_markers(channel, RESET_FILE, RESET_KEY, witness["token"]):
            raise RuntimeError("Reset witness could not be planted and read back")
        witness["observed"] = await check_markers(channel, RESET_FILE, RESET_KEY)
        witness["success"] = markers_match(witness["observed"], witness["token"])
    except Exception as exc:
        logger.exception("Restore witness preparation failed")
        witness["error"] = str(exc)
    finally:
        witness["vm_stopped"] = await stop_vm_cleanly(vbox, vm_name)
        witness["success"] = witness["success"] and witness["vm_stopped"]
        if witness["success"]:
            witness["disk"] = await current_disk_fingerprint(vbox, vm_name)
    return witness


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
    """Require normal shutdown; leave a stalled guest intact for diagnosis."""
    state = await vbox.get_state(vm_name)
    if state == "poweroff":
        return True
    if state == "saved":
        return False

    logger.info("Stopping VM '%s' (current_state=%s)...", vm_name, state)
    settings = get_settings().http_guest
    channel = HTTPGuestChannel(base_url=settings.base_url, auth_token=settings.auth_token,
        capture_dir=settings.capture_dir, procmon_path=settings.procmon_path,
        tshark_path=settings.tshark_path, sysmon_log=settings.sysmon_log)
    try:
        stopped = await shutdown_normally(vbox, vm_name, channel=channel, timeout=timeout_s)
    finally:
        await channel.aclose()
    if stopped:
        await asyncio.sleep(3.0)
        return True

    logger.error("ACPI shutdown timed out for '%s'; preserving current state", vm_name)
    return False


async def current_disk_fingerprint(vbox, vm_name: str) -> dict:
    info = await vbox._run("showvminfo", vm_name, "--machinereadable")
    if not info.success:
        raise RuntimeError(info.stderr)
    fields = {}
    for line in info.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip('"')] = value.strip('"')
    disk = fields.get("SATA-ImageUUID-0-0")
    snapshot = fields.get("CurrentSnapshotUUID")
    if not disk or not snapshot:
        raise RuntimeError("Current disk/snapshot UUID is unavailable")
    return {"disk_uuid": disk, "snapshot_uuid": snapshot}


async def run_verification_cycle(
    vbox, channel, vm_name: str, snapshot_name: str, cycle_index: int,
    prior_marker_file=None, prior_reg_path=None, ready_timeout_s=300,
    reset_witness=None, evidence_root=None,
) -> dict:
    """Verify the actual Runner restore/boot, without a redundant preflight boot."""
    marker = f"ADAM_VERIFY_CYCLE_{cycle_index}_{uuid.uuid4().hex[:8]}"
    result = {"cycle": cycle_index, "marker": marker, "snapshot": snapshot_name,
              **{key: False for key in ("restore_success", "pre_clean_verified", "guest_ready",
                 "workload_executed", "telemetry_verified", "vm_stopped", "marker_planted")},
              "restore_operations": []}
    if not await stop_vm_cleanly(vbox, vm_name):
        result["error"] = "Could not shut down before restoration"
        return result
    if cycle_index == 2:
        try:
            if not reset_witness or not reset_witness.get("success") or reset_witness.get("candidate_uuid") != snapshot_name:
                raise RuntimeError("A successful candidate-bound reset witness is required")
            fingerprint = await current_disk_fingerprint(vbox, vm_name)
            result["reset_before_restore"] = {"observation": reset_witness["observed"], "disk": fingerprint}
            if fingerprint != reset_witness.get("disk") or not markers_match(reset_witness["observed"], reset_witness["token"]):
                raise RuntimeError("An intermediate restore or changed reset witness invalidates the proof")
        except Exception as exc:
            logger.exception("Restore witness continuity check failed")
            result["error"] = str(exc)
            return result
    evidence = Path(evidence_root or DEFAULT_OUTPUT_FILE.parent) / marker
    evidence.mkdir(parents=True, exist_ok=True)
    sample = evidence / (marker + ".exe")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    settings = get_settings().model_copy(deep=True)
    settings.sandbox.snapshot_name = snapshot_name
    settings.sandbox.guest_ready_timeout_s = max(ready_timeout_s, settings.sandbox.guest_ready_timeout_s)
    settings.http_guest.tools_required = True
    settings.http_guest.tshark_interface = "auto"
    settings.network.host = settings.network.host_ip = "192.168.19.2"
    settings.live.enabled = True
    active_ref = {}
    result["live_workload_observed"] = False

    async def observe_live_persistence(events):
        if result.get("live_marker_checked"):
            return
        for event in events:
            if (event.source == Source.SYSMON and str(event.raw_ref).startswith("guest:sysmon:")
                    and event.process and marker.lower() in event.process.image.lower()):
                result["live_marker_checked"] = True
                proof = {"record_id": event.source_event_id, "pid": event.process.pid,
                         "image": event.process.image, "occurred_at": event.occurred_at.isoformat()}
                try:
                    status = await active_ref["channel"].wait_process(event.process.pid)
                    proof.update(status=status, checked_at=datetime.now(UTC).isoformat())
                    result["live_workload_observed"] = status.get("exited") is False
                except Exception as exc:
                    logger.exception("Live workload persistence observation failed")
                    proof["error"] = str(exc)
                (evidence / "live-workload-observation.json").write_text(json.dumps(proof, indent=2))
                return

    class ObservedWriter(RawEventWriter):
        # Read-only observation of real persistence; no synthetic telemetry or
        # altered writes. Exported events cannot satisfy the live-source gate.
        async def write(self, event):
            await super().write(event)
            await observe_live_persistence([event])

        async def write_batch(self, events):
            await super().write_batch(events)
            await observe_live_persistence(events)

    class VerificationRunner(Runner):
        def _build_guest_channel(self, client, config, **kwargs):
            runtime = super()._build_guest_channel(client, config, **kwargs)
            active = runtime.channel
            active_ref["channel"] = active
            original_restore = client.restore_snapshot
            original_verify = active.verify_tools
            original_export = active.stop_export_and_fetch
            inspected = False

            async def restore(vm, snapshot):
                if snapshot != snapshot_name:
                    raise RuntimeError("Runner requested an unexpected snapshot")
                operation = await original_restore(vm, snapshot)
                result["restore_operations"].append({"snapshot_uuid": snapshot, "success": operation.success})
                result["restore_success"] = result["restore_success"] or operation.success
                return operation

            async def verify():
                nonlocal inspected
                availability = await original_verify()
                if inspected:
                    return availability
                if not result["restore_success"]:
                    raise RuntimeError("Guest checks were reached without a successful Runner restore")
                result["guest_ready"] = (await active.get_health()).status == "ok"
                version = await active.get_version()
                expected = json.loads((_PROJECT_ROOT / "adam/sandbox/guest/agent/build_manifest.json").read_text(encoding="utf-8"))["build_id"]
                if version.build_id != expected:
                    raise RuntimeError("Running guest build does not match the package")
                result["guest_build"] = version.build_id
                result["diagnostics"] = (await active.get_tool_diagnostics()).model_dump()
                result["tools_ready"] = bool(availability.procmon_available and availability.tshark_available
                    and availability.sysmon_log_available and not availability.required_missing)
                if not result["tools_ready"] or result["diagnostics"]["tools"].get("npcap", {}).get("service_status") != "Running":
                    raise RuntimeError("Mandatory guest tools are not ready")
                for cycle in (1, 2):
                    observation = await check_markers(active, f"C:\\ADAM\\run_marker_{cycle}.txt", f"HKLM\\Software\\ADAM\\Cycle{cycle}")
                    if not markers_absent(observation):
                        raise RuntimeError("Workload markers are present or unreadable after Runner restore")
                witness_after = await check_markers(active, RESET_FILE, RESET_KEY)
                result["pre_clean_verified"] = markers_absent(witness_after)
                if not result["pre_clean_verified"]:
                    raise RuntimeError("Reset witness is present or unreadable after Runner restore")
                if cycle_index == 2:
                    result["reset_after_restore"] = witness_after
                    result["state_reset_verified"] = True
                inspected = True
                return availability

            client.restore_snapshot = restore
            active.verify_tools = verify
            async def export(*args, **export_kwargs):
                try:
                    response = await powershell(active, f"[IO.File]::ReadAllText('C:\\ADAM\\{marker}_c2_response.txt')")
                    result["c2_response"] = response.strip()
                    expected_body = "ADAM verification response" if cycle_index == 1 else "fake_c2_payload"
                    result["c2_effect_verified"] = result["c2_response"] == expected_body
                    (evidence / "c2-response.txt").write_text(response, encoding="utf-8")
                except Exception as exc:
                    logger.exception("Controlled C2 response verification failed")
                    result["c2_probe_error"] = str(exc)
                    result["c2_effect_verified"] = False
                return await original_export(*args, **export_kwargs)
            active.stop_export_and_fetch = export
            return runtime

    try:
        await asyncio.to_thread(subprocess.run, [str(compiler), "/nologo", f"/out:{sample}", str(_PROJECT_ROOT / "scripts/verification_workload.cs")], check=True, capture_output=True)
        with patch("adam.orchestrator.session.RawEventWriter", ObservedWriter):
            session = await VerificationRunner(settings).run(str(sample.resolve()), artifacts_dir=evidence,
                arm=Arm.CONTROL if cycle_index == 1 else Arm.TREATMENT)
        (evidence / "session.json").write_text(session.model_dump_json(indent=2), encoding="utf-8")
        result.update(session_id=session.session_id, session_status=session.status.value,
                      source_outcomes={key: value.model_dump() for key, value in session.source_outcomes.items()})
        correlation = {f"{source}.{kind}": False for source in ("SYSMON", "PROCMON") for kind in ("process", "file", "registry")}
        correlation["network"] = False
        with (evidence / session.session_id / "raw.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                source, category = row["source"].upper(), row["category"].lower()
                text = json.dumps(row)
                if source in ("SYSMON", "PROCMON"):
                    if category == "process" and marker in text:
                        correlation[f"{source}.process"] = True
                    if category == "file" and f"run_marker_{cycle_index}.txt" in text:
                        correlation[f"{source}.file"] = True
                    if category == "registry" and f"cycle{cycle_index}" in text.lower():
                        correlation[f"{source}.registry"] = True
                if source in ("NETWORK", "WIRESHARK", "TSHARK") and "192.168.19.2" in text and "18080" in text:
                    correlation["network"] = True
        requests = [request for request in CONTROLLED_REQUESTS if request["path"] == "/" + marker]
        (evidence / "controlled-requests.json").write_text(json.dumps(requests, indent=2), encoding="utf-8")
        token_in_packets = False
        for packet_file in evidence.rglob("network.ek.json"):
            with packet_file.open(encoding="utf-8") as stream:
                token_in_packets = token_in_packets or any(marker in line for line in stream)
        correlation["network_unique_request"] = bool(requests and token_in_packets)
        correlation["c2_effect"] = result.get("c2_effect_verified") is True
        result["marker_correlation"] = correlation
        result["marker_planted"] = all(value for key, value in correlation.items() if key.endswith((".file", ".registry")))
        result["workload_executed"] = session.status == SessionStatus.COMPLETED and session.execution_exit_code == 0
        outcomes = session.source_outcomes
        result["telemetry_verified"] = bool(result["workload_executed"] and result["live_workload_observed"] and all(correlation.values())
            and outcomes.get("sysmon") and outcomes["sysmon"].live_events_count > 0
            and all(outcomes.get(key) and outcomes[key].verified and outcomes[key].capture_started
                and outcomes[key].exported and outcomes[key].parsed and not outcomes[key].errors
                for key in ("sysmon", "procmon", "network")))
    except Exception as exc:
        logger.exception("Normal pipeline verification failed")
        result["error"] = str(exc)
    finally:
        result["vm_stopped"] = await stop_vm_cleanly(vbox, vm_name)
    return result


async def promote_candidate_to_clean(
    vbox: VirtualBoxClient,
    vm_name: str,
    candidate_name: str,
    recovery_name: str,
    verification: dict | None = None,
    acceptance_file: str | Path | None = None,
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

    acceptance = validate_acceptance(acceptance_file, candidate_name)
    promotion["acceptance"] = acceptance
    if not acceptance["success"]:
        promotion["error"] = acceptance["error"]
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
        if not markers_absent(await check_markers(channel, RESET_FILE, RESET_KEY)):
            raise RuntimeError("Final baseline contains a reset witness or could not be checked")
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
    required_checks = ("restore_success", "guest_ready", "pre_clean_verified", "marker_planted", "live_workload_observed",
                       "workload_executed", "telemetry_verified", "vm_stopped")
    channel1 = _create_channel()
    try:
        cycle1 = await run_verification_cycle(
            vbox, channel1, vm_name, target_candidate, cycle_index=1, evidence_root=output_path.parent
        )
    finally:
        await channel1.aclose()

    reset_witness = {"success": False, "error": "Cycle one did not pass"}
    channel2 = _create_channel()
    try:
        cycle2 = {"cycle": 2, "error": "Cycle one did not pass"}
        if all(cycle1.get(key) is True for key in required_checks):
            reset_witness = await prepare_reset_witness(vbox, channel2, vm_name, target_candidate)
            if reset_witness["success"]:
                cycle2 = await run_verification_cycle(
                    vbox, channel2, vm_name, target_candidate, cycle_index=2,
                    prior_marker_file="C:\\ADAM\\run_marker_1.txt",
                    prior_reg_path="HKLM\\Software\\ADAM\\Cycle1",
                    reset_witness=reset_witness, evidence_root=output_path.parent,
                )
            else:
                cycle2 = {"cycle": 2, "error": "Reset witness could not be established"}
    finally:
        await channel2.aclose()

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
            acceptance_file=getattr(args, "acceptance_file", None),
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
    promotion_passed = (not args.promote) or bool(promotion_result and promotion_result.get("success"))
    overall_passed = bool(cycles_passed and vdi_final.get("untouched", True) and promotion_passed)

    final_result = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "vm_name": vm_name,
        "candidate_snapshot": target_candidate,
        "recovery_snapshot": recovery_snap,
        "cycles": [cycle1, cycle2],
        "reset_witness": reset_witness,
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
        CONTROLLED_REQUESTS.append({"path": self.path, "peer": self.client_address[0], "timestamp": time.time()})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ADAM verification response")

    def log_message(self, *args):
        logger.info("Controlled guest request: %s", self.path)


@keep_system_awake
async def run_harness(args: argparse.Namespace) -> int:
    # The workload intentionally targets only this host-only address.
    CONTROLLED_REQUESTS.clear()
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
    parser.add_argument("--acceptance-file", help="Candidate/revision/build-bound evidence record required for promotion")
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
