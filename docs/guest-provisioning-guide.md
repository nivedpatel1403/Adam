# ADAM guest provisioning and verification

Keep the Windows guest on its host-only network. Stage installers on the host, validate publisher signatures, record SHA-256 hashes, and transfer the verified files with the agent package.

[Documentation index](README.md) · [Current limitations](known_limitations.md) · [Dated acceptance](repair-verification.md)

## Configure a new local environment

The repository contains agent source and provisioning utilities, not a Windows VM, tool installers, credentials, configured snapshot, or sample executables. Use a Windows guest you control and obtain the required tooling separately. The [agent README](../adam/sandbox/guest/agent/README.md) documents installation.

After the [root installation steps](../README.md#quick-start), create the gitignored `config/development.toml` from your environment's actual values. `ADAM_ENV` defaults to `development`. Credentials belong in `.env`, not TOML; use `ADAM__SANDBOX__GUEST_USERNAME`, `ADAM__SANDBOX__GUEST_PASSWORD`, and `ADAM__HTTP_GUEST__AUTH_TOKEN`.

```toml
guest_backend = "http"

[sandbox]
vm_name = "Your Windows VM"
snapshot_name = "clean"
guest_ready_timeout_s = 900.0

[http_guest]
host = "192.168.56.101" # example only: replace with the guest host-only IP
port = 8765
tools_required = true

[network]
host_ip = "192.168.56.1" # example only: replace with the host-only adapter IP
```

Use a baseline you have actually verified; the name `clean` alone proves nothing. The guest readiness example above is independent of the CLI workload timeout and is not a claim of successful startup. The committed default is 300 seconds; the November 6 frontend host used a 420-second local override. Do not copy verification-host UUIDs or addresses as your own configuration.

Provision the HTTP agent and capture tools, confirm agent health and normal shutdown, and follow the candidate acceptance process below before treating a baseline as usable. Only one API, CLI, or harness should control the VM at a time. The [initial frontend check](frontend-verification.md) was blocked by VirtualBox state-query timeouts; a subsequent [console repair](console-launch-repair.md) completed a real control run. The November 5 acceptance remains historical evidence.

Default paths:

- `procmon_path = "C:\\ADAM\\Tools\\Procmon\\Procmon64.exe"`
- `tshark_path = "C:\\Program Files\\Wireshark\\tshark.exe"`
- Sysmon channel: `Microsoft-Windows-Sysmon/Operational`

The Procmon executable is `C:\ADAM\Tools\Procmon\Procmon64.exe`.

## Historical staged repair — November 4, 2026

The commands in this section describe a past host-specific repair handoff, not fresh-clone setup or current instructions. That repair's successful acceptance was recorded on [November 5](repair-verification.md); later state-query failures are recorded in [frontend verification](frontend-verification.md). Local evidence directories and temporary shares mentioned below are not distributed.

For the current repair execution, the user has already completed the Npcap wizard and installation. Do not rerun the installer solely because older instructions appear below. The remaining work is automated capture, restoration, failure handling, and baseline verification. Consult `repair-verification.md` for current results. The commands below document the earlier installation handoff.

For the latest November 4 follow-up, the corrected bundle is exposed through the temporary read-only `ADAMRepair` share while the VM remains running. The older staged script has a PowerShell 5.1 manifest parsing bug. Use administrator PowerShell inside the guest:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File '\\VBOXSVR\ADAMRepair\complete_repair.ps1'
```

This verifies and copies the corrected bundle into `C:\ADAM\repair` before running it. Inspect any existing installer before starting another: the previous HTTP installation request lost its response and its outcome is unknown. Complete the Npcap wizard when prompted. The transient share disappears when the VM stops; the host can recreate it from `logs/repair-verification/20261104-172228/handoff`. Remove the share before creating the candidate snapshot.

The November 4 repair bundle is staged at `C:\ADAM\repair`. Start the VM in its current state, without restoring the old recovery snapshot. Log in and open an administrator PowerShell. Run:

```powershell
& 'C:\ADAM\repair\finish_guest_repair.ps1'
```

Complete the Npcap wizard when it opens. The script verifies installer hashes against the host manifest, installs missing tools, verifies the driver and interfaces, deploys the agent as SYSTEM, verifies deployed file hashes, and checks health. It preserves existing authentication settings and performs no snapshot operations.

Standard Npcap requires its graphical installer. Silent installation requires an OEM package and an explicit `-NpcapOem` switch. See the [Npcap installer documentation](https://npcap.com/guide/npcap-users-guide.html).

`provision_guest_tools.ps1` accepts host-staged installer paths and a signature manifest. It performs no Internet downloads, package-manager fallback, or blind key presses.

## Candidate acceptance

After deployment succeeds, the host agent must shut down normally and create a distinct powered-off candidate snapshot. Preserve the recovery snapshot until verification passes. Run `scripts/verify_candidate_snapshots.py` with explicit candidate and recovery UUIDs. Promotion is disabled by default.

Acceptance requires two normal pipeline runs using a benign workload, file/registry/process marker correlation in Sysmon and Procmon, controlled host-only traffic in parsed packet evidence, live Sysmon delivery, restoration checks, successful cleanup, and matching guest build/capabilities. Missing evidence fails verification.

The harness checks the unregistered disk by SHA-256 and never deletes it. Explicit promotion requires distinct UUIDs and successful verification. It checks candidate health before promotion and again after consolidation, then restores the pristine powered-off baseline.

The final state must contain exactly one verified `clean` snapshot and a powered-off VM. Tool presence, unit tests, parser checks, and old completion reports alone do not establish a working VM baseline. Never manually delete snapshot VDI files.
