from __future__ import annotations

from ..models import RawEvent, SemanticEvent
from .base import BaseDetector


class CommandAndControlDetector(BaseDetector):
    """
    Detects Command and Control activity.

    MITRE ATT&CK:
        T1071 - Application Layer Protocol
    """

    TECHNIQUE_ID = "T1071"

    STRONG_INDICATORS = [
        "curl http",
        "wget http",
        "powershell iwr",
        "powershell invoke-webrequest",
        "invoke-webrequest",
        "iwr http",
        "certutil -urlcache",
        "bitsadmin",
        "nc.exe",
        "netcat",
        "c2_beacon",
        "c2_beacon_test",
    ]

    MEDIUM_INDICATORS = [
        "http://",
        "https://",
        "ftp://",
        "dns",
        "beacon",
        "callback",
        "reverse shell",
        "c2.baddomain.org",
    ]

    WEAK_INDICATORS = [
        "socket",
        "connect",
        "downloadstring",
        "webclient",
    ]

    SCORE_THRESHOLD = 6

    SUSPICIOUS_DNS_PREFIXES = ("c2.", "beacon.", "callback.")
    SUSPICIOUS_DNS_SUFFIXES = (".xyz", ".top", ".tk", ".ru")

    @classmethod
    def _suspicious_dns(cls, event: RawEvent) -> bool:
        payload = event.payload or {}
        query = str(payload.get("QueryName", "")).strip().lower().rstrip(".")
        if not query:
            return False
        return query.startswith(cls.SUSPICIOUS_DNS_PREFIXES) or query.endswith(
            cls.SUSPICIOUS_DNS_SUFFIXES
        )

    def match(self, events: list[RawEvent]) -> list[RawEvent] | None:

        score, matched = self.score_events(events)

        # Sysmon EventID 22 (DNSEvent) has no command line, so a
        # C2-looking QueryName would otherwise never reach the threshold.
        for event in events:
            if self._suspicious_dns(event):
                if event not in matched:
                    matched.append(event)
                score += self.SCORE_THRESHOLD

        if score >= self.SCORE_THRESHOLD:
            return matched

        return None

    def build(self, matched: list[RawEvent]) -> SemanticEvent:

        return SemanticEvent(
            timestamp=matched[0].timestamp,
            category="Command and Control",
            technique_id=self.TECHNIQUE_ID,
            severity="HIGH",
            confidence=0.91,
            description="Potential command and control communication detected.",
            evidence=matched,
        )