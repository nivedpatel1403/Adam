# ADAM documentation

[Project overview](../README.md) · [React console](../frontend/adam-ui/README.md) · [Known limitations](known_limitations.md)

Start with synthetic replay or simulation to explore the pipeline without a VM. Live analysis requires a separately provisioned Windows guest and local configuration.

## Setup and operation

| Guide | Purpose |
| --- | --- |
| [Quick start](../README.md#quick-start) | Install, replay generated events, and start the console. |
| [React console](../frontend/adam-ui/README.md) | Primary interface, real versus synthetic modes, and checks. |
| [Guest setup and verification](guest-provisioning-guide.md) | Configure the Windows guest, tools, and acceptance process. |
| [Guest agent](../adam/sandbox/guest/agent/README.md) | PowerShell HTTP service and installer reference. |
| [HTTP guest API](phase5-http-agent-api.md) | Agent protocol reference. |
| [Network interception](network-interception.md) | Bounded HTTP proxy, configuration, and unsupported transports. |

The legacy Streamlit dashboard is an optional interface; installation and startup are in the [root README](../README.md#documentation).

## Architecture and extension

| Reference | Purpose |
| --- | --- |
| [Architecture specification](../ARCHITECTURE.md) | Original system design; use current source and dated verification for implementation status. |
| [Policy and deception](policy_deception_reference.md) | Rule/deception reference. |
| [Rule authoring](rule_authoring.md) | Author and understand YAML policies. |
| [Runtime settings model](../adam/common/config.py) | Settings names, validation, and precedence. |
| [Committed defaults](../config/default.toml) | Baseline values; machine-specific overrides are not distributed. |

## Status and evidence

| Record | Interpretation |
| --- | --- |
| [Known limitations](known_limitations.md) | Current operating boundaries and unresolved startup reliability. |
| [November 6 frontend verification](frontend-verification.md) | Passing browser/component checks and a fresh live run blocked before launch. |
| [November 5 repair acceptance](repair-verification.md) | Historical real-VM acceptance and promotion, not a current health assertion. |
| [Snapshot lineage](../config/NOTES.md) | Historical maintenance notes; older sections are superseded by the dated acceptance report. |
| [October 26 audit](audit/baseline-2026-10-26.md) | Historical audit baseline. |

The latest frontend checks used implementation commit `c284feb`. During those checks, VirtualBox stopped answering state queries and no fresh live detonation was started. Earlier successful real captures remain valid historical evidence; neither screenshots nor synthetic comparisons establish current VM health.

Raw logs, databases, captures, and VM images are intentionally excluded from Git. Paths labeled **local evidence** describe files from the verification host and will not exist in a fresh clone. Published screenshots are in [images](images/README.md). The private local `DESIGN.md` is also excluded; public setup must not depend on it.
