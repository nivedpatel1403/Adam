from datetime import UTC, datetime
from typing import ClassVar

from ..models import SemanticEvent
from .base import BaseDetector


class PersistenceDetector(BaseDetector):
    """
    Detects common Windows persistence mechanisms.
    MITRE ATT&CK: T1547 - Boot or Logon Autostart Execution
    """

    REGISTRY_RUN_KEYS: ClassVar[dict[str, str]] = [
        r"currentversion\run",
        r"currentversion\runonce"
    ]

    def match(self, events):
        matched = []

        for event in events:
            process = (event.process_name or "").lower()
            payload = event.payload or {}

            command = str(payload.get("command_line", "")).lower()
            target_object = str(payload.get("TargetObject", "")).lower()
            target_file = str(payload.get("TargetFilename", "")).lower()

            # Sysmon RegistryEvent (12/13): the registry key itself is the
            # indicator, even when the writing process is not reg.exe.
            if any(key in target_object for key in self.REGISTRY_RUN_KEYS):
                matched.append(event)
                continue

            # Sysmon FileCreate (11): a drop into a Startup folder.
            if "\\startup\\" in target_file:
                matched.append(event)
                continue

            # Registry Run Keys written through reg.exe (ProcessCreate or
            # command-line-only telemetry).
            if process == "reg.exe":
                if any(key in command for key in self.REGISTRY_RUN_KEYS):
                    matched.append(event)

            # Scheduled Tasks
            elif process == "schtasks.exe" or process == "sc.exe" and "create" in command:
                matched.append(event)

        return matched if matched else None

    def build(self, events):

        evidence = []

        for event in events:
            evidence.append({
                "timestamp": event.timestamp.isoformat(),
                "process": event.process_name,
                "command": event.payload.get("command_line", "")
            })

        return SemanticEvent(
            timestamp=datetime.now(UTC),
            category="Persistence",
            technique_id="T1547",
            severity="MEDIUM",
            confidence=0.88,
            description="Possible persistence mechanism detected.",
            evidence=events
    )