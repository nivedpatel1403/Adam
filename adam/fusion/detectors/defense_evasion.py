from __future__ import annotations

from typing import ClassVar

from ..models import RawEvent, SemanticEvent
from .base import BaseDetector


class DefenseEvasionDetector(BaseDetector):
    """
    Detects attempts to evade security products.

    MITRE ATT&CK:
        T1562 - Impair Defenses
    """

    TECHNIQUE_ID = "T1562"

    STRONG_INDICATORS: ClassVar[dict[str, str]] = [
        "set-mppreference",
        "disableantispyware",
        "disablebehaviormonitoring",
        "tamperprotection",
        "windefend",
    ]

    MEDIUM_INDICATORS: ClassVar[dict[str, str]] = [
        "sc stop windefend",
        "net stop windefend",
        "stop-service windefend",
        "taskkill /f",
        "uninstall defender",
        "disable realtime monitoring",
    ]

    WEAK_INDICATORS: ClassVar[dict[str, str]] = [
        "bypass",
        "amsi",
        "etw",
        "powershell -ep bypass",
        "powershell -executionpolicy bypass",
    ]

    SCORE_THRESHOLD = 6

    def match(self, events: list[RawEvent]) -> list[RawEvent] | None:

        score, matched = self.score_events(events)

        if score >= self.SCORE_THRESHOLD:
            return matched

        return None

    def build(self, matched: list[RawEvent]) -> SemanticEvent:

        return SemanticEvent(
            timestamp=matched[0].timestamp,
            category="Defense Evasion",
            technique_id=self.TECHNIQUE_ID,
            severity="HIGH",
            confidence=0.91,
            description="Potential defense evasion activity detected.",
            evidence=matched,
        )