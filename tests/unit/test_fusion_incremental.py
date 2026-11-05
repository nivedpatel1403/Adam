"""Compare indexed fusion with the original full-window detector evaluation."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from random import Random

from adam.fusion.correlate import EventCorrelator
from adam.fusion.detectors.base import BaseDetector
from adam.fusion.detectors.privilege_escalation import PrivilegeEscalationDetector
from adam.fusion.detectors.registry import DetectorRegistry
from adam.fusion.engine import EventFusionEngine
from adam.fusion.models import RawEvent, SemanticEvent
from adam.fusion.normalise import EventNormalizer
from adam.fusion.window import SlidingWindow


class ReferenceFusion:
    def __init__(self):
        self.window = SlidingWindow(10)
        self.registry = DetectorRegistry()
        self.seen = set()

    def process(self, events):
        for event in events:
            self.window.add(EventNormalizer().normalize(event))
        groups = EventCorrelator().correlate(self.window.get_events())
        detections = []
        for group in groups:
            for detector in self.registry:
                matched = detector.match(group)
                if matched:
                    sig = (type(detector).__name__, tuple(
                        (e.timestamp, e.process_name, e.process_id, str(e.payload.get('command_line', '')))
                        for e in matched
                    ))
                    if sig in self.seen:
                        continue
                    self.seen.add(sig)
                    if len(self.seen) > 5000:
                        self.seen.clear()
                    detections.append(detector.build(matched))
        return len(groups), detections


def representation(detections):
    return [(d.category, [(e.timestamp, e.process_id, e.process_name, e.payload) for e in d.evidence])
            for d in detections]


def test_incremental_results_match_full_window_with_expiry_and_out_of_order_events():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        RawEvent(base, 'x', 'process', process_id=1, process_name='cmd.exe', payload={'command_line': 'http://local/'}),
        RawEvent(base + timedelta(seconds=1), 'x', 'dns', process_id=1, payload={'QueryName': 'c2.example.xyz'}),
        RawEvent(base + timedelta(seconds=2), 'x', 'registry', process_id=2, payload={'TargetObject': r'HKLM\CurrentVersion\Run\test'}),
        RawEvent(base + timedelta(seconds=3), 'x', 'process', process_id=None, process_name='whoami.exe'),
        RawEvent(base + timedelta(seconds=4), 'x', 'process', process_id=None, process_name='hostname.exe'),
        RawEvent(base + timedelta(seconds=5), 'x', 'process', process_id=None, process_name='tasklist.exe'),
        RawEvent(base + timedelta(seconds=12), 'x', 'file', process_id=2, payload={'path': 'ordinary'}),
        RawEvent(base + timedelta(seconds=9), 'x', 'file', process_id=1, payload={'command_line': 'curl http://local'}),
        RawEvent(base + timedelta(seconds=25), 'x', 'file', process_id=3),
    ]
    engine, reference = EventFusionEngine(10), ReferenceFusion()
    for batch in ([events[0]], events[1:3], events[3:5], events[5:7], events[7:], []):
        actual = engine.process(deepcopy(batch))
        group_count, expected = reference.process(deepcopy(batch))
        assert actual.correlated_groups == group_count
        assert representation(actual.detections) == representation(expected)


class CustomDetector(BaseDetector):
    def match(self, events):
        return events if len(events) >= 2 else None

    def build(self, matched):
        return SemanticEvent(matched[0].timestamp, 'custom', 'T0', 'LOW', 1, 'custom', matched)


def test_custom_detectors_and_window_clear_rebuild_the_index():
    now = datetime.now(UTC)
    engine = EventFusionEngine(10)
    first = RawEvent(now, 'x', 'file', process_id=1)
    engine.process([first])
    engine.registry.register(CustomDetector())
    result = engine.process([RawEvent(now, 'x', 'file', process_id=1)])
    assert any(d.category == 'custom' and len(d.evidence) == 2 for d in result.detections)
    engine.window.clear()
    result = engine.process([RawEvent(now, 'x', 'file', process_id=1)])
    assert not any(d.category == 'custom' for d in result.detections)
    assert result.correlated_groups == 1


def test_mixed_scored_groups_match_full_window_through_repeated_expiration():
    rng = Random(8675309)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    engine, reference = EventFusionEngine(10), ReferenceFusion()
    payloads = [
        {}, {'command_line': 'curl http://local/secret'},
        {'command_line': 'powershell invoke-webrequest http://local/'},
        {'TargetObject': r'HKLM\Software\CurrentVersion\Run\demo'},
        {'TargetFilename': r'C:\Users\demo\Startup\test.exe'},
        {'QueryName': 'c2.example.xyz'}, {'path': 'ordinary'},
    ]
    for index in range(250):
        event = RawEvent(
            now + timedelta(seconds=index // 5), 'test', 'file',
            process_id=rng.choice([None, 0, 1, 2, 3]),
            process_name=rng.choice(['cmd.exe', 'whoami.exe', 'schtasks.exe', 'example.exe']),
            payload=deepcopy(rng.choice(payloads)),
        )
        result = engine.process([deepcopy(event)])
        count, expected = reference.process([deepcopy(event)])
        assert result.correlated_groups == count
        assert representation(result.detections) == representation(expected)


def test_system_paths_alone_are_not_privilege_escalation_indicators():
    now = datetime.now(UTC)
    detector = PrivilegeEscalationDetector()
    ordinary = [RawEvent(now, 'procmon', 'file', process_name='Sysmon64.exe',
                         payload={'path': r'C:\Windows\System32\KernelBase.dll'}),
                RawEvent(now, 'procmon', 'registry', process_name='Procmon64.exe',
                         payload={'path': r'HKLM\System\CurrentControlSet\Control\WMI'})]
    assert detector.match(ordinary * 5) is None
    suspicious = RawEvent(now, 'sysmon', 'process', process_name='cmd.exe',
                          payload={'command_line': 'tool.exe token::elevate getsystem'})
    assert detector.match([suspicious]) == [suspicious]


def test_signature_cache_reset_rechecks_unchanged_groups():
    now = datetime.now(UTC)
    engine, reference = EventFusionEngine(10), ReferenceFusion()
    initial = [RawEvent(now, 'test', 'registry', process_id=pid,
                        payload={'TargetObject': rf'HKLM\CurrentVersion\Run\{pid}'})
               for pid in (1, 2)]
    actual = engine.process(deepcopy(initial))
    _, expected = reference.process(deepcopy(initial))
    assert representation(actual.detections) == representation(expected)
    engine._emitted_signatures.update(f'placeholder-{i}'.encode() for i in range(4999))
    reference.seen.update(('placeholder', i) for i in range(4999))
    update = [RawEvent(now, 'test', 'registry', process_id=1,
                       payload={'TargetObject': r'HKLM\CurrentVersion\Run\new'})]
    for batch in (update, []):
        actual = engine.process(deepcopy(batch))
        _, expected = reference.process(deepcopy(batch))
        assert representation(actual.detections) == representation(expected)
