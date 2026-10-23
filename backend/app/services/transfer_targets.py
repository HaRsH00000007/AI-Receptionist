"""Transfer rules as a published configuration records them.

A published ``agent_configs`` row is the answer to "what was this receptionist
doing when that call came in?". Before this module, that answer covered what the
agent *said*; a transfer rule changes what it *does*, so the destinations a
version was published with are recorded in its ``model_params`` alongside the
prompt and the model settings.

Keeping the conversion here, in a module that imports neither the publisher nor
the rule service, is what lets both sides use one copy of it without an import
cycle — and it means the shape stored in a config version is defined in exactly
one place rather than written by the publisher and re-guessed by the reader.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.models.enums import TransferType
from app.models.transfer_rule import TransferRule
from app.providers.models import TransferTarget

#: The key under ``model_params`` the destinations live in.
PARAMS_KEY = "transfers"


def targets_for(rules: Sequence[TransferRule]) -> list[TransferTarget]:
    """The enabled rules, as the provider layer wants them.

    Disabled rules are dropped here rather than in the query, so a caller that
    loaded every rule for display cannot accidentally publish the switched-off
    ones.
    """
    return [
        TransferTarget(
            e164=rule.destination_e164,
            condition=rule.condition,
            kind="warm" if rule.transfer_type is TransferType.WARM else "cold",
        )
        for rule in rules
        if rule.enabled
    ]


def dump_targets(targets: Sequence[TransferTarget]) -> list[dict[str, Any]]:
    """For storage in a JSONB column."""
    return [
        {"phone_number": target.e164, "condition": target.condition, "kind": target.kind}
        for target in targets
    ]


def load_targets(raw: Any) -> list[TransferTarget]:
    """Read destinations back out of a stored configuration.

    Defensive, because this reads a JSON column that older versions were
    published without: anything that is not a list of well-formed entries reads
    as "this version transferred nothing". Degrading to no transfers is the safe
    direction — the receptionist takes a message, which is what it did before
    this feature existed — whereas guessing at a malformed entry could dial a
    number nobody configured.
    """
    if not isinstance(raw, list):
        return []
    targets: list[TransferTarget] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        number = str(entry.get("phone_number") or "").strip()
        condition = str(entry.get("condition") or "").strip()
        if not number or not condition:
            continue
        kind = entry.get("kind")
        targets.append(
            TransferTarget(
                e164=number,
                condition=condition,
                kind="cold" if kind == "cold" else "warm",
            )
        )
    return targets


def targets_of_config(model_params: dict[str, Any] | None) -> list[TransferTarget]:
    """The destinations a published config version carries."""
    return load_targets((model_params or {}).get(PARAMS_KEY))
