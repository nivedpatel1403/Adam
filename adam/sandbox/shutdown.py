"""Normal guest shutdown with an authoritative hypervisor state check."""
import logging

logger = logging.getLogger(__name__)


async def shutdown_normally(client, vm_name, *, channel=None, timeout=90):
    if await client.get_state(vm_name) == "poweroff":
        return True
    requested = False
    if channel is not None and hasattr(channel, "run_process"):
        try:
            result = await channel.run_process(
                r"C:\Windows\System32\shutdown.exe", ["/s", "/t", "5"],
                wait=True, timeout_s=10,
            )
            requested = result.return_code == 0
        except Exception:
            logger.warning("Guest shutdown response unavailable; checking VM state", exc_info=True)
    if not requested and await client.get_state(vm_name) != "poweroff":
        result = await client.stop(vm_name, mode="acpi")
        if not result.success:
            return False
    result = await client.wait_for_state(vm_name, "poweroff", timeout=timeout)
    return result.success
