from __future__ import annotations

import hashlib
import pickle
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .correlate import EventCorrelator
from .detectors.collection import CollectionDetector
from .detectors.command_and_control import CommandAndControlDetector
from .detectors.credential_access import CredentialAccessDetector
from .detectors.defense_evasion import DefenseEvasionDetector
from .detectors.exfiltration import ExfiltrationDetector
from .detectors.impact import ImpactDetector
from .detectors.lateral_movement import LateralMovementDetector
from .detectors.persistence import PersistenceDetector
from .detectors.privilege_escalation import PrivilegeEscalationDetector
from .detectors.recon import ReconDetector
from .detectors.registry import DetectorRegistry
from .models import FusionResult, RawEvent
from .normalise import EventNormalizer
from .process_tree import ProcessTree
from .window import SlidingWindow

_SCORED_DETECTORS = frozenset({
    CollectionDetector, CommandAndControlDetector, CredentialAccessDetector,
    DefenseEvasionDetector, ExfiltrationDetector, ImpactDetector,
    LateralMovementDetector, PrivilegeEscalationDetector,
})
_ALL_CANDIDATES_MATCH = (_SCORED_DETECTORS - {CommandAndControlDetector}) | {ReconDetector, PersistenceDetector}


@dataclass
class _Group:
    events: deque = field(default_factory=deque)
    candidates: dict = field(default_factory=dict)
    cached: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)
    signature_hashes: dict = field(default_factory=dict)
    dirty: bool = True
    signature_generation: int = -1


class EventFusionEngine:
    """
    Coordinates the complete Event Fusion pipeline.

    Pipeline

    RawEvent
        ↓
    Normalizer
        ↓
    Sliding Window
        ↓
    Process Tree
        ↓
    Correlator
        ↓
    Detector Registry
        ↓
    Semantic Events
        ↓
    Fusion Result
    """

    def __init__(self, window_seconds: int = 10):

        self.normalizer = EventNormalizer()
        self.window = SlidingWindow(window_seconds)
        self.process_tree = ProcessTree()
        self.correlator = EventCorrelator()
        self.registry = DetectorRegistry()
        self._emitted_signatures: set[bytes] = set()
        self._signature_generation = 0
        self._groups: dict[int | None, _Group] = {}
        self._indexed_detectors = tuple(self.registry)
        self._window_generation = self.window.clear_generation
        self._sequence = 0

    @staticmethod
    def _candidates(event: RawEvent, detectors: tuple) -> list:
        """Discard only records that a built-in detector cannot match.

        Unknown detectors retain the full group, preserving the extension API.
        """
        result = []
        searchable = None
        for detector in detectors:
            detector_type = type(detector)
            if detector_type in _SCORED_DETECTORS:
                if searchable is None:
                    process = (event.process_name or "").lower()
                    payload = " ".join(str(value).lower() for value in event.payload.values())
                    command = event.payload.get("command_line", "").lower()
                    searchable = f"{process} {command} {payload}"
                score = 5 * any(indicator in searchable for indicator in detector.STRONG_INDICATORS)
                score += 3 * any(indicator in searchable for indicator in detector.MEDIUM_INDICATORS)
                score += 2 * any(indicator in searchable for indicator in detector.WEAK_INDICATORS)
                include = bool(score)
                if detector_type is CommandAndControlDetector:
                    include = include or detector._suspicious_dns(event)
            elif detector_type is ReconDetector:
                score = 0
                include = event.process_name in detector.RECON_COMMANDS
            elif detector_type is PersistenceDetector:
                score = 0
                include = bool(detector.match([event]))
            else:
                score = 0
                include = True
            if include:
                result.append((detector, score))
        return result

    def _index_event(self, event: RawEvent) -> None:
        group = self._groups.setdefault(event.process_id, _Group())
        item = (self._sequence, event)
        self._sequence += 1
        group.events.append(item)
        for detector, score in self._candidates(event, self._indexed_detectors):
            candidates = group.candidates.setdefault(detector, deque())
            if not candidates:
                group.signature_hashes[detector] = self._new_signature(detector)
            candidates.append((*item, score))
            signature = group.signature_hashes.get(detector)
            if signature is not None:
                signature.update(self._signature_event(event))
            group.scores[detector] = group.scores.get(detector, 0) + score
            group.cached.pop(detector, None)
            group.dirty = True

    def _expire_event(self, event: RawEvent) -> None:
        group = self._groups[event.process_id]
        sequence, old_event = group.events.popleft()
        assert old_event is event
        for detector, candidates in group.candidates.items():
            if candidates and candidates[0][0] == sequence:
                _, _, score = candidates.popleft()
                group.scores[detector] -= score
                group.signature_hashes.pop(detector, None)
                group.cached.pop(detector, None)
                group.dirty = True
        if not group.events:
            del self._groups[event.process_id]

    def _refresh_index(self) -> None:
        detectors = tuple(self.registry)
        if detectors != self._indexed_detectors or self.window.clear_generation != self._window_generation:
            self._groups.clear()
            self._indexed_detectors = detectors
            self._window_generation = self.window.clear_generation
            for event in self.window.get_events():
                self._index_event(event)

    @staticmethod
    def _new_signature(detector):
        return hashlib.sha256(pickle.dumps(detector.__class__.__name__, protocol=5))

    @staticmethod
    def _signature_event(event: RawEvent) -> bytes:
        timestamp = event.timestamp.astimezone(UTC) if event.timestamp.tzinfo else event.timestamp
        return pickle.dumps((timestamp, event.process_name, event.process_id,
                             str(event.payload.get("command_line", ""))), protocol=5)

    def _signature(self, detector, events) -> bytes:
        signature = self._new_signature(detector)
        for event in events:
            signature.update(self._signature_event(event))
        return signature.digest()

    def process(self, events: Iterable[RawEvent]) -> FusionResult:
        """
        Process a batch of events through the fusion pipeline.
        """

        start = time.perf_counter()

        normalized_events = []
        self._refresh_index()

        # ----------------------------
        # Normalize + Window + Process Tree
        # ----------------------------
        for event in events:

            normalized = self.normalizer.normalize(event)

            normalized_events.append(normalized)

            for expired in self.window.add(normalized):
                self._expire_event(expired)
            self._index_event(normalized)

            self.process_tree.update(normalized)

        # ----------------------------
        # Correlation
        # ----------------------------
        # Correlator order is the first occurrence of each PID, followed by
        # the group without a PID. Its membership is maintained on expiry.
        groups = sorted(
            self._groups.items(),
            key=lambda pair: (pair[0] is None, pair[1].events[0][0]),
        )

        # ----------------------------
        # Detection (with signature deduplication)
        # ----------------------------
        semantic_events = []

        for _, group in groups:
            if not group.dirty and group.signature_generation == self._signature_generation:
                continue
            evaluated_generation = self._signature_generation
            group.dirty = False

            for detector in self.registry:

                if detector not in group.cached:
                    candidate_items = group.candidates.get(detector, ())
                    indexed_match = False
                    if type(detector) in _SCORED_DETECTORS and type(detector) is not CommandAndControlDetector:
                        matched = candidate_items if group.scores.get(detector, 0) >= detector.SCORE_THRESHOLD else None
                        indexed_match = True
                    elif type(detector) is PersistenceDetector:
                        matched = candidate_items
                        indexed_match = True
                    else:
                        matched = detector.match([event for _, event, _ in candidate_items])
                    if type(detector) in _ALL_CANDIDATES_MATCH:
                        state = group.signature_hashes.get(detector)
                        if state is None:
                            state = self._new_signature(detector)
                            for _, event, _ in candidate_items:
                                state.update(self._signature_event(event))
                            group.signature_hashes[detector] = state
                        signature = state.digest()
                    else:
                        signature = self._signature(detector, matched or ())
                    group.cached[detector] = (matched, signature, indexed_match)
                matched, sig, indexed_match = group.cached[detector]

                if matched:
                    if sig in self._emitted_signatures:
                        continue

                    self._emitted_signatures.add(sig)
                    # Limit memory of emitted signatures
                    if len(self._emitted_signatures) > 5000:
                        self._emitted_signatures.clear()
                        self._signature_generation += 1

                    evidence = [event for _, event, _ in matched] if indexed_match else matched
                    semantic_events.append(detector.build(evidence))
            group.signature_generation = evaluated_generation

        runtime_ms = (time.perf_counter() - start) * 1000

        return FusionResult(
            timestamp=datetime.now(),
            processed_events=len(normalized_events),
            normalized_events=len(normalized_events),
            correlated_groups=len(groups),
            detections=semantic_events,
            runtime_ms=runtime_ms,
        )