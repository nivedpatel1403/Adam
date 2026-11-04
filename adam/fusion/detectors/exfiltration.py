from __future__ import annotations

from typing import ClassVar

from ..models import RawEvent, SemanticEvent
from .base import BaseDetector


class ExfiltrationDetector(BaseDetector):
    """
    Detects data exfiltration behaviour.

    MITRE ATT&CK:
        T1041 - Exfiltration Over C2 Channel
    """

    TECHNIQUE_ID = "T1041"

    STRONG_INDICATORS: ClassVar[dict[str, str]] = [
        "rclone",
        "megasync",
        "dropbox.exe",
        "onedrive.exe",
        "scp",
        "sftp",
    ]

    MEDIUM_INDICATORS: ClassVar[dict[str, str]] = [
        "upload",
        "send-file",
        "aws s3 cp",
        "azcopy",
        "google drive",
        "ftp put",
    ]

    WEAK_INDICATORS: ClassVar[dict[str, str]] = [
        ".zip",
        ".7z",
        ".rar",
        "archive",
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
            category="Exfiltration",
            technique_id=self.TECHNIQUE_ID,
            severity="HIGH",
            confidence=0.93,
            description="Potential data exfiltration detected.",
            evidence=matched,
        )