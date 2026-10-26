# ADAM Guest VM Provisioning Guide

This guide walks through provisioning telemetry tools (**Process Monitor**, **TShark / Wireshark**, and **Sysmon**) inside the VirtualBox guest VM to resolve **Limitation #3** from `known_limitations.md`.

---

## 1. Overview of Guest Requirements

ADAM collects telemetry from three primary sources during malware analysis sessions:
1. **Sysmon**: Process creation, network connections, file creation, registry modifications. (Channel: `Microsoft-Windows-Sysmon/Operational`).
2. **Process Monitor (`Procmon64.exe`)**: Detailed file and registry operation tracing (`.pml` -> `.csv`).
3. **TShark (`tshark.exe`)**: Packet capture on the guest network interface (`.pcapng` -> `.json`).

The committed default paths in `config/default.toml` are:
- `procmon_path = "C:\\Users\\Admin\\Downloads\\ProcessMonitor\\Procmon64.exe"`
- `tshark_path = "C:\\Program Files\\Wireshark\\tshark.exe"`
- `sysmon_log = "Microsoft-Windows-Sysmon/Operational"`

---

## 2. Automated Provisioning Procedure

### Step 1: Boot the VirtualBox Guest VM
Start the VM from the analysis host:
```bash
VBoxManage startvm "Windows" --type gui
```
Log in using the configured guest credentials (`guest_username` / `guest_password`).

### Step 2: Run the Provisioning Script
Inside the guest VM, open an **Elevated PowerShell Prompt (Run as Administrator)**:
```powershell
# Copy scripts/provision_guest_tools.ps1 into the guest (or download/paste it)
Set-ExecutionPolicy Bypass -Scope Process -Force
.\provision_guest_tools.ps1
```

The script will:
- Download and unpack Sysinternals Process Monitor to `C:\Users\Admin\Downloads\ProcessMonitor\Procmon64.exe`.
- Pre-accept the Sysinternals EULA in the registry (`EulaAccepted=1`).
- Download and install Wireshark silently, placing `tshark.exe` at `C:\Program Files\Wireshark\tshark.exe`.
- Verify the Sysmon service status.

### Step 3: Take a Clean Golden Snapshot
Shut down the VM cleanly:
```powershell
Stop-Computer -Force
```
On the host, take a new snapshot:
```bash
VBoxManage snapshot "Windows" take "disk-resized-36gb-c-drive"
```

---

## 3. Graceful Degradation in Code

If you cannot immediately update the guest snapshot, ADAM handles missing tools gracefully:

1. **Non-blocking verification**: `HTTPGuestChannel.verify_tools()` logs warnings for missing tools instead of raising exceptions.
2. **Skipping inactive collectors**: `start_captures()` inspects the cached `tool_availability` and skips capture requests for uninstalled tools.
3. **Strict mode option**: Setting `tools_required = true` in `config/default.toml` under `[guest_tools]` or `[http_guest]` enforces that missing tools will fail fast if required for strict environments.
