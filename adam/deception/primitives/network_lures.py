from __future__ import annotations
import re
from typing import Any
from adam.contracts.enums import ChangeKind, MutationStatus
from adam.contracts.mutation import Change, MutationResult
from adam.deception.catalogue import register_primitive
from adam.deception.plausibility import combine, score_naming_consistency, score_timestamp_consistency
from adam.deception.primitives.base import DeceptionPrimitive

@register_primitive("MOUNT_FAKE_NETWORK_SHARE")
class MountFakeNetworkShare(DeceptionPrimitive):
    name = "MountFakeNetworkShare"
    version = "1.0"

    async def _build_changes(self, parameters: dict[str, Any]) -> list[Change]:
        drive = str(parameters.get("drive_letter", "Z")).strip().rstrip(":").upper()
        if not re.fullmatch(r"[A-Z]", drive):
            raise ValueError(f"MOUNT_FAKE_NETWORK_SHARE: invalid drive letter {drive!r}")
        share = str(parameters.get("share_unc", r"\\Server\Share")).strip()
        if not share.startswith("\\\\"):
            raise ValueError(
                f"MOUNT_FAKE_NETWORK_SHARE: share_unc must be a UNC path, got {share!r}"
            )
        username = str(parameters.get("username", r"CORP\analyst"))
        base = rf"HKCU\Network\{drive}"
        # HKCU\Network\<Drive> is a SUBKEY with five values.  Writing the
        # drive letter as a single value (the old behavior) does not create
        # a network-drive artifact that Explorer or malware enumerates.
        return [
            Change(
                kind=ChangeKind.REGISTRY,
                target=rf"{base}\RemotePath",
                operation="SET",
                value=share,
                value_type="String",
            ),
            Change(
                kind=ChangeKind.REGISTRY,
                target=rf"{base}\ProviderName",
                operation="SET",
                value="Microsoft Windows Network",
                value_type="String",
            ),
            Change(
                kind=ChangeKind.REGISTRY,
                target=rf"{base}\ConnectionType",
                operation="SET",
                value="1",
                value_type="DWord",
            ),
            Change(
                kind=ChangeKind.REGISTRY,
                target=rf"{base}\UserName",
                operation="SET",
                value=username,
                value_type="String",
            ),
            Change(
                kind=ChangeKind.REGISTRY,
                target=rf"{base}\DeferFlags",
                operation="SET",
                value="4",
                value_type="DWord",
            ),
            Change(
                kind=ChangeKind.NETWORK,
                target=share,
                operation="MOUNT",
                value=f"FakeShare-{drive}",
            ),
        ]

    def _plausibility(self, parameters: dict[str, Any]) -> tuple[float, str]:
        ts_score = score_timestamp_consistency(is_post_boot_write=True)
        name_score = score_naming_consistency(matches_locale_convention=True)
        score = combine(ts_score, name_score)
        return score, "Mounted fake network share"

    async def revert_async(self, mutation: MutationResult) -> MutationResult:
        drive: str | None = None
        for change in reversed(mutation.changes):
            if change.kind == ChangeKind.REGISTRY:
                await self._channel.apply_mutation(change.kind.value, change.target, "DELETE", None)
                # Every value lives under HKCU\Network\<Drive>; remember the
                # subkey so it can be removed after its values are gone.
                if r"\Network" in change.target:
                    _, _, tail = change.target.partition(r"\Network")
                    drive = tail.strip("\\").split("\\", 1)[0] or None
            elif change.kind == ChangeKind.NETWORK:
                await self._channel.apply_mutation(change.kind.value, change.target, "UNMOUNT", None)
        if drive:
            await self._channel.apply_mutation(
                ChangeKind.REGISTRY.value,
                rf"HKCU\Network\{drive}",
                "DELETE_KEY",
                None,
            )
        mutation.status = MutationStatus.REVERTED
        return mutation


@register_primitive("FABRICATE_C2_RESPONSE")
class FabricateC2Response(DeceptionPrimitive):
    name = "FabricateC2Response"
    version = "1.0"

    async def _build_changes(self, parameters: dict[str, Any]) -> list[Change]:
        target = parameters.get("target_url") or parameters.get("c2_url")
        if not target:
            raise ValueError(
                "FABRICATE_C2_RESPONSE requires an observed target_url/c2_url "
                "in the policy decision parameters"
            )
        body = str(parameters.get("response_body", "fake_c2_payload"))
        raw_response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/octet-stream\r\n"
            f"Content-Length: {len(body.encode('utf-8'))}\r\n"
            "\r\n"
            f"{body}"
        )
        return [
            Change(
                kind=ChangeKind.NETWORK,
                target=str(target),
                operation="RESPOND",
                value=raw_response,
            )
        ]

    def _plausibility(self, parameters: dict[str, Any]) -> tuple[float, str]:
        ts_score = score_timestamp_consistency(is_post_boot_write=True)
        name_score = score_naming_consistency(matches_locale_convention=True)
        score = combine(ts_score, name_score)
        return score, "Fabricated C2 beacon network response"

    async def revert_async(self, mutation: MutationResult) -> MutationResult:
        for change in reversed(mutation.changes):
            await self._channel.apply_mutation(change.kind.value, change.target, "UNRESPOND", None)
        mutation.status = MutationStatus.REVERTED
        return mutation
