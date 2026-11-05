import hashlib
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import verify_candidate_snapshots as harness
from scripts import vm_acceptance
from scripts.vm_acceptance import (
    inspect_markers,
    markers_absent,
    markers_match,
    validate_acceptance,
)


@pytest.mark.asyncio
async def test_readiness_deadline_bounds_an_unresponsive_request():
    async def stalled():
        await asyncio.sleep(60)

    channel = SimpleNamespace(get_version=stalled, get_health=AsyncMock())
    assert await asyncio.wait_for(
        harness.wait_for_guest_ready(channel, timeout_s=0.02, poll_interval_s=1), timeout=1
    ) is False
    channel.get_health.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stdout,rc", [('{}',0), ('not json',0), ('',1)])
async def test_unreadable_markers_never_count_as_absent(stdout, rc):
    channel = SimpleNamespace(run_process=AsyncMock(return_value=SimpleNamespace(return_code=rc,stdout=stdout,stderr='query failed')))
    observation = await inspect_markers(channel, r'C:\ADAM\marker', r'HKLM\Software\ADAM\Marker')
    assert not markers_absent(observation)
    assert not markers_match(observation, 'expected')


@pytest.mark.asyncio
async def test_failed_witness_planting_blocks_restore_proof(monkeypatch):
    client = SimpleNamespace(get_state=AsyncMock(return_value='poweroff'), start=AsyncMock(return_value=SimpleNamespace(success=True)))
    monkeypatch.setattr(harness, 'wait_for_guest_ready', AsyncMock(return_value=True))
    monkeypatch.setattr(harness, 'check_markers', AsyncMock(return_value={'file_present':False,'reg_present':False}))
    monkeypatch.setattr(harness, 'plant_markers', AsyncMock(return_value=False))
    monkeypatch.setattr(harness, 'stop_vm_cleanly', AsyncMock(return_value=True))
    channel = object()
    result = await harness.prepare_reset_witness(client, channel, 'vm', 'candidate', ready_timeout_s=731)
    assert result['success'] is False
    harness.wait_for_guest_ready.assert_awaited_once_with(channel, timeout_s=731)


@pytest.mark.asyncio
@pytest.mark.parametrize('defect', ['absent', 'changed_value', 'unavailable', 'disk_changed', 'snapshot_changed'])
async def test_intermediate_restore_or_changed_witness_blocks_cycle(monkeypatch, defect):
    client = SimpleNamespace(start=AsyncMock(return_value=SimpleNamespace(success=True)),restore_snapshot=AsyncMock())
    observation = {'file_present':True,'reg_present':True,'file_value':'expected','reg_value':'expected'}
    before = {'disk_uuid':'original','snapshot_uuid':'candidate'}
    after = dict(before)
    if defect == 'absent':
        observation = {'file_present':False,'reg_present':False}
    elif defect == 'changed_value':
        observation['reg_value']='wrong'
    elif defect == 'unavailable':
        observation = {'error':'unavailable'}
    elif defect == 'disk_changed':
        after['disk_uuid']='new'
    else:
        after['snapshot_uuid']='different'
    monkeypatch.setattr(harness, 'stop_vm_cleanly', AsyncMock(return_value=True))
    monkeypatch.setattr(harness, 'current_disk_fingerprint', AsyncMock(return_value=after))
    result = await harness.run_verification_cycle(client, object(), 'vm', 'candidate', 2,
        reset_witness={'success':True,'candidate_uuid':'candidate','token':'expected','observed':observation,'disk':before})
    assert not result['restore_success']
    assert 'invalidates the proof' in result['error']
    client.restore_snapshot.assert_not_awaited()


def test_missing_acceptance_record_cannot_promote():
    assert not validate_acceptance(None, 'candidate')['success']


@pytest.mark.asyncio
async def test_cycles_alone_cannot_authorize_promotion():
    client = SimpleNamespace(_run=AsyncMock(),list_snapshots=AsyncMock())
    result = await harness.promote_candidate_to_clean(client, 'vm', 'candidate', 'recovery',
        verification={'candidate_uuid':'candidate','cycles_passed':True})
    assert not result['success']
    client._run.assert_not_awaited()
    client.list_snapshots.assert_not_awaited()


@pytest.mark.parametrize('defect', ['candidate', 'revision', 'build', 'failed_check', 'missing_evidence', 'changed_evidence', 'dirty'])
def test_acceptance_rejects_mismatched_or_partial_proof(tmp_path, monkeypatch, defect):
    root = tmp_path / 'repo'
    manifest = root / 'adam/sandbox/guest/agent/build_manifest.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'build_id':'build'}))
    monkeypatch.setattr(vm_acceptance, 'ROOT', root)
    monkeypatch.setattr(vm_acceptance.subprocess, 'check_output',
        lambda command, **kwargs: ('dirty' if defect == 'dirty' else '') if 'status' in command else 'revision')
    proof = tmp_path / 'proof.json'
    proof.write_text('{"success":true}')
    digest = hashlib.sha256(proof.read_bytes()).hexdigest()
    record = {'candidate_uuid':'candidate', 'repository_revision':'revision', 'guest_build':'build',
              'checks': {key:{'status':'passed','evidence':[{'path':'proof.json','sha256':digest}]}
                         for key in vm_acceptance.REQUIRED_CHECKS}}
    if defect in ('candidate', 'revision', 'build'):
        record[{'candidate':'candidate_uuid','revision':'repository_revision','build':'guest_build'}[defect]]='wrong'
    elif defect == 'failed_check':
        record['checks']['failure_export']['status']='failed'
    elif defect == 'missing_evidence':
        record['checks']['dashboard']['evidence']=[]
    elif defect == 'changed_evidence':
        proof.write_text('{"success":false}')
    path = tmp_path / 'acceptance.json'
    path.write_text(json.dumps(record))
    assert not validate_acceptance(path, 'candidate')['success']
