import logging
from datetime import UTC, datetime

from adam.contracts.interfaces import IReportGenerator
from adam.db.interfaces import (
    IDecisionRepository,
    IEventRepository,
    IMutationRepository,
    ISessionRepository,
)
from adam.reporting.renderers import HTMLRenderer, JSONRenderer, MarkdownRenderer


def format_local_time(dt, include_ms=False):
    if not dt:
        return "N/A"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
            return dt
    try:
        if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
            local_dt = dt.astimezone()
        else:
            local_dt = dt.replace(tzinfo=UTC).astimezone()
        
        fmt = "%Y-%m-%d %H:%M:%S.%f" if include_ms else "%Y-%m-%d %H:%M:%S"
        res = local_dt.strftime(fmt)
        tz_name = local_dt.strftime("%Z") or "Local"
        return f"{res[:-3] if include_ms else res} ({tz_name})"
    except Exception:
        logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
        return str(dt)

class ReportGenerator(IReportGenerator):
    def __init__(
        self,
        session_repo: ISessionRepository,
        event_repo: IEventRepository,
        decision_repo: IDecisionRepository,
        mutation_repo: IMutationRepository,
        plausibility_warn_below: float = 0.5
    ):
        self.session_repo = session_repo
        self.event_repo = event_repo
        self.decision_repo = decision_repo
        self.mutation_repo = mutation_repo
        self.plausibility_warn_below = plausibility_warn_below
        
        self.renderers = {
            "json": JSONRenderer(),
            "md": MarkdownRenderer(),
            "html": HTMLRenderer(),
        }

    async def generate(self, session_id: str, format: str = "json") -> str:
        session = await self.session_repo.get_by_id(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        events = await self.event_repo.get_semantic_by_session(session_id)
        decisions = await self.decision_repo.get_by_session(session_id)
        mutations = await self.mutation_repo.get_by_session(session_id)

        # Timeline (with localized timestamps)
        timeline = []
        for e in events:
            timeline.append({
                "type": "SemanticEvent",
                "time": format_local_time(e.window_start, include_ms=True),
                "intent": e.intent,
                "confidence": e.confidence,
                "detector": getattr(e, "detector", "FusionDetector")
            })
        for m in mutations:
            timeline.append({
                "type": "MutationResult",
                "time": format_local_time(m.applied_at, include_ms=True),
                "primitive": m.primitive,
                "status": m.status.value if hasattr(m.status, "value") else str(m.status),
                "plausibility": getattr(m, "plausibility_score", 1.0)
            })
        timeline.sort(key=lambda x: str(x["time"]))

        # ATT&CK Coverage
        attck_coverage = []
        for e in events:
            if e.attck:
                cov = f"{e.attck.tactic} / {e.attck.technique}"
                if cov not in attck_coverage:
                    attck_coverage.append(cov)

        # IOC extraction (deduplicated)
        seen_iocs = set()
        iocs = []
        for m in mutations:
            for c in m.changes:
                key = ("mutation", c.target, c.operation or "SET")
                if key not in seen_iocs:
                    seen_iocs.add(key)
                    iocs.append({"source": "mutation", "target": c.target, "operation": c.operation or "SET"})
        for e in events:
            target = None
            op = "Observed Access"
            if e.features.get("target_object"):
                target = e.features["target_object"]
                op = "File/Reg Access"
            elif e.features.get("TargetFilename"):
                target = e.features["TargetFilename"]
                op = "File Read/Write"
            elif e.features.get("TargetObject"):
                target = e.features["TargetObject"]
                op = "Registry Access"
            elif e.features.get("network_endpoint"):
                target = e.features["network_endpoint"]
                op = "Network Connect"
            elif e.intent:
                intent_map = {
                    "RECON_USER_ARTIFACTS": ("Desktop / Documents / User Artifacts Search", "Directory Traversal"),
                    "CRED_BROWSER_STORE": ("AppData\\Local\\Google\\Chrome / Login Data", "Credential Store Read"),
                    "PERSIST_RUN_KEY": ("HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run", "Persistence Query"),
                    "C2_BEACON": ("Outbound HTTP/DNS C2 Traffic", "Beaconing / Check-in"),
                    "DISCOVERY_SYS_INFO": ("System Architecture & OS Profile", "Host Discovery"),
                }
                target, op = intent_map.get(e.intent, (f"Observed Intent: {e.intent}", "Attacker Behavior"))
            
            if target:
                key = ("semantic_event", target, op)
                if key not in seen_iocs:
                    seen_iocs.add(key)
                    iocs.append({"source": "semantic_event", "target": target, "operation": op})

        # Detection risk
        high_risk_mutations = [
            m.model_dump() for m in mutations if getattr(m, "plausibility_score", 1.0) < self.plausibility_warn_below
        ]

        # Policy decisions summary
        decision_items = []
        for d in decisions:
            decision_items.append({
                "decision_id": d.decision_id,
                "rule_id": d.rule_id,
                "verdict": d.verdict.value if hasattr(d.verdict, "value") else str(d.verdict),
                "action": d.action or "NONE",
                "rationale": d.rationale or "—"
            })

        raw_events = []
        if hasattr(self.event_repo, "get_raw_by_session"):
            try:
                raw_events = await self.event_repo.get_raw_by_session(session_id)
            except Exception:
                logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
                raw_events = []

        source_counts = {"Sysmon": 0, "Procmon": 0, "Tshark": 0}
        for r in raw_events:
            src = r.source.value if hasattr(r.source, "value") else str(r.source)
            src_upper = src.upper()
            if "SYSMON" in src_upper:
                source_counts["Sysmon"] += 1
            elif "PROCMON" in src_upper:
                source_counts["Procmon"] += 1
            elif "TSHARK" in src_upper or "WIRESHARK" in src_upper or "NETWORK" in src_upper:
                source_counts["Tshark"] += 1

        source_outcomes = {}
        for key, label in (("sysmon", "Sysmon"), ("procmon", "Procmon"), ("network", "Tshark")):
            outcome = session.source_outcomes.get(key)
            if outcome is None:
                source_outcomes[label] = {"source": label, "status": "UNVERIFIED", "events_count": source_counts[label]}
                continue
            # ADR-005 stores new raw events in JSONL, not the legacy raw
            # SQLite table. Lifecycle counts reflect the durable writer.
            source_counts[label] = outcome.events_count
            status = "FAILED" if outcome.errors else (
                "CAPTURED" if outcome.verified and outcome.capture_started and outcome.exported and outcome.parsed and outcome.events_count else
                "EMPTY" if outcome.verified and outcome.capture_started and outcome.exported and outcome.parsed else "UNVERIFIED"
            )
            source_outcomes[label] = {**outcome.model_dump(), "source": label, "status": status}

        failures = [{"type": f.code, "stage": f.stage, "detail": f.description} for f in session.failures]
        if session.error:
            failures.append({
                "type": "SessionError",
                "detail": session.error,
            })
        for m in mutations:
            status_val = m.status.value if hasattr(m.status, "value") else str(m.status)
            if status_val == "FAILED":
                failures.append({
                    "type": "MutationFailure",
                    "primitive": m.primitive,
                    "detail": m.error or getattr(m, "message", "Unknown mutation failure"),
                })

        data = {
            "session_id": session_id,
            "experiment_id": session.experiment_id,
            "arm": session.arm.value if hasattr(session.arm, "value") else str(session.arm),
            "status": session.status.value if hasattr(session.status, "value") else str(session.status),
            "error": session.error,
            "execution_pid": session.execution_pid,
            "execution_exit_code": session.execution_exit_code,
            "failures": failures,
            "source_outcomes": source_outcomes,
            "started_at": format_local_time(session.started_at),
            "ended_at": format_local_time(session.ended_at),
            "sample": session.sample.model_dump() if session.sample else {},
            "config": session.config.model_dump() if session.config else {},
            "timeline": timeline,
            "attck_coverage": attck_coverage,
            "iocs": iocs,
            "decisions": decision_items,
            "detection_risk": high_risk_mutations,
            "metrics": {
                "raw_events_count": max(session.metrics.raw_events, len(raw_events)),
                "events_count": len(events),
                "mutations_count": len(mutations),
                "decisions_count": len(decisions),
                "attck_count": len(attck_coverage),
                "failures_count": len(failures),
                "source_counts": source_counts,
            }
        }

        if format not in self.renderers:
            raise ValueError(f"Unsupported format {format}")
            
        return self.renderers[format].render(data)

    async def generate_comparison(self, experiment_id: str) -> str:
        all_sessions = await self.session_repo.list_all()
        exp_sessions = [s for s in all_sessions if s.experiment_id == experiment_id]
        
        control_session = next((s for s in exp_sessions if s.arm.value == "CONTROL" or str(s.arm) == "CONTROL"), None)
        treatment_session = next((s for s in exp_sessions if s.arm.value == "TREATMENT" or str(s.arm) == "TREATMENT"), None)

        if not control_session or not treatment_session:
            raise ValueError(f"Experiment {experiment_id} missing CONTROL or TREATMENT session")

        t_events = await self.event_repo.get_semantic_by_session(treatment_session.session_id)
        c_events = await self.event_repo.get_semantic_by_session(control_session.session_id)

        t_post_mutation = [e for e in t_events if e.caused_by_mutation is not None]
        c_post_mutation = [e for e in c_events if e.caused_by_mutation is not None]

        delta_semantic_events = len(t_post_mutation) - len(c_post_mutation)

        def extract_distinct(events):
            intents = {e.intent for e in events}
            networks = {e.features.get("network_endpoint") for e in events if "network_endpoint" in e.features}
            return intents, networks

        t_intents, t_networks = extract_distinct(t_post_mutation)
        c_intents, c_networks = extract_distinct(c_post_mutation)

        data = {
            "experiment_id": experiment_id,
            "control_session": control_session.session_id,
            "treatment_session": treatment_session.session_id,
            "delta_semantic_events": delta_semantic_events,
            "distinct_intents_yield": list(t_intents - c_intents),
            "distinct_networks_yield": list(t_networks - c_networks)
        }

        return self.renderers["json"].render(data)
