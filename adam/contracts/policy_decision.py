"""
PolicyDecision — what the Policy Engine emits for every SemanticEvent it
evaluates (including suppressed ones — §7.4 is explicit that these must be
persisted, not discarded).

LOCAL STUB — see enums.py note.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from adam.contracts.enums import Verdict


class PolicyDecision(BaseModel):
    decision_id: str
    session_id: str
    correlation_id: str
    triggered_by: str  # semantic_id of the SemanticEvent that caused this
    rule_id: str
    rule_version: str
    action: str | None = None  # name of a deception primitive, e.g. SPAWN_FAKE_DC_ARTIFACTS
    verdict: Verdict
    priority: int = 0
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evaluation_ms: float = 0.0
