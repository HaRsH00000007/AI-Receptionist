"""SQLAlchemy models.

Importing this package imports every model, which is what populates
``Base.metadata``. Alembic's ``env.py`` and the test harness both rely on that,
so a new model must be re-exported here or it will silently never be migrated.
"""

from __future__ import annotations

from app.models.agent import Agent
from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.call import Call
from app.models.phone_number import PhoneNumber
from app.models.provisioning import ProvisioningRun, ProvisioningStepRecord
from app.models.tenant import Tenant
from app.models.webhook_event import WebhookEvent

__all__ = [
    "Agent",
    "AgentConfig",
    "BusinessProfile",
    "Call",
    "PhoneNumber",
    "ProvisioningRun",
    "ProvisioningStepRecord",
    "Tenant",
    "WebhookEvent",
]
