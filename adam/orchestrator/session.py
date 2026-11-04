"""
adam/orchestrator/session.py

SessionOrchestrator -- ARCHITECTURE.md section 6.1 (session lifecycle) and
section 8.4's bus subscription map ("Orchestrator | SessionLifecycle |
all"). Ties SandboxController, the collectors, and EventBus together into
one coordinated run: prepare -> arm -> detonate -> collect -> teardown,
producing a populated `artifacts/<session_id>/raw.jsonl` and a final
AnalysisSession. docs/dev-a-environment-and-roadmap.md Phase 8.

This module coordinates existing components; it does not reimplement any
of them. It calls SandboxController's already-tested methods, drains
already-tested BaseCollector subclasses via iter_events(), and publishes
onto an already-tested EventBus. Its own job is purely: ordering, the
collector-to-bus/persistence bridge the roadmap calls out explicitly (see
below), and guaranteed cleanup.

Step order matches the project's own stated Phase 8 execution model
exactly (config load/validate happens before this is ever called --
Settings() itself fails fast on construction per Milestone 4 -- so this
method starts from an already-validated `config: Settings`):

    3-5. controller.prepare()          (COLD -> RESTORING -> BOOTING -> READY,
                                         one call, per SandboxController's
                                         own design)
    6.   bus.start()
    7.   collector.start() for each collector, then spawn one pump task per
         collector (see _pump() below)
    8.   controller.arm(host_sample_path, guest_target_path)
    9.   controller.detonate(sample)
    10-11. (continuous, running in the background since step 7) each pump
         task drains its collector's iter_events(), writes every RawEvent to
         raw.jsonl FIRST, then publishes it onto the bus -- see _pump()'s
         docstring for why that order, not the reverse.
    12.  collector.stop() for each collector (lets each pump task's
         iter_events() drain the last buffered events and return)
    13.  bus.drain(timeout)
    14.  controller.teardown()
    15.  build and return AnalysisSession
    16.  guaranteed via one try/except/finally wrapping steps 3-14 -- see
         run_session()'s own docstring for the FAILED vs PARTIAL vs ABORTED
         distinction.

Collector-to-bus bridging (roadmap's own words, Phase 2 file list note):
"each collector's iter_events() yields RawEvents that get published onto
the bus by a thin wrapper in the orchestrator (Phase 8), not by the
collector itself calling bus.publish() directly, so collectors stay
unit-testable without a live bus." That thin wrapper is `_pump()` below.

Host-path gap, disclosed. The roadmap's own `SessionOrchestrator.run_session
(self, sample: SampleRef, config: Settings) -> AnalysisSession` signature
does not carry a host-side path to the sample file (`SampleRef` only has
sha256/md5/filename/size_bytes/file_type -- see adam/contracts/session.py),
but `SandboxController.arm()` needs a real host filesystem path to copy
from. This is the same category of gap already disclosed for `detonate()`
before Phase 2 existed (docs/implementation-audit.md, Phase 4 Deviations).
Resolved here with an explicit, required `host_sample_path` keyword-only
parameter on `run_session()` -- the CLI layer (adam/cli/run.py) has this
path directly from its own command-line argument, so this is a minimal,
necessary, disclosed deviation from the roadmap's literal two-argument
signature, not a guess at unspecified architecture.

Guest-telemetry-source gap -- RESOLVED (Phase 5). SysmonCollector/
ProcmonCollector/NetworkCollector each tail a HOST-side file path (an EVTX
file, a CSV export, a tshark -T ek export). Originally closed by
adam.sandbox.guest.agent.agent.GuestAgent (a host-orchestrated
VBoxManage-guestcontrol automation) via an optional `guest_agent`
constructor parameter that drives Procmon/tshark capture around detonate()
and exports Sysmon/Procmon/tshark telemetry to real host paths afterward,
from which this class builds and runs collectors automatically -- see
run_session()'s own step-by-step comments below for exactly where.

Phase 5 revision -- GuestChannel interface. The constructor parameter is
now typed `GuestChannel | None` (adam/sandbox/guest/channel.py), not
`GuestAgent | None` -- SessionOrchestrator calls only the three methods
that Protocol defines (verify_tools/start_captures/stop_export_and_fetch)
and does not know or care whether the concrete object passed in is
VBoxGuestChannel (wrapping the original, untouched GuestAgent) or
HTTPGuestChannel (talking to the guest-resident PowerShell HTTP agent) --
Runner (adam/orchestrator/runner.py) decides which one to construct, based
on `Settings.guest_backend`. The parameter name stays `guest_agent` for
backward compatibility with every existing caller/test that already
constructs a SessionOrchestrator with this keyword; only its accepted
type widened. SessionOrchestrator still does not duplicate the backend's
own work, and still reuses the exact same collector classes and _pump()
bridge as the pre-existing, constructor-injected `collectors` path,
unchanged. `guest_agent=None` (the default) preserves this class's
original behavior byte-for-byte, so every existing offline verification
scenario built against constructor-injected FakeCollectors continues to
exercise the same code path it always has.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adam.collectors.base import BaseCollector
from adam.collectors.network import NetworkCollector
from adam.collectors.parsers.evtx import _parse_system_time, category_for_sysmon_event
from adam.collectors.procmon import ProcmonCollector
from adam.collectors.sysmon import SysmonCollector
from adam.common.bus import EventBus
from adam.common.config import Settings
from adam.contracts.enums import Arm, NetworkMode, SessionStatus, Source
from adam.contracts.envelope import Envelope
from adam.contracts.raw_event import ProcessInfo, RawEvent
from adam.contracts.session import (
    AnalysisSession,
    SampleRef,
    SessionConfig,
    SessionFailure,
    SessionLifecycle,
    SessionMetrics,
    SourceOutcome,
)
from adam.orchestrator.experiment import resolve_experiment
from adam.orchestrator.persistence import RawEventWriter
from adam.sandbox.controller import SandboxController
from adam.sandbox.guest.channel import GuestChannel, TelemetryArtifacts
from adam.sandbox.guest.http_models import SysmonRecordItem
from adam.sandbox.state import SandboxOperationError

logger = logging.getLogger(__name__)


def build_collectors_from_telemetry(
    session_id: str,
    artifacts: TelemetryArtifacts,
    *,
    watermark_record_id: int | None = None,
) -> list[BaseCollector]:
    """
    Step 10 of the Phase 5 guest-agent lifecycle: construct whichever
    concrete collectors have a real, exported host path to tail. Mirrors
    adam.orchestrator.runner.Runner._build_collectors()'s "only construct a
    collector if its path is not None" rule exactly -- that method now
    delegates here instead of duplicating the logic, so a CLI-override path
    and a GuestAgent-exported path are wired into a collector identically.
    Kept in this module (not runner.py) so SessionOrchestrator can call it
    directly once GuestAgent.stop_export_and_fetch() returns, mid-session,
    without runner.py needing visibility into a decision it isn't present
    for.
    """
    collectors: list[BaseCollector] = []
    if artifacts.sysmon_evtx_path is not None:
        collectors.append(
            SysmonCollector(
                artifacts.sysmon_evtx_path,
                session_id=session_id,
                watermark_record_id=watermark_record_id,
            )
        )
    if artifacts.procmon_csv_path is not None:
        collectors.append(ProcmonCollector(artifacts.procmon_csv_path, session_id=session_id))
    if artifacts.network_ek_json_path is not None:
        collectors.append(NetworkCollector(artifacts.network_ek_json_path, session_id=session_id))
    return collectors


def _sysmon_int(data: dict[str, object], key: str) -> int:
    """Best-effort integer conversion for Sysmon's string-valued fields."""
    try:
        return int(str(data.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def raw_event_from_sysmon_item(item: SysmonRecordItem, session_id: str) -> RawEvent:
    """
    Convert one live Sysmon record into the shared RawEvent contract.

    The same EventID -> category mapping as the offline EVTX parser is
    used, and process fields are populated for every event type. Registry,
    file, and DNS records keep their identifying fields in
    ``attributes`` so Fusion detectors can see them during the run.
    """
    occurred_at = _parse_system_time(item.time_created)
    observed_at = datetime.now(UTC)
    category = category_for_sysmon_event(item.event_id)
    data = dict(item.data or {})

    image = str(data.get("Image") or "").strip()
    command_line = str(data.get("CommandLine") or "-")
    data["command_line"] = command_line
    if image:
        data["process_name"] = image

    proc_info = None
    if image:
        proc_info = ProcessInfo(
            pid=_sysmon_int(data, "ProcessId"),
            ppid=_sysmon_int(data, "ParentProcessId"),
            image=image,
            command_line=command_line,
            integrity_level=str(data.get("IntegrityLevel") or "-"),
            user=str(data.get("User") or "-"),
            guid=str(data.get("ProcessGuid") or "-"),
        )

    return RawEvent(
        event_id=f"raw_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        source=Source.SYSMON,
        source_event_id=item.record_id,
        category=category,
        occurred_at=occurred_at,
        observed_at=observed_at,
        process=proc_info,
        attributes=data,
        raw_ref=f"guest:sysmon:{item.record_id}",
    )


def new_session_id() -> str:
    """
    Temporary session-ID generator, same disclosed-placeholder status as
    the `uuid.uuid4()`-based IDs in adam/collectors/parsers/evtx.py and
    pml.py: adam/common/ids.py (a real new_id(prefix) generator) does not
    exist yet (tracked in docs/remaining-work-plan.md). Formatted to match
    ARCHITECTURE.md section 7.1's own example style (`sess_YYYY_MM_DD_xxxx`)
    for readability in artifact directory names and logs.
    """
    now = datetime.now(UTC)
    return f"sess_{now:%Y_%m_%d}_{uuid.uuid4().hex[:8]}"


class SessionOrchestrator:
    """
    Coordinates one SandboxController, one EventBus, and a fixed set of
    already-constructed collectors through a full session. See module
    docstring for the exact step order and the two disclosed gaps
    (host_sample_path, guest-side collector sourcing) this class does not
    attempt to solve.
    """

    def __init__(
        self,
        controller: SandboxController,
        bus: EventBus,
        collectors: Sequence[BaseCollector],
        *,
        artifacts_dir: str | Path = "artifacts",
        guest_target_path_template: str = "C:\\ADAM\\samples\\{filename}",
        bus_drain_timeout: float = 5.0,
        post_detonation_drain_seconds: float = 0.5,
        guest_agent: GuestChannel | None = None,
        live_poll_enabled: bool = True,
    ) -> None:
        self._controller = controller
        self._bus = bus
        self._collectors = list(collectors)
        self._artifacts_dir = Path(artifacts_dir)
        self._guest_target_path_template = guest_target_path_template
        self._bus_drain_timeout = bus_drain_timeout
        self._post_detonation_drain_seconds = post_detonation_drain_seconds
        # Phase 5 addition. None (the default) preserves this class's
        # pre-Phase-5 behavior exactly -- see module docstring's
        # "Guest-telemetry-source gap -- RESOLVED" note.
        self._guest_agent = guest_agent
        # Tests and offline replay can disable polling without changing
        # [live] configuration; both this flag and live.enabled must be true.
        self._live_poll_enabled = live_poll_enabled
        self._session_id: str | None = None
        self._seen_event_keys: set[tuple[str, str, int, Any]] = set()
        self._ingest_lock = asyncio.Lock()
        self._published_event_keys = set()
        self._sysmon_generation = 0
        self._failures: list[SessionFailure] = []
        self._source_outcomes: dict[str, SourceOutcome] = {}

    def _record_failure(self, stage: str, exc: Exception) -> None:
        failure = SessionFailure(stage=stage, code=type(exc).__name__, description=str(exc))
        if failure not in self._failures:
            self._failures.append(failure)
            if stage in self._source_outcomes:
                self._source_outcomes[stage].errors.append(str(exc))

    def _guest_target_path_for(self, sample: SampleRef) -> str:
        return self._guest_target_path_template.format(filename=sample.filename)

    async def ingest_raw_event(
        self,
        event: RawEvent,
        writer: RawEventWriter,
        channel: str = "sysmon",
        generation: int = 0,
    ) -> bool:
        """
        Single unified ingestion path for both live polling and exported collectors.

        1. Validates session ownership (event.session_id == self._session_id).
        2. Deduplicates using (session_id, channel, generation, event.source_event_id).
        3. Persists raw event to raw.jsonl via await writer.write(event).
        4. Publishes Envelope[RawEvent] to the event bus.

        Returns True if accepted and published, False if dropped as duplicate.
        """
        if self._session_id is not None and event.session_id != self._session_id:
            raise ValueError(
                f"session ownership validation failed: event session_id {event.session_id!r} "
                f"does not match orchestrator session_id {self._session_id!r}"
            )
        if self._session_id is None:
            self._session_id = event.session_id

        dedup_key = (event.session_id, channel, generation, event.source_event_id)
        async def commit() -> bool:
            async with self._ingest_lock:
                if dedup_key in self._published_event_keys:
                    return False
                if dedup_key not in self._seen_event_keys:
                    try:
                        await writer.write(event)
                    except Exception as exc:
                        self._record_failure(channel, exc)
                        logger.exception("Raw event persistence failed")
                        raise
                    self._seen_event_keys.add(dedup_key)
                    outcome = self._source_outcomes.get(channel)
                    if outcome is not None:
                        outcome.events_count += 1
                envelope: Envelope[RawEvent] = Envelope(
                    message_id=f"msg_{uuid.uuid4().hex}", message_type="RawEvent",
                    session_id=event.session_id, correlation_id=event.event_id,
                    emitted_at=datetime.now(UTC), emitter=f"collector.{channel}", payload=event,
                )
                try:
                    await self._bus.publish(envelope)
                except Exception as exc:
                    self._record_failure("publish", exc)
                    logger.exception("Durable raw event could not be published")
                    raise
                self._published_event_keys.add(dedup_key)
                return True

        # Complete disk persistence AND publication before releasing a cancelled
        # caller, so a worker thread cannot race the next live/exported ingest.
        task = asyncio.create_task(commit())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _publish_lifecycle(self, session_id: str, status: SessionStatus, detail: str) -> None:
        lifecycle = SessionLifecycle(
            session_id=session_id, status=status, detail=detail, occurred_at=datetime.now(UTC)
        )
        envelope: Envelope[SessionLifecycle] = Envelope(
            message_id=f"msg_{uuid.uuid4().hex}",
            message_type="SessionLifecycle",
            session_id=session_id,
            correlation_id=session_id,
            emitted_at=datetime.now(UTC),
            emitter="orchestrator.session",
            payload=lifecycle,
        )
        try:
            await self._bus.publish(envelope)
        except Exception as exc:
            self._record_failure("lifecycle", exc)
            logger.exception("failed to publish SessionLifecycle(%s) for session=%s", status.value, session_id)

    async def _pump(self, collector: BaseCollector, session_id: str, writer: RawEventWriter) -> None:
        """
        Drains one collector's iter_events() for the lifetime of the session,
        routing each RawEvent through the unified ingest_raw_event() pipeline.
        """
        async for event in collector.iter_events():
            try:
                await self.ingest_raw_event(
                    event,
                    writer,
                    channel=collector.source_name,
                    generation=self._sysmon_generation if collector.source_name == "sysmon" else 0,
                )
            except Exception as exc:
                self._record_failure(collector.source_name, exc)
                logger.exception(
                    "session=%s collector=%s error during ingest_raw_event",
                    session_id,
                    collector.source_name,
                )

    async def _run_live_sysmon_poll(
        self,
        session_id: str,
        stop_event: asyncio.Event,
        *,
        writer: RawEventWriter | None = None,
        initial_record_id: int = -1,
        event_ids: str = "1,3,5,11,12,13,22",
        poll_interval_s: float = 1.0,
        batch_size: int = 100,
        max_batches_per_cycle: int = 10,
    ) -> None:
        """
        Poll the guest agent's incremental Sysmon endpoint during DETONATING.

        Each cycle drains up to ``max_batches_per_cycle`` batches of at most
        ``batch_size`` records, publishing every record as a RawEvent. A
        baseline failure disables live polling for the session: falling
        back to record_id 0 would replay all pre-detonation history and
        attribute it to the sample.
        """
        last_record_id = initial_record_id
        log_generation = 0
        if last_record_id < 0:
            try:
                baseline_res = await self._guest_agent.get_sysmon_events_since(
                    record_id=-1, event_ids=event_ids
                )
                last_record_id = baseline_res[0] if isinstance(baseline_res, tuple) else getattr(baseline_res, "last_record_id", -1)
                logger.info(
                    "session=%s live sysmon baseline record_id=%d established",
                    session_id,
                    last_record_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "session=%s failed to query sysmon baseline -- live polling "
                    "disabled for this session (not replaying history): %s",
                    session_id,
                    exc,
                )
                return
        else:
            logger.info(
                "session=%s using pre-established live sysmon baseline record_id=%d",
                session_id,
                last_record_id,
            )

        while not stop_event.is_set():
            try:
                await asyncio.sleep(poll_interval_s)
                if stop_event.is_set():
                    break

                for batch_index in range(max_batches_per_cycle):
                    previous_id = last_record_id
                    res = await self._guest_agent.get_sysmon_events_since(
                        record_id=last_record_id, event_ids=event_ids
                    )
                    next_id, records = res[0], res[1]

                    is_reset = getattr(res, "log_reset", False) or (last_record_id > 0 and next_id < last_record_id)
                    if is_reset:
                        log_generation += 1
                        self._sysmon_generation = log_generation
                        logger.info(
                            "session=%s sysmon log reset detected (next_id=%d, last_id=%d), incremented log_generation to %d",
                            session_id,
                            next_id,
                            last_record_id,
                            log_generation,
                        )

                    last_record_id = next_id

                    for record in records:
                        try:
                            raw_event = raw_event_from_sysmon_item(record, session_id=session_id)
                        except Exception:
                            logger.exception(
                                "session=%s failed to convert live Sysmon record_id=%s EventID=%s",
                                session_id,
                                record.record_id,
                                record.event_id,
                            )
                            continue

                        wall_clock_now = datetime.now(UTC)
                        gap = (wall_clock_now - raw_event.occurred_at).total_seconds()
                        logger.info(
                            "[LIVE_POLL] record_id=%d EventID=%d TimeCreated=%s "
                            "ObservedAt=%s Gap=%.3fs Image=%s PID=%s",
                            record.record_id,
                            record.event_id,
                            record.time_created,
                            wall_clock_now.isoformat(),
                            gap,
                            raw_event.process.image if raw_event.process else "N/A",
                            raw_event.process.pid if raw_event.process else "N/A",
                        )

                        if writer is not None:
                            accepted = await self.ingest_raw_event(
                                raw_event,
                                writer,
                                channel="sysmon",
                                generation=log_generation,
                            )
                            if accepted and "sysmon" in self._source_outcomes:
                                self._source_outcomes["sysmon"].live_events_count += 1
                        else:
                            envelope: Envelope[RawEvent] = Envelope(
                                message_id=f"msg_{uuid.uuid4().hex}",
                                message_type="RawEvent",
                                session_id=session_id,
                                correlation_id=raw_event.event_id,
                                emitted_at=wall_clock_now,
                                emitter="collector.sysmon.live",
                                payload=raw_event,
                            )
                            await self._bus.publish(envelope)

                    # Stop when the batch was short (the guest drained its
                    # current backlog) or the record ID did not advance (unless reset).
                    if len(records) < batch_size or (next_id <= previous_id and not is_reset):
                        break
                    if batch_index == max_batches_per_cycle - 1:
                        logger.warning(
                            "session=%s live sysmon poll reached the %d-batch cap; "
                            "the poller is falling behind the event stream",
                            session_id,
                            max_batches_per_cycle,
                        )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._record_failure("sysmon", exc)
                logger.exception("session=%s error in live sysmon poll cycle", session_id)

    async def run_session(
        self,
        sample: SampleRef,
        config: Settings,
        *,
        host_sample_path: str,
        session_id: str | None = None,
        experiment_id: str = "adhoc",
        arm: Arm | None = None,
        deception_enabled: bool | None = None,
        sample_timeout_seconds: int = 300,
    ) -> AnalysisSession:
        """
        Runs one full session. See module docstring for step order.

        `session_id`: normally left as None, in which case a fresh ID is
        generated internally (new_session_id()). Accepted as an explicit
        override because the injected `collectors` were already
        constructed with a `session_id` baked into their own constructor
        (SysmonCollector/ProcmonCollector/NetworkCollector all tag every
        RawEvent they produce with the session_id they were built with) --
        a caller building collectors ahead of time (see runner.py) must
        generate the ID first via new_session_id(), pass it to both the
        collectors' constructors and here, so every RawEvent's
        `session_id` field and this session's own `session_id` agree. If
        left None, the auto-generated ID obviously won't match whatever
        (if anything) was baked into already-constructed collectors --
        the caller's responsibility to keep these consistent when
        pre-building collectors, same as any other dependency-injected
        component.

        Status/error semantics on the returned AnalysisSession:
          - COMPLETED: every step succeeded.
          - FAILED: something failed before collectors were ever started
            (prepare() itself, or bus.start()) -- no telemetry could
            possibly have been captured, so there is nothing "partial"
            about the result.
          - PARTIAL: something failed after collectors were started
            (arm(), detonate(), or anything else mid-session) --
            ARCHITECTURE.md section 14.4: "A session that errored still
            produces a report -- marked PARTIAL. Partial results are still
            evidence." Whatever raw.jsonl accumulated before the failure
            is retained.
          - ABORTED: the session was cancelled (Ctrl-C / task
            cancellation). Deliberately NOT re-raised as CancelledError --
            see the try/except below for why this method's contract is to
            always return a well-formed AnalysisSession, including on
            cancellation, matching this project's requirement that a
            session "exit cleanly even if failures occur."

        teardown() is called unconditionally in the finally block and never
        raises (SandboxController's own guarantee, section 14.4) -- the VM
        is restored to `clean` regardless of what happened above it.

        Phase 5 (guest_agent, constructor parameter): when supplied, this
        method additionally starts Procmon/tshark captures after arm() and,
        after detonate() completes, stops those captures, exports Sysmon/
        Procmon/tshark telemetry, copies it to the host, and automatically
        builds+starts the matching collectors -- see the inline comments
        around arm()/detonate() below for exactly where. A source already
        covered by a constructor-injected collector (the pre-Phase-5 CLI-
        override path) is never re-captured. `collectors_started` (and
        therefore the PARTIAL-vs-FAILED distinction above) is set True by
        either path, whichever starts a collector first.
        """
        if session_id is None:
            session_id = new_session_id()
        self._session_id = session_id
        self._seen_event_keys.clear()
        self._published_event_keys.clear()
        self._failures.clear()
        self._source_outcomes.clear()
        self._sysmon_generation = 0
        started_at = datetime.now(UTC)
        guest_target_path = self._guest_target_path_for(sample)

        experiment = resolve_experiment(
            config,
            arm=arm,
            deception_enabled=deception_enabled,
        )
        vm_profile_name = getattr(self._controller, "vm_profile", None) or config.sandbox.vm_name
        session_config = SessionConfig(
            deception_enabled=experiment.deception_enabled,
            policy_ruleset=experiment.policy_ruleset,
            vm_profile=vm_profile_name,
            timeout_seconds=sample_timeout_seconds,
            network_mode=NetworkMode.HOST_ONLY,
        )

        artifact_path = self._artifacts_dir / session_id / "raw.jsonl"
        writer = RawEventWriter(artifact_path)

        status = SessionStatus.PENDING
        error: str | None = None
        collectors_started = False
        pump_tasks: list[asyncio.Task[None]] = []
        # Phase 5: collectors GuestAgent builds AFTER detonate() (once
        # telemetry has actually been exported to real host paths), kept
        # separate from self._collectors/pump_tasks above -- which remain
        # exactly the pre-Phase-5 constructor-injected, started-before-
        # detonate path -- and merged with them only in the finally block's
        # stop/await loops below, so neither path's own logic changes.
        guest_collectors: list[BaseCollector] = []
        guest_pump_tasks: list[asyncio.Task[None]] = []

        await writer.open()
        await self._publish_lifecycle(session_id, status, "session created")

        try:
            status = SessionStatus.PREPARING
            await self._publish_lifecycle(session_id, status, "restoring snapshot and booting guest")
            await self._controller.prepare()

            await self._bus.start()

            for collector in self._collectors:
                await collector.start()
                # Set True as soon as the FIRST collector starts, not only
                # after the whole loop succeeds: if a later collector's
                # start() raises, the ones that already started may have
                # captured something real, so the failure must still be
                # classified PARTIAL, not FAILED. stop() in the finally
                # block below is safe to call on every collector regardless
                # (BaseCollector.stop() is a no-op if the collector's task
                # was never created).
                collectors_started = True
                # Pump task spawned immediately after each collector starts,
                # not batched after the whole loop: if a LATER collector's
                # start() raises, this one's already-buffered events must
                # still get drained and persisted, not silently lost.
                pump_tasks.append(
                    asyncio.create_task(
                        self._pump(collector, session_id, writer),
                        name=f"adam.orchestrator.pump.{collector.source_name}",
                    )
                )

            # Tool verification happens BEFORE arm(): a strict
            # tools_required=true session must fail before the sample is
            # staged into the guest, not after the VM has been armed.
            if self._guest_agent is not None:
                try:
                    availability = await self._guest_agent.verify_tools()
                except Exception as exc:
                    self._record_failure("verify_tools", exc)
                    logger.exception(
                        "session=%s guest_agent.verify_tools raised unexpectedly -- continuing anyway",
                        session_id,
                    )
                else:
                    tool_settings = config.http_guest if config.guest_backend == "http" else config.guest_tools
                    required = bool(tool_settings.tools_required)
                    effective = {
                        "sysmon": availability.sysmon_log_available,
                        "procmon": availability.procmon_available,
                        "network": availability.tshark_available,
                    }
                    diagnostics_method = getattr(self._guest_agent, "get_tool_diagnostics", None)
                    if diagnostics_method is not None:
                        try:
                            diagnostics = await diagnostics_method()
                            tools = diagnostics.tools
                            effective["sysmon"] &= bool(tools.get("sysmon", {}).get("available"))
                            effective["procmon"] &= bool(tools.get("procmon", {}).get("available"))
                            effective["network"] &= bool(tools.get("tshark", {}).get("available")) and tools.get("npcap", {}).get("service_status") == "Running"
                        except Exception as exc:
                            logger.exception("Guest functional tool diagnostics failed")
                            self._record_failure("verify_tools", exc)
                            effective = dict.fromkeys(effective, False)
                    for name, available in (
                        ("sysmon", effective["sysmon"]),
                        ("procmon", effective["procmon"]),
                        ("network", effective["network"]),
                    ):
                        configured = bool({"sysmon": tool_settings.sysmon_log, "procmon": tool_settings.procmon_path, "network": tool_settings.tshark_path}[name])
                        self._source_outcomes[name] = SourceOutcome(enabled=configured, required=required and configured, verified=available)
                        if configured and not available:
                            self._record_failure(name, RuntimeError("Guest source unavailable"))
                    functional_missing = [name for name, outcome in self._source_outcomes.items() if outcome.required and not outcome.verified]
                    if availability.required_missing or functional_missing:
                        missing = ", ".join(dict.fromkeys([*availability.required_missing, *functional_missing]))
                        raise SandboxOperationError(
                            "verify_tools",
                            RuntimeError(
                                f"Required guest tools are unavailable: {missing}"
                            ),
                        )

            await self._controller.arm(host_sample_path, guest_target_path)

            # Phase 5, steps 2-3: start Procmon/tshark captures inside the
            # guest, detached, before the sample runs. Sources already
            # covered by a constructor-injected collector (a CLI override
            # path -- see adam/orchestrator/runner.py) are skipped so
            # GuestAgent never captures something that would just be
            # discarded in favor of the override.
            if self._guest_agent is not None:
                overridden_sources = {c.source_name for c in self._collectors}
                try:
                    capture_status = await self._guest_agent.start_captures(
                        session_id,
                        capture_procmon="procmon" not in overridden_sources,
                        capture_network="network" not in overridden_sources,
                    )
                    if isinstance(capture_status, dict):
                        for name, started in capture_status.items():
                            if name in self._source_outcomes:
                                self._source_outcomes[name].capture_started = started
                                if not started and name not in overridden_sources:
                                    self._record_failure(name, RuntimeError("Capture did not start"))
                except Exception as exc:
                    self._record_failure("capture_start", exc)
                    # GuestAgent's own methods are documented to never raise
                    # (partial-telemetry guarantee) -- guarded anyway, same
                    # defense-in-depth convention as teardown()/
                    # _publish_lifecycle() elsewhere in this method.
                    logger.exception(
                        "session=%s guest_agent.start_captures raised unexpectedly -- "
                        "continuing detonation without guest-driven captures",
                        session_id,
                    )

            # Pre-detonation Sysmon watermark: always establish before detonation, bound to session and channel
            watermark_record_id: int | None = None
            if self._guest_agent is not None and hasattr(self._guest_agent, "get_sysmon_events_since"):
                try:
                    wm_res = await self._guest_agent.get_sysmon_events_since(record_id=-1, event_ids="1,3,5,11,12,13,22")
                    wm_id = wm_res[0] if isinstance(wm_res, tuple) else getattr(wm_res, "last_record_id", -1)
                    if wm_id >= 0:
                        watermark_record_id = wm_id
                        if "sysmon" in self._source_outcomes:
                            self._source_outcomes["sysmon"].capture_started = True
                        logger.info(
                            "session=%s established pre-detonation Sysmon watermark record_id=%d",
                            session_id,
                            watermark_record_id,
                        )
                except Exception as exc:  # noqa: BLE001
                    self._record_failure("sysmon", exc)
                    logger.warning(
                        "session=%s failed to query pre-detonation Sysmon watermark: %s",
                        session_id,
                        exc,
                    )

            status = SessionStatus.RUNNING
            await self._publish_lifecycle(session_id, status, "detonating sample")

            live_poll_task: asyncio.Task[None] | None = None
            stop_live_poll = asyncio.Event()
            live = config.live
            if (
                self._live_poll_enabled
                and live.enabled
                and self._guest_agent is not None
                and hasattr(self._guest_agent, "get_sysmon_events_since")
            ):
                initial_record_id = watermark_record_id if watermark_record_id is not None else -1
                if initial_record_id < 0:
                    try:
                        base_res = await self._guest_agent.get_sysmon_events_since(
                            record_id=-1, event_ids=live.sysmon_event_ids
                        )
                        initial_record_id = base_res[0] if isinstance(base_res, tuple) else getattr(base_res, "last_record_id", -1)
                        if initial_record_id >= 0:
                            watermark_record_id = initial_record_id
                        logger.info(
                            "session=%s live sysmon pre-detonation baseline record_id=%d",
                            session_id,
                            initial_record_id,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "session=%s failed to query sysmon pre-detonation baseline "
                            "-- live polling disabled for this session (not replaying history): %s",
                            session_id,
                            exc,
                        )
                if initial_record_id >= 0:
                    live_poll_task = asyncio.create_task(
                        self._run_live_sysmon_poll(
                            session_id,
                            stop_live_poll,
                            writer=writer,
                            initial_record_id=initial_record_id,
                            event_ids=live.sysmon_event_ids,
                            poll_interval_s=live.poll_interval_s,
                            batch_size=live.batch_size,
                            max_batches_per_cycle=live.max_batches_per_cycle,
                        ),
                        name=f"adam.orchestrator.live_sysmon_poll.{session_id}",
                    )

            try:
                await self._controller.detonate(sample)
            except Exception as exc:
                logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
                self._record_failure("detonate", exc)
            finally:
                if live_poll_task is not None:
                    stop_live_poll.set()
                    try:
                        await asyncio.wait_for(live_poll_task, timeout=2.0)
                    except (TimeoutError, asyncio.CancelledError):
                        live_poll_task.cancel()
                        try:
                            await live_poll_task
                        except asyncio.CancelledError:
                            pass

            # Give constructor-injected collectors a short grace period to
            # pick up trailing telemetry (e.g. process-exit events) before
            # stop() cuts them off in the finally block below. Not used by
            # guest-driven collectors below -- those don't exist yet at
            # this point, and get their own, separate grace sleep once
            # they're started.
            await asyncio.sleep(self._post_detonation_drain_seconds)

            # Phase 5, steps 6-10: stop captures, export telemetry in-guest,
            # copy it to the host artifact directory, and automatically
            # build+start the matching collectors -- no CLI arguments
            # required (this phase's own stated goal). Sources already
            # covered by a CLI override are skipped, same reasoning as
            # start_captures() above.
            if self._guest_agent is not None:
                overridden_sources = {c.source_name for c in self._collectors}
                try:
                    artifacts = await self._guest_agent.stop_export_and_fetch(
                        session_id,
                        self._artifacts_dir / session_id,
                        export_sysmon="sysmon" not in overridden_sources,
                        export_procmon="procmon" not in overridden_sources,
                        export_network="network" not in overridden_sources,
                    )
                except Exception:
                    logger.exception(
                        "session=%s guest_agent.stop_export_and_fetch raised unexpectedly -- "
                        "no guest-driven telemetry available this session",
                        session_id,
                    )
                    artifacts = TelemetryArtifacts()

                collector_artifacts = artifacts
                if config.guest_backend == "http" and watermark_record_id is None:
                    self._record_failure("sysmon", RuntimeError("No session watermark; historical Sysmon export retained but not ingested"))
                    collector_artifacts = TelemetryArtifacts(
                        sysmon_evtx_path=None,
                        procmon_csv_path=artifacts.procmon_csv_path,
                        network_ek_json_path=artifacts.network_ek_json_path,
                    )
                guest_collectors = build_collectors_from_telemetry(
                    session_id, collector_artifacts,
                    watermark_record_id=watermark_record_id if self._sysmon_generation == 0 else None,
                )
                for name, path in (
                    ("sysmon", artifacts.sysmon_evtx_path),
                    ("procmon", artifacts.procmon_csv_path),
                    ("network", artifacts.network_ek_json_path),
                ):
                    outcome = self._source_outcomes.get(name)
                    if outcome is not None and outcome.enabled and name not in overridden_sources:
                        outcome.exported = bool(path and Path(path).is_file())
                        if not outcome.exported:
                            self._record_failure(name, RuntimeError("No exported artifact"))
                for collector in guest_collectors:
                    await collector.start()
                    collectors_started = True  # same reasoning as the constructor-injected loop above
                    guest_pump_tasks.append(
                        asyncio.create_task(
                            self._pump(collector, session_id, writer),
                            name=f"adam.orchestrator.pump.{collector.source_name}",
                        )
                    )

                if guest_collectors:
                    for collector in guest_collectors:
                        await asyncio.wait_for(collector.read_complete.wait(), timeout=120)
                        outcome = self._source_outcomes.get(collector.source_name)
                        if collector.source_name == "procmon" and not collector._header_validated:
                            self._record_failure("procmon", RuntimeError("Exported CSV has no valid header"))
                        if outcome is not None:
                            outcome.parsed = not collector.errors
                    # The exported files are already complete and static by
                    # this point -- this sleep only needs to cover one poll
                    # cycle (each collector's default poll_interval is
                    # 0.1s) so the first (and only) poll ingests everything,
                    # not a live-tailing grace period the way the sleep
                    # above is for the constructor-injected path.
                    await asyncio.sleep(self._post_detonation_drain_seconds)

            status = SessionStatus.COMPLETED

        except asyncio.CancelledError:
            status = SessionStatus.ABORTED
            error = "session cancelled (Ctrl-C / task cancellation)"
            logger.warning("session=%s cancelled", session_id)
        except Exception as exc:
            self._record_failure("execution", exc)
            status = SessionStatus.PARTIAL if collectors_started else SessionStatus.FAILED
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("session=%s failed during %s", session_id, status.value)
        finally:
            # self._collectors (constructor-injected, started before
            # detonate) and guest_collectors (Phase 5, started after
            # detonate/export/fetch -- empty if guest_agent is None or
            # produced no telemetry) are stopped and drained together here;
            # neither path's own start-up logic above needed to change.
            for collector in (*self._collectors, *guest_collectors):
                try:
                    await collector.stop()
                    if getattr(collector, "dropped", 0):
                        self._record_failure(collector.source_name, RuntimeError("Collector dropped buffered events"))
                    for message in getattr(collector, "errors", []):
                        self._record_failure(collector.source_name, RuntimeError(message))
                except Exception as exc:
                    self._record_failure("collector_stop", exc)
                    logger.exception("session=%s collector=%s failed to stop", session_id, collector.source_name)

            for task in (*pump_tasks, *guest_pump_tasks):
                try:
                    await task
                except Exception as exc:
                    self._record_failure("ingestion", exc)
                    logger.exception("session=%s a pump task raised unexpectedly", session_id)

            # Drain analysis before cleanup, but publish no terminal success yet.
            try:
                await self._bus.drain(timeout=self._bus_drain_timeout)
                for message in getattr(self._bus, "errors", []):
                    self._record_failure("bus_drain", RuntimeError(message))
            except Exception as exc:
                self._record_failure("bus_drain", exc)
                logger.exception("Session bus drain failed")

            # Remove the guest proxy/hosts redirect while the VM is still
            # running and the guest agent is reachable; the interceptor
            # itself is stopped by Runner after this returns.
            try:
                reset_redirect = getattr(self._controller, "reset_network_redirect", None)
                if reset_redirect is not None:
                    await reset_redirect()
            except Exception as exc:
                self._record_failure("redirect_cleanup", exc)
                logger.exception("session=%s guest network redirect cleanup failed", session_id)

            # SandboxController.teardown() is documented to never raise
            # (ARCHITECTURE.md section 14.4) -- guarded anyway, defense in
            # depth, consistent with every other cleanup step above.
            try:
                await self._controller.teardown()
            except Exception as exc:
                self._record_failure("teardown", exc)
                logger.exception("session=%s controller teardown raised unexpectedly", session_id)

            try:
                await writer.close()
            except Exception as exc:
                logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
                self._record_failure("writer_close", exc)

        if self._failures and status != SessionStatus.ABORTED:
            status = SessionStatus.PARTIAL if writer.count else SessionStatus.FAILED
            error = "; ".join(f"{f.stage}: {f.description}" for f in self._failures)

        # Cleanup may change the outcome. Restart consumers solely to deliver
        # the final lifecycle record, after all failure-producing stages finish.
        try:
            await self._bus.start()
            await self._publish_lifecycle(session_id, status, error or "session finished")
            await self._bus.drain(timeout=self._bus_drain_timeout)
        except Exception as exc:
            self._record_failure("final_lifecycle", exc)
            logger.exception("Final lifecycle delivery failed")
            if status != SessionStatus.ABORTED:
                status = SessionStatus.PARTIAL if writer.count else SessionStatus.FAILED
                error = "; ".join(f"{f.stage}: {f.description}" for f in self._failures)

        ended_at = datetime.now(UTC)
        execution = getattr(self._controller, "last_detonation_result", None)
        execution_pid = getattr(execution, "pid", None)
        execution_exit_code = getattr(execution, "return_code", None)

        return AnalysisSession(
            session_id=session_id,
            experiment_id=experiment_id,
            arm=experiment.arm,
            sample=sample,
            config=session_config,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            metrics=SessionMetrics(raw_events=writer.count),
            error=error,
            source_outcomes=self._source_outcomes,
            failures=self._failures,
            execution_pid=execution_pid if isinstance(execution_pid, int) else None,
            execution_exit_code=execution_exit_code if isinstance(execution_exit_code, int) else None,
        )
