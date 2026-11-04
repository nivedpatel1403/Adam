"""CLI parsing tests for `adam run`.

The arm enum values are uppercase (Arm.CONTROL / Arm.TREATMENT), while the
CLI accepts lowercase user input. This test pins that normalization so the
`--arm control` / `--arm treatment` interface cannot regress into a
ValueError from Arm(...).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from adam.cli import run as run_module
from adam.cli.main import app
from adam.contracts.enums import Arm, SessionStatus


class _FakeRunner:
    last_kwargs: dict | None = None

    def __init__(self) -> None:
        self.kwargs: dict | None = None

    async def run(self, sample_path, **kwargs):
        type(self).last_kwargs = kwargs
        self.kwargs = kwargs
        return SimpleNamespace(
            session_id="sess_test",
            status=SessionStatus.COMPLETED,
            metrics=SimpleNamespace(raw_events=0),
            error=None,
        )


@pytest.fixture
def cli_sample(tmp_path):
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    return sample


@pytest.mark.parametrize(
    ("cli_value", "expected"),
    [
        ("control", Arm.CONTROL),
        ("CONTROL", Arm.CONTROL),
        ("treatment", Arm.TREATMENT),
        ("Treatment", Arm.TREATMENT),
    ],
)
def test_run_arm_is_case_insensitive(monkeypatch, cli_sample, cli_value, expected):
    _FakeRunner.last_kwargs = None
    monkeypatch.setattr(run_module, "Runner", _FakeRunner)

    def _await(coro):
        return asyncio.run(coro)

    monkeypatch.setattr(run_module, "_run_with_graceful_cancellation", _await)

    result = CliRunner().invoke(
        app,
        ["run", str(cli_sample), "--arm", cli_value],
    )

    assert result.exit_code == 0, result.output
    assert _FakeRunner.last_kwargs is not None
    assert _FakeRunner.last_kwargs["arm"] is expected


def test_run_rejects_unknown_arm(monkeypatch, cli_sample):
    monkeypatch.setattr(run_module, "Runner", _FakeRunner)

    result = CliRunner().invoke(
        app,
        ["run", str(cli_sample), "--arm", "not-an-arm"],
    )

    assert result.exit_code == 2
    assert "invalid --arm" in result.output


def test_execution_deadline_reaches_runner(monkeypatch, cli_sample):
    monkeypatch.setattr(run_module, "Runner", _FakeRunner)
    monkeypatch.setattr(run_module, "_run_with_graceful_cancellation", asyncio.run)
    result = CliRunner().invoke(app, ["run", str(cli_sample), "--timeout-seconds", "8"])
    assert result.exit_code == 0, result.output
    assert _FakeRunner.last_kwargs["sample_timeout_seconds"] == 8
