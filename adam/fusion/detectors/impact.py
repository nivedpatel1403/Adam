from __future__ import annotations

from typing import ClassVar

from ..models import RawEvent, SemanticEvent
from .base import BaseDetector


class ImpactDetector(BaseDetector):
    """
    Detects destructive or ransomware-like behaviour.

    MITRE ATT&CK:
        T1486 - Data Encrypted for Impact
    """

    TECHNIQUE_ID = "T1486"

    STRONG_INDICATORS: ClassVar[dict[str, str]] = [
        "vssadmin delete shadows",
        "cipher /w",
        "bcdedit",
        "wbadmin delete catalog",
        "encryptor.exe",
    ]

    MEDIUM_INDICATORS: ClassVar[dict[str, str]] = [
        ".locked",
        ".encrypted",
        ".crypt",
        "ransom",
        "delete shadows",
    ]

    WEAK_INDICATORS: ClassVar[dict[str, str]] = [
        "rename",
        "overwrite",
        "wipe",
        "format ",
        "format.exe",
        "format.com",
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
            category="Impact",
            technique_id=self.TECHNIQUE_ID,
            severity="CRITICAL",
            confidence=0.96,
            description="Potential destructive or ransomware activity detected.",
            evidence=matched,
        )
