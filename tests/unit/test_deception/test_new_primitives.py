import pytest
from unittest.mock import AsyncMock
from adam.contracts.mutation import MutationResult
from adam.contracts.enums import MutationStatus
from adam.deception.primitives.process_lures import SimulateAVPresence, AccelerateSystemClock, SpawnDecoyProcesses

@pytest.mark.asyncio
async def test_spawn_decoy_processes():
    channel = AsyncMock()
    prim = SpawnDecoyProcesses(channel)
    mut = await prim.apply_async("sess", "corr", "dec", {})
    assert mut.status == MutationStatus.APPLIED
    assert len(mut.changes) == 2
    
    rev = await prim.revert_async(mut)
    assert rev.status == MutationStatus.REVERTED
    assert channel.apply_mutation.call_count == 4

@pytest.mark.asyncio
async def test_accelerate_system_clock():
    channel = AsyncMock()
    prim = AccelerateSystemClock(channel)
    mut = await prim.apply_async("sess", "corr", "dec", {})
    assert mut.status == MutationStatus.APPLIED
    assert len(mut.changes) == 1
    
    rev = await prim.revert_async(mut)
    assert rev.status == MutationStatus.REVERTED
    assert channel.apply_mutation.call_count == 2
from adam.deception.primitives.filesystem_lures import PlantDecoyDocuments, PlantDecoyWallet
from adam.deception.primitives.network_lures import MountFakeNetworkShare, FabricateC2Response
from adam.deception.primitives.identity_lures import HideVMArtifacts, InjectFakeBrowserCreds
from adam.deception.primitives.registry_lures import PlantDecoyRunKey

@pytest.mark.asyncio
async def test_fabricate_c2_response():
    channel = AsyncMock()
    prim = FabricateC2Response(channel)
    mut = await prim.apply_async(
        "sess",
        "corr",
        "dec",
        {"target_url": "http://198.51.100.7:8080/beacon"},
    )
    assert mut.status == MutationStatus.APPLIED
    assert len(mut.changes) == 1
    assert mut.changes[0].target == "http://198.51.100.7:8080/beacon"
    
    rev = await prim.revert_async(mut)
    assert rev.status == MutationStatus.REVERTED
    assert channel.apply_mutation.call_count == 2

@pytest.mark.asyncio
async def test_inject_fake_browser_creds():
    channel = AsyncMock()
    prim = InjectFakeBrowserCreds(channel)
    mut = await prim.apply_async("sess", "corr", "dec", {})
    assert mut.status == MutationStatus.APPLIED
    assert len(mut.changes) == 2
    
    rev = await prim.revert_async(mut)
    assert rev.status == MutationStatus.REVERTED
    assert channel.apply_mutation.call_count == 4

@pytest.mark.asyncio
async def test_plant_decoy_wallet():
    channel = AsyncMock()
    prim = PlantDecoyWallet(channel)
    mut = await prim.apply_async("sess", "corr", "dec", {})
    assert mut.status == MutationStatus.APPLIED
    assert len(mut.changes) == 2
    
    rev = await prim.revert_async(mut)
    assert rev.status == MutationStatus.REVERTED
    assert channel.apply_mutation.call_count == 4

@pytest.mark.asyncio
async def test_simulate_av_presence():
    channel = AsyncMock()
    prim = SimulateAVPresence(channel)
    mut = await prim.apply_async("sess", "corr", "dec", {})
    assert mut.status == MutationStatus.APPLIED
    assert len(mut.changes) == 2
    
    rev = await prim.revert_async(mut)
    assert rev.status == MutationStatus.REVERTED
    assert channel.apply_mutation.call_count == 4

@pytest.mark.asyncio
async def test_plant_decoy_documents():
    channel = AsyncMock()
    prim = PlantDecoyDocuments(channel)
    mut = await prim.apply_async("sess", "corr", "dec", {})
    assert mut.status == MutationStatus.APPLIED
    assert len(mut.changes) == 3
    # apply_async takes the batch path when channel has apply_mutation_batch
    # (AsyncMock auto-exposes it) — so apply_mutation is NOT called for creates.
    assert channel.apply_mutation_batch.call_count == 1
    assert channel.apply_mutation.call_count == 0

    rev = await prim.revert_async(mut)
    assert rev.status == MutationStatus.REVERTED
    # revert still uses 3 individual apply_mutation DELETE calls
    assert channel.apply_mutation.call_count == 3

@pytest.mark.asyncio
async def test_mount_fake_network_share():
    channel = AsyncMock()
    prim = MountFakeNetworkShare(channel)
    mut = await prim.apply_async("sess", "corr", "dec", {})
    assert mut.status == MutationStatus.APPLIED
    assert len(mut.changes) == 6
    registry = {c.target: c for c in mut.changes if c.kind.value == "REGISTRY"}
    assert registry[r"HKCU\Network\Z\RemotePath"].value == r"\\Server\Share"
    assert registry[r"HKCU\Network\Z\ProviderName"].value == "Microsoft Windows Network"
    assert registry[r"HKCU\Network\Z\ConnectionType"].value == "1"
    assert registry[r"HKCU\Network\Z\ConnectionType"].value_type == "DWord"
    assert registry[r"HKCU\Network\Z\UserName"].value == r"CORP\analyst"
    assert registry[r"HKCU\Network\Z\DeferFlags"].value == "4"
    assert registry[r"HKCU\Network\Z\DeferFlags"].value_type == "DWord"

    rev = await prim.revert_async(mut)
    assert rev.status == MutationStatus.REVERTED
    # Six apply calls + five value deletes + one key delete + one unmount.
    assert channel.apply_mutation.call_count == 13

@pytest.mark.asyncio
async def test_hide_vm_artifacts():
    channel = AsyncMock()
    prim = HideVMArtifacts(channel)
    mut = await prim.apply_async("sess", "corr", "dec", {})
    assert mut.status == MutationStatus.APPLIED
    assert len(mut.changes) == 1
    
    rev = await prim.revert_async(mut)
    assert rev.status == MutationStatus.REVERTED
    assert channel.apply_mutation.call_count == 2

@pytest.mark.asyncio
async def test_plant_decoy_run_key():
    channel = AsyncMock()
    prim = PlantDecoyRunKey(channel)
    mut = await prim.apply_async("sess", "corr", "dec", {})
    assert mut.status == MutationStatus.APPLIED
    assert len(mut.changes) == 1
    
    rev = await prim.revert_async(mut)
    assert rev.status == MutationStatus.REVERTED
    assert channel.apply_mutation.call_count == 2
