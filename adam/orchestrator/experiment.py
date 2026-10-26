"""
adam/orchestrator/experiment.py

Single source of truth for a session's experimental arm and deception
enablement.  Before this module existed, Runner and SessionOrchestrator each
derived the arm independently (and from different settings), so a session
could be labelled CONTROL while its DeceptionEngine was still executing
mutations.  Resolve once here and pass the resulting object everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from adam.common.config import Settings
from adam.contracts.enums import Arm


@dataclass(frozen=True)
class ResolvedExperiment:
    """The immutable experiment state for one session."""

    arm: Arm
    deception_enabled: bool
    policy_ruleset: str = "default"

    @property
    def dry_run(self) -> bool:
        """True when policy may evaluate but deception must not execute."""
        return not self.deception_enabled


def resolve_experiment(
    settings: Settings,
    *,
    arm: Arm | None = None,
    deception_enabled: bool | None = None,
) -> ResolvedExperiment:
    """
    Resolve the arm/deception pair with explicit overrides taking priority
    over configuration.

    Rules:
      - explicit arm wins; deception_enabled is derived from it;
      - explicit deception_enabled (without arm) derives the arm;
      - both explicit must agree, otherwise this is a caller bug;
      - neither explicit falls back to ``settings.deception.enabled``.
    """
    if arm is not None and deception_enabled is not None:
        expected = arm == Arm.TREATMENT
        if deception_enabled != expected:
            raise ValueError(
                "Conflicting experiment request: "
                f"arm={arm.value!r} implies deception_enabled={expected}, "
                f"but deception_enabled={deception_enabled} was supplied"
            )
        return ResolvedExperiment(arm=arm, deception_enabled=deception_enabled)

    if arm is not None:
        return ResolvedExperiment(
            arm=arm,
            deception_enabled=arm == Arm.TREATMENT,
        )

    if deception_enabled is not None:
        return ResolvedExperiment(
            arm=Arm.TREATMENT if deception_enabled else Arm.CONTROL,
            deception_enabled=deception_enabled,
        )

    enabled = settings.deception.enabled
    return ResolvedExperiment(
        arm=Arm.TREATMENT if enabled else Arm.CONTROL,
        deception_enabled=enabled,
    )
