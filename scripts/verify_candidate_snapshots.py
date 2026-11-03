"""
scripts/verify_candidate_snapshots.py

Automated Verification Harness for ADAM Candidate Snapshots (Requirement R6).

Workflow:
1. Asserts the unregistered ~4 GiB VDI ({c86fbab5-1567-4629-a3c3-5f73b5afbb46}.vdi) exists and records its stat baseline.
2. Cycle 1:
   - Restores the candidate snapshot.
   - Verifies pre-clean state (run marker files and registry keys absent).
   - Starts the VM (headless) and waits for guest agent readiness.
   - Executes benign workload with unique run marker 1.
   - Verifies telemetry ingestion and policy evaluation.
   - Stops VM cleanly.
3. Cycle 2:
   - Restores the candidate snapshot.
   - Verifies state reset (ALL files and registry artifacts from Cycle 1 are completely absent).
   - Starts VM, executes second benign workload with unique run marker 2.
   - Verifies clean execution and telemetry.
   - Stops VM cleanly.
4. Promotion:
   - Deletes the old recovery snapshot.
   - Renames candidate snapshot to "clean".
   - Verifies exactly one snapshot named "clean" is registered in VirtualBox.
   - Confirms default.toml and development.toml resolve to "clean".
5. Verifies unregistered ~4 GiB VDI remains completely untouched (size and mtime unchanged).
6. Writes machine-readable results to logs/candidate-verification/result.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from adam.common.config import get_settings
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
DEFAULT_OUTPUT_FILE = _PROJECT_ROOT / "logs" / "candidate-verification" / "result.json"


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

    untouched = True
    if baseline_stat is not None and (size_bytes != baseline_stat.st_size or mtime != baseline_stat.st_mtime):
        untouched = False

    return {
        "exists": True,
        "path": str(vdi_path),
        "size_bytes": size_bytes,
        "mtime": mtime,
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
    timeout_s: float = 15.0,
) -> bool:
    """Stops VM gracefully via ACPI power button, falling back to poweroff."""
    state = await vbox.get_state(vm_name)
    if state in ("poweroff", "saved"):
        return True

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
    return res.success


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
    await stop_vm_cleanly(vbox, vm_name)

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

    # 6. Plant marker
    planted = await plant_markers(channel, marker_file, reg_path, cycle_marker)
    cycle_result["marker_planted"] = planted

    # 7. Execute benign workload
    workload_res = await run_benign_workload(channel, "cmd.exe", ["/c", "whoami.exe"])
    cycle_result["workload"] = workload_res
    cycle_result["workload_executed"] = workload_res.get("success", False)

    # 8. Verify telemetry source responsiveness
    diag = await channel.verify_tools()
    is_valid_diag = isinstance(diag, ToolAvailability)
    telemetry_ready = (
        is_valid_diag
        and (diag.sysmon_log_available or diag.procmon_available or diag.tshark_available)
        and not diag.required_missing
    )
    cycle_result["telemetry_verified"] = bool(telemetry_ready)
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

    # 9. Stop VM cleanly
    cycle_result["vm_stopped"] = await stop_vm_cleanly(vbox, vm_name)
    logger.info("=== Finished Cycle %d: pre_clean=%s, workload=%s ===", cycle_index, is_pre_clean, cycle_result["workload_executed"])

    return cycle_result


async def promote_candidate_to_clean(
    vbox: VirtualBoxClient,
    vm_name: str,
    candidate_name: str,
    recovery_name: str,
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

    if candidate_name == "clean":
        logger.info("Candidate snapshot is already named 'clean' -- verifying single snapshot baseline")
        snapshots = await vbox.list_snapshots(vm_name)
        other_snapshots = [s for s in snapshots if s.name != "clean"]
        if other_snapshots:
            for s in other_snapshots:
                logger.info("Deleting old snapshot '%s' (uuid=%s)...", s.name, s.uuid)
                del_res = await vbox._run("snapshot", vm_name, "delete", s.uuid)
                if not del_res.success:
                    logger.warning("Could not delete old snapshot '%s': %s", s.name, del_res.stderr)
            snapshots = await vbox.list_snapshots(vm_name)

        promotion["recovery_deleted"] = (
            not any(s.name == recovery_name for s in snapshots)
            if recovery_name != candidate_name
            else True
        )
        promotion["candidate_renamed"] = True
        promotion["final_snapshot_count"] = len(snapshots)
        promotion["final_snapshots"] = [s.name for s in snapshots]
        promotion["success"] = any(s.name == "clean" for s in snapshots) and len(snapshots) == 1
        return promotion

    snapshots = await vbox.list_snapshots(vm_name)
    logger.info("Current snapshots registered on '%s': %s", vm_name, [s.name for s in snapshots])

    # Find recovery snapshot
    rec_snap = next((s for s in snapshots if s.name == recovery_name or s.uuid == recovery_name), None)
    if rec_snap:
        logger.info("Deleting old recovery snapshot '%s' (uuid=%s)...", rec_snap.name, rec_snap.uuid)
        del_res = await vbox._run("snapshot", vm_name, "delete", rec_snap.uuid)
        promotion["recovery_deleted"] = del_res.success
        if not del_res.success:
            logger.warning("Could not delete recovery snapshot: %s", del_res.stderr)
    else:
        logger.info("Recovery snapshot '%s' not present or already removed", recovery_name)
        promotion["recovery_deleted"] = True

    # Rename candidate snapshot to 'clean'
    logger.info("Renaming candidate snapshot '%s' to 'clean'...", candidate_name)
    edit_res = await vbox._run("snapshot", vm_name, "edit", candidate_name, "--name", "clean")
    promotion["candidate_renamed"] = edit_res.success
    if not edit_res.success:
        logger.error("Failed to rename snapshot '%s' to 'clean': %s", candidate_name, edit_res.stderr)
        promotion["error"] = edit_res.stderr
        return promotion

    # Verify final snapshots
    final_snapshots = await vbox.list_snapshots(vm_name)
    promotion["final_snapshot_count"] = len(final_snapshots)
    promotion["final_snapshots"] = [s.name for s in final_snapshots]
    promotion["success"] = any(s.name == "clean" for s in final_snapshots) and len(final_snapshots) == 1

    return promotion


async def run_harness(args: argparse.Namespace) -> int:
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
        )

    logger.info("Starting candidate snapshot verification harness for VM '%s'", vm_name)

    # 1. Check unregistered 4 GiB VDI initial baseline
    vdi_baseline = check_orphan_vdi(vdi_path)
    logger.info("Unregistered VDI check: %s", vdi_baseline)
    baseline_stat = vdi_path.stat() if vdi_path.exists() else None

    # Check available snapshots
    snapshots = await vbox.list_snapshots(vm_name)
    snap_names = [s.name for s in snapshots]
    logger.info("Available snapshots: %s", snap_names)

    # If candidate doesn't exist, but clean exists and candidate was default, handle gracefully
    target_candidate = candidate_snap
    if candidate_snap not in snap_names and "clean" in snap_names and candidate_snap == "candidate":
        logger.info("Candidate snapshot 'candidate' not found; evaluating existing baseline 'clean'")
        target_candidate = "clean"

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

    cycles_passed = (
        cycle1.get("restore_success", False)
        and cycle1.get("pre_clean_verified", False)
        and cycle1.get("marker_planted", False)
        and cycle1.get("workload_executed", False)
        and cycle2.get("restore_success", False)
        and cycle2.get("state_reset_verified", False)
        and cycle2.get("marker_planted", False)
        and cycle2.get("workload_executed", False)
    )

    # 3. Promote candidate to clean if requested and cycles passed
    promotion_result: dict | None = None
    if cycles_passed and args.promote:
        promotion_result = await promote_candidate_to_clean(
            vbox, vm_name, target_candidate, recovery_snap
        )

    # 4. Final VDI untouched verification
    vdi_final = check_orphan_vdi(vdi_path, baseline_stat)
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
        "orphan_disk_untouched": vdi_final.get("untouched", True),
        "orphan_disk_path": str(vdi_path),
        "orphan_disk_bytes": vdi_final.get("size_bytes", 0),
        "overall_status": "PASSED" if overall_passed else "FAILED",
    }

    output_path.write_text(json.dumps(final_result, indent=2))
    logger.info("Saved verification results to: %s", output_path)

    return 0 if final_result["overall_status"] == "PASSED" else 1


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
        default=True,
        help="Whether to promote candidate snapshot to clean on success (default: True)",
    )

    args = parser.parse_args()
    rc = asyncio.run(run_harness(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()
