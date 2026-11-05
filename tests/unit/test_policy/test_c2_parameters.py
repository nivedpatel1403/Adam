"""RULE-023 must fabricate against the endpoint actually observed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adam.contracts.enums import Verdict
from adam.contracts.semantic_event import SemanticEvent
from adam.deception.primitives.network_lures import FabricateC2Response
from adam.policy.context import SessionContext
from adam.policy.engine import PolicyEngine

FIXTURES = Path(__file__).parents[2] / "fixtures" / "semantic_events"
RULES_DIR = Path(__file__).parents[3] / "rules" / "default"


def _c2_event() -> SemanticEvent:
    data = json.loads((FIXTURES / "c2_beacon.json").read_text())
    event = SemanticEvent.model_validate(data)
    event.features["network_endpoint"] = "http://198.51.100.7:8080/beacon"
    return event


def test_rule_023_uses_observed_network_endpoint() -> None:
    engine = PolicyEngine(str(RULES_DIR), global_confidence_gate=0.60)
    event = _c2_event()

    decisions = engine.evaluate(event, SessionContext(session_id=event.session_id))
    fired = [d for d in decisions if d.rule_id == "RULE-023"]

    assert len(fired) == 1
    assert fired[0].parameters["target_url"] == "http://198.51.100.7:8080/beacon"
    assert fired[0].parameters["c2_url"] == "http://198.51.100.7:8080/beacon"


@pytest.mark.parametrize('missing_endpoint', [None, '192.168.19.2:18080', 'ftp://example.test/file'])
def test_unusable_c2_endpoint_does_not_consume_budget(missing_endpoint) -> None:
    engine = PolicyEngine(str(RULES_DIR), global_confidence_gate=0.60)
    event = _c2_event()
    context = SessionContext(session_id=event.session_id)
    event.features['network_endpoint'] = missing_endpoint
    first = next(d for d in engine.evaluate(event, context) if d.rule_id == 'RULE-023')
    assert first.verdict is Verdict.SUPPRESSED_CONFLICT
    assert first.action is None
    assert 'budget remains available' in first.rationale
    event.features['network_endpoint'] = 'http://192.168.19.2:18080/marker/c2'
    second = next(d for d in engine.evaluate(event, context) if d.rule_id == 'RULE-023')
    assert second.verdict is Verdict.EXECUTE
    assert second.parameters['target_url'] == event.features['network_endpoint']


@pytest.mark.asyncio
async def test_fabricate_primitive_targets_observed_endpoint() -> None:
    primitive = FabricateC2Response(channel=object())  # type: ignore[arg-type]
    changes = await primitive._build_changes(
        {
            "target_url": "http://198.51.100.7:8080/beacon",
            "response_body": "hello",
        }
    )

    assert len(changes) == 1
    assert changes[0].kind.value == "NETWORK"
    assert changes[0].target == "http://198.51.100.7:8080/beacon"
    assert "hello" in (changes[0].value or "")
    assert "c2.baddomain.org" not in changes[0].target


@pytest.mark.asyncio
async def test_fabricate_primitive_requires_an_observed_endpoint() -> None:
    primitive = FabricateC2Response(channel=object())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="target_url/c2_url"):
        await primitive._build_changes({})
