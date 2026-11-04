# ADAM guest provisioning and verification

Keep the Windows guest on its host-only network. Stage installers on the host, validate publisher signatures, record SHA-256 hashes, and transfer the verified files with the agent package.

Default paths:

- `procmon_path = "C:\\ADAM\\Tools\\Procmon\\Procmon64.exe"`
- `tshark_path = "C:\\Program Files\\Wireshark\\tshark.exe"`
- Sysmon channel: `Microsoft-Windows-Sysmon/Operational`

The Procmon executable is `C:\ADAM\Tools\Procmon\Procmon64.exe`.

## Complete the staged repair

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
