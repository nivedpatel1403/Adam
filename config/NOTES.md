# Sandbox snapshot lineage and maintenance

> Historical maintenance notes. The November 3 baseline below was superseded by the [November 5 acceptance](../docs/repair-verification.md), which recorded promotion of `4d3f4a15-5567-474f-96e5-208537a5ce4f`. The [November 6 frontend check](../docs/frontend-verification.md) subsequently encountered state-query timeouts and did not establish current VM health. Local backup paths and logs below are historical references, not files provided by this repository.

## Historical baseline: one snapshot (2026-11-03; superseded)

Only `clean` remains, UUID `b15f95bb-d463-444f-9019-c7dd966a7d90`.
It is the previously tested `disk-resized-36gb-c-drive` snapshot, renamed after
removing the other branch restore points and merging its ancestor with
VirtualBox's supported operations. Both default and development configuration
now select `clean`. Its contents are preserved; this is not either of the
older snapshots that also used the name `clean`.

Pre-consolidation checks confirmed boot, HTTP agent health, successful SYSTEM
execution of `whoami.exe`, and an available Sysmon channel. Procmon exists at
`C:\ADAM\Tools\Procmon\Procmon64.exe`, but the guest agent still uses an older
path for capture. Tshark is missing, and `/sysmon/events/since` returns 404.
The snapshot is a working baseline, not a claim that all telemetry is healthy.
The alternative v4 snapshot did not reach a working agent during its boot check.

The earlier full temporary backup was no longer present when this consolidation
started. No new full backup was created. The unregistered disk mentioned below
remains untouched. Local checks and merge logs are in
`logs/single-clean-snapshot/`. The earlier snapshot descriptions below are
historical and do not represent the current tree or available restore points.

Final verification through the CLI using the renamed snapshot completed:
`sess_2026_11_03_9b0e7290`, control arm, 6,463 Sysmon raw events captured
(including historical guest records). Cleanup restored `clean` to its saved
state. The configuration tests passed (24 tests). Procmon capture still fails
at its obsolete guest-agent path; the tooling gaps above remain unresolved.
The VM folder is now 24.16 GiB, down from 37.39 GiB with the original eight
snapshots (13.23 GiB recovered overall).

## Earlier four-snapshot consolidation (2026-11-03; superseded)

VirtualBox's supported snapshot-delete/merge operation consolidated four
intermediate checkpoints. The retained snapshot names and UUIDs are unchanged:

```text
clean (4a81ac05-7cbb-4275-8ecf-57cd1e0b63f6)
├── clean (af1bc3a4-dc6b-4cb2-aa34-a5fb278b4318) — hardened fallback
├── golden-v4-live-reaction (a5e7ca5a-32c6-4868-b7b2-338fadf8d534)
└── disk-resized-36gb-c-drive (b15f95bb-d463-444f-9019-c7dd966a7d90) — configured baseline
```

The common ancestor remains to avoid duplicating its disk data into all three
branches. Consolidated checkpoints: `Clean` (c1e810b0),
`golden-v2-http-agent-verified` (4f293bee),
`golden-v3-samples-autologon-verified` (72c7b7ba), and
`clean-samples-dir` (4a15ae25). Their individual restore points no longer exist;
VirtualBox merged the disk data needed by the surviving snapshots.

The VM directory decreased from 37.39 GiB to 33.29 GiB, before subsequent boot
activity. Retained hardware/network settings match the pre-merge configuration,
and all registered disk chains are accessible. Local inventories and merge logs
are under `logs/snapshot-consolidation/` (not committed).

Post-merge verification booted the configured baseline and received HTTP 200
with `status: ok` from its guest agent, then restored the baseline to its saved
state. A temporary full backup remains at
`C:\Users\NIVED\VirtualBox Backups\Windows-before-consolidation-20261103`:
automatic approval policy blocked its cleanup. It occupies 37.39 GiB; the host
does not realize the net space savings until that temporary copy is removed.

Do not delete `.vdi` or `.sav` files by hand. Use VirtualBox snapshot operations,
identify snapshots by UUID (two are named `clean`), and preserve the configured
baseline. Create new checkpoints only for deliberate guest provisioning changes.
The extra unregistered disk `{c86fbab5-1567-4629-a3c3-5f73b5afbb46}.vdi` was
left untouched because its provenance has not been established.

## Historical notes (2026-08-11; tree below predates consolidation)

Date documented: 2026-08-11 (verified through 04 boots of `disk-resized-36gb-c-drive`, incl. 2 explicit `VBoxManage snapshot restore` cycles).

## Canonical golden baseline going forward: `disk-resized-36gb-c-drive`

This is the snapshot `config/development.toml` → `[sandbox].snapshot_name` now points to.

Its verification is *stronger* than the original `golden-v2-http-agent-verified` ever received:

| Boot | Operation | T0→bind | Evidence |
|------|-----------|---------|----------|
| 3–4 | plain `startvm` (persisted disk) | 3m00s / 2m16s | task 2026-08-11, `C:\tmp\boot4_*`, agent-log bind lines |
| 5–6 | **explicit `VBoxManage snapshot restore`** (the real per-run op) | ≈1m58s / ≈2m00s | task 2026-08-11 §7, `C:\tmp\restore{1,2}_*.log`, trace/phases |

Per the 2026-08-11 investigation, the 8m01s/8m31s of boots 1–2 was a **one-time post-resize transient** (first boots after the 20→36 GB diskpart extend), not the per-run restore cost; snapshot restore does not discard anything that re-introduces it (Add-Type compile stayed ~15.5 s on both restores).

`disk-resized-36gb-c-drive` also carries the `C:\ADAM\samples` staging directory (confirmed present on-guest via `guestcontrol` 2026-08-11, created 2026-08-09 13:05:46, still empty as designed) — inherited from `clean-samples-dir`.

## Known-unresolved lineage fact (do NOT assume otherwise)

```
clean (4a81ac05-7cbb-4275-8ecf-57cd1e0b63f6)
├── golden-v2-http-agent-verified (4f293bee-…)            ← original golden, still intact, untouched
├── clean-samples-dir (4a15ae25-…)                          ← sibling of golden, NOT its child
│   └── disk-resized-36gb-c-drive (93ec1364-…)  ← current baseline; descends from the samples branch
└── clean (hardened, af1bc3a4-…)
```

`golden-v2-http-agent-verified` and the `clean-samples-dir → disk-resized-36gb-c-drive` branch are **siblings under `clean`** — they are *not* in a parent/child relationship. Anything that later restructures the snapshot tree (e.g. collapsing to a single linear chain for ADAM's `restore_snapshot` atomicity assumptions in `adam/sandbox/controller.py`) must treat these as two distinct branches and reconcile them explicitly; do not assume `disk-resized-36gb-c-drive` is a descendant of `golden-v2-http-agent-verified`.

`golden-v2-http-agent-verified` is left **intact** (and `clean-samples-dir` likewise) as a rollback target. `disk-resized-36gb-c-drive` is the new working baseline purely because it is (a) the only snapshot verified through the actual per-run restore path, and (b) its larger disk is the current production need.
