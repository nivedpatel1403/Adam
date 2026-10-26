"""Unit tests for the single-source-of-truth experiment resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from adam.common.bus import EventBus
from adam.common.config import DeceptionSettings, Settings
from adam.contracts.enums import Arm
from adam.contracts.session import SampleRef
from adam.orchestrator.experiment import resolve_experiment
from adam.orchestrator.session import SessionOrchestrator


def _settings(*, enabled: bool) -> Settings:
    return Settings(
        sandbox={
            "vm_name": "TEST_VM",
            "guest_username": "tester",
            "guest_password": "not-a-real-secret",
        },
        deception=DeceptionSettings(enabled=enabled),
    )


def test_config_enabled_true_resolves_treatment() -> None:
    experiment = resolve_experiment(_settings(enabled=True))
    assert experiment.arm == Arm.TREATMENT
    assert experiment.deception_enabled is True
    assert experiment.dry_run is False


def test_config_enabled_false_resolves_control() -> None:
    experiment = resolve_experiment(_settings(enabled=False))
    assert experiment.arm == Arm.CONTROL
    assert experiment.deception_enabled is False
    assert experiment.dry_run is True


def test_explicit_control_overrides_enabled_config() -> None:
    experiment = resolve_experiment(_settings(enabled=True), arm=Arm.CONTROL)
    assert experiment.arm == Arm.CONTROL
    assert experiment.deception_enabled is False
    assert experiment.dry_run is True


def test_explicit_treatment_overrides_disabled_config() -> None:
    experiment = resolve_experiment(_settings(enabled=False), arm=Arm.TREATMENT)
    assert experiment.arm == Arm.TREATMENT
    assert experiment.deception_enabled is True
    assert experiment.dry_run is False


def test_explicit_deception_flag_derives_arm() -> None:
    control = resolve_experiment(_settings(enabled=True), deception_enabled=False)
    assert control.arm == Arm.CONTROL
    assert control.dry_run is True

    treatment = resolve_experiment(_settings(enabled=False), deception_enabled=True)
    assert treatment.arm == Arm.TREATMENT
    assert treatment.dry_run is False


def test_conflicting_arm_and_flag_raises() -> None:
    with pytest.raises(ValueError, match="Conflicting experiment request"):
        resolve_experiment(
            _settings(enabled=True),
            arm=Arm.CONTROL,
            deception_enabled=True,
        )


class FakeController:
    vm_profile = None

    async def prepare(self) -> None:
        pass

    async def arm(self, host_sample_path: str, guest_target_path: str, *, timeout: float = 60.0) -> None:
        pass

    async def detonate(self, sample: SampleRef) -> None:
        pass

    async def teardown(self) -> None:
        pass


def _sample() -> SampleRef:
    return SampleRef(
        sha256="0" * 64,
        md5="0" * 32,
        filename="sample.exe",
        size_bytes=1,
        file_type="PE32",
    )


async def _run(tmp_path: Path, *, arm: Arm | None = None, enabled: bool = True):
    settings = _settings(enabled=enabled)
    orchestrator = SessionOrchestrator(
        FakeController(),  # type: ignore[arg-type]
        EventBus(),
        [],
        artifacts_dir=tmp_path,
        bus_drain_timeout=1.0,
        post_detonation_drain_seconds=0.0,
    )
    return await orchestrator.run_session(
        _sample(),
        settings,
        host_sample_path="sample.exe",
        arm=arm,
    )


@pytest.mark.asyncio
async def test_run_session_honors_explicit_control(tmp_path: Path) -> None:
    session = await _run(tmp_path, arm=Arm.CONTROL, enabled=True)
    assert session.arm == Arm.CONTROL
    assert session.config.deception_enabled is False
    assert session.config.policy_ruleset == "default"


@pytest.mark.asyncio
async def test_run_session_honors_explicit_treatment(tmp_path: Path) -> None:
    session = await _run(tmp_path, arm=Arm.TREATMENT, enabled=False)
    assert session.arm == Arm.TREATMENT
    assert session.config.deception_enabled is True


@pytest.mark.asyncio
async def test_run_session_uses_config_when_no_override(tmp_path: Path) -> None:
    treatment = await _run(tmp_path, enabled=True)
    assert treatment.arm == Arm.TREATMENT

    control = await _run(tmp_path, enabled=False)
    assert control.arm == Arm.CONTROL
