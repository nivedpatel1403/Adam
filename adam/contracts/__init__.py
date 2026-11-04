"""
adam/contracts/

The frozen boundary (ARCHITECTURE.md section 7). Re-exports the public
models/Protocols so callers can `from adam.contracts import RawEvent` rather
than reaching into submodules.

Scope of this package today (Dev A's Phase 2 proposal, pending the
all-four-developer review required by section 10.2): `Envelope`, `RawEvent`,
`ProcessInfo`, `AnalysisSession`, `SampleRef`, `SessionConfig`,
`SessionMetrics`, the enums in `enums.py`, and `ICollector` /
`ISandboxController` (plus their minimal supporting types) in
`interfaces.py`.

Policy/Deception interfaces (`IPolicyEngine`, `IDeceptionEngine`, etc.) and
their supporting enums (`Verdict`, `MutationStatus`, `ChangeKind`) are owned
by Dev C and are exported from this package for cross-module use.
"""

from __future__ import annotations

from adam.contracts.enums import (
    Arm,
    Category,
    ChangeKind,
    MutationStatus,
    NetworkMode,
    SessionStatus,
    Source,
    Verdict,
)
from adam.contracts.envelope import Envelope
from adam.contracts.interfaces import (
    ArtifactRef,
    ICollector,
    IDeception,
    IDeceptionEngine,
    IPolicyEngine,
    IPredicate,
    IReportGenerator,
    IRuleLoader,
    ISandboxController,
    MutationRequest,
    MutationResult,
    SessionContextProtocol,
)
from adam.contracts.profile import DecoyPersona, GuestAgentConfig, HardwareConfig, VMProfile
from adam.contracts.raw_event import ProcessInfo, RawEvent
from adam.contracts.session import (
    AnalysisSession,
    SampleRef,
    SessionConfig,
    SessionLifecycle,
    SessionMetrics,
)

__all__ = [
    "AnalysisSession",
    # Enums
    "Arm",
    # Interfaces — Sandbox / Collectors
    "ArtifactRef",
    "Category",
    "ChangeKind",
    "DecoyPersona",
    # Models
    "Envelope",
    "GuestAgentConfig",
    "HardwareConfig",
    "ICollector",
    # Interfaces — Policy / Deception
    "IDeception",
    "IDeceptionEngine",
    "IPolicyEngine",
    "IPredicate",
    "IReportGenerator",
    "IRuleLoader",
    "ISandboxController",
    "MutationRequest",
    "MutationResult",
    "MutationStatus",
    "NetworkMode",
    "ProcessInfo",
    "RawEvent",
    "SampleRef",
    "SessionConfig",
    "SessionContextProtocol",
    "SessionLifecycle",
    "SessionMetrics",
    "SessionStatus",
    "Source",
    "VMProfile",
    "Verdict",
]
