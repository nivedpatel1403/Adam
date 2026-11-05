from __future__ import annotations

from typing import ClassVar

from ..models import RawEvent, SemanticEvent
from .base import BaseDetector


class CollectionDetector(BaseDetector):
    """
    Detects collection of sensitive files and information.

    MITRE ATT&CK:
        T1005 - Data from Local System
    """

    TECHNIQUE_ID = "T1005"

    STRONG_INDICATORS: ClassVar[dict[str, str]] = [
        "copy-item",
        "robocopy",
        "xcopy",
        "esentutl",
        "7z.exe",
        "rar.exe",
    ]

    MEDIUM_INDICATORS: ClassVar[dict[str, str]] = [
        "documents",
        "desktop",
        "downloads",
        "finance",
        "confidential",
        "archive",
    ]

    WEAK_INDICATORS: ClassVar[dict[str, str]] = [
        ".docx",
        ".xlsx",
        ".pdf",
        ".pptx",
        ".zip",
    ]

    SCORE_THRESHOLD = 6

    def score_events(self, events: list[RawEvent]) -> tuple[int, list[RawEvent]]:
        """Score file paths and commands, excluding unrelated diagnostic detail.

        Procmon's detail field includes ordinary Windows access flags such as
        "Open For Backup". Registry paths such as Control Panel\\Desktop are
        also not evidence of collecting a user's desktop files.
        """
        matched = []
        total = 0
        for event in events:
            if event.event_type == "REGISTRY":
                continue
            payload = event.payload or {}
            searchable = " ".join(str(value).lower() for value in (
                event.process_name or "", event.command_line or "",
                payload.get("command_line", ""), payload.get("TargetFilename", ""),
                payload.get("path", ""),
            ))
            score = 5 * any(term in searchable for term in self.STRONG_INDICATORS)
            score += 3 * any(term in searchable for term in self.MEDIUM_INDICATORS)
            score += 2 * any(term in searchable for term in self.WEAK_INDICATORS)
            if score:
                total += score
                matched.append(event)
        return total, matched

    def match(self, events: list[RawEvent]) -> list[RawEvent] | None:

        score, matched = self.score_events(events)

        if score >= self.SCORE_THRESHOLD:
            return matched

        return None

    def build(self, matched: list[RawEvent]) -> SemanticEvent:

        return SemanticEvent(
            timestamp=matched[0].timestamp,
            category="Collection",
            technique_id=self.TECHNIQUE_ID,
            severity="MEDIUM",
            confidence=0.89,
            description="Potential collection of sensitive data detected.",
            evidence=matched,
        )
