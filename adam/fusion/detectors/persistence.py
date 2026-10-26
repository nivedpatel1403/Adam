from datetime import datetime, timezone

from .base import BaseDetector
from ..models import SemanticEvent


class PersistenceDetector(BaseDetector):
    """
    Detects common Windows persistence mechanisms.
    MITRE ATT&CK: T1547 - Boot or Logon Autostart Execution
    """

    REGISTRY_RUN_KEYS = [
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
            elif process == "schtasks.exe":
                matched.append(event)

            # Windows Service Creation
            elif process == "sc.exe":
                if "create" in command:
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
            timestamp=datetime.now(timezone.utc),
            category="Persistence",
            technique_id="T1547",
            severity="MEDIUM",
            confidence=0.88,
            description="Possible persistence mechanism detected.",
            evidence=events
    )