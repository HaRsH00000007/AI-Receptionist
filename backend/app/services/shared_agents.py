"""Which shared agent serves which vertical.

The POC gives every tenant its own ElevenLabs agent. That is simple and it
works, and it stops working at a few hundred customers: a prompt improvement
becomes a few hundred vendor calls that can half-fail, and there is no moment
at which the fleet is known to be consistent.

Shared vertical agents invert that. About five agents exist — salon, legal,
medical, real estate, generic — and a tenant's identity arrives per call as
dynamic variables from ``/voice/init``. A prompt improvement then ships once,
to everyone, instantly, and rollback is a config version flag rather than a
fan-out.

**The mapping is configured, never discovered.** Agent ids are account-specific
and differ between a test and a live ElevenLabs account, so they are operator
input exactly like Stripe price ids. Nothing here creates a shared agent: an
application that can create one can also create a *second* one by accident, and
two agents serving one vertical is an invisible split-brain where half the
customers get last month's prompt.

Tenant isolation is the property to be most careful about. A shared agent is
generic until ``/voice/init`` tells it who it is; the isolation therefore lives
in that lookup — keyed on the dialled number, which only one tenant owns — and
in never putting one tenant's configuration where another tenant's call can
reach it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.enums import AgentMode, BusinessType

logger = get_logger(__name__)

#: The vertical used when a business type has no agent of its own.
GENERIC_VERTICAL = "generic"


@dataclass(frozen=True, slots=True)
class SharedAgent:
    """A configured shared agent."""

    vertical: str
    agent_id: str


def parse_shared_agent_map(raw: str) -> dict[str, str]:
    """Read ``"salon:agent_a,legal:agent_b"`` into a mapping.

    Malformed entries are skipped with a warning rather than raising. This is
    read during provisioning and on the call path; a stray comma in an
    environment variable should degrade one vertical to dedicated agents, not
    take the process down.
    """
    mapping: dict[str, str] = {}
    for entry in raw.split(","):
        piece = entry.strip()
        if not piece:
            continue
        vertical, separator, agent_id = piece.partition(":")
        if not separator or not vertical.strip() or not agent_id.strip():
            logger.warning("ignoring a malformed shared agent mapping entry")
            continue
        mapping[vertical.strip().lower()] = agent_id.strip()
    return mapping


def resolve_shared_agent(settings: Settings, business_type: BusinessType) -> SharedAgent | None:
    """The shared agent for a business type, or ``None`` if none is configured.

    Falls back to the generic vertical before giving up, so adding a new
    business type does not leave it unserved until someone remembers to
    configure an agent for it.

    ``None`` is a normal answer, not an error: it means this deployment has no
    shared agent for this vertical, and the caller should fall back to a
    dedicated agent. Failing instead would make an unconfigured vertical
    unprovisionable.
    """
    mapping = parse_shared_agent_map(settings.shared_agent_ids)
    if not mapping:
        return None

    for vertical in (business_type.value, GENERIC_VERTICAL):
        agent_id = mapping.get(vertical)
        if agent_id:
            return SharedAgent(vertical=vertical, agent_id=agent_id)

    logger.warning(
        "no shared agent configured for this vertical; falling back to a dedicated agent",
        extra={"business_type": business_type.value},
    )
    return None


def effective_agent_mode(settings: Settings, tenant_mode: AgentMode) -> AgentMode:
    """What mode this tenant is actually served in.

    The tenant's own column wins, so a migration moves tenants in batches rather
    than flipping everyone at once — which is the only way to verify a batch
    with a test call before moving the next.

    A tenant marked shared is downgraded to dedicated when no shared agent is
    configured. Refusing instead would strand every tenant already migrated the
    moment a configuration entry is lost.
    """
    if tenant_mode is not AgentMode.SHARED_VERTICAL:
        return AgentMode.PER_TENANT
    if not parse_shared_agent_map(settings.shared_agent_ids):
        logger.warning("tenant is marked shared but no shared agents are configured")
        return AgentMode.PER_TENANT
    return AgentMode.SHARED_VERTICAL
