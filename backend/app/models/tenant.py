"""The tenant — one signed-up business."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AgentMode,
    BusinessType,
    TenantPlan,
    TenantStatus,
    enum_column,
)

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.agent_config import AgentConfig
    from app.models.billing import Subscription
    from app.models.business_profile import BusinessProfile
    from app.models.call import Call
    from app.models.identity import Membership
    from app.models.phone_number import PhoneNumber
    from app.models.provisioning import ProvisioningRun


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A business using the receptionist.

    ``contact_email`` is stored lower-cased and carries a partial unique index
    over live statuses: one business cannot hold two live signups, which is the
    database half of "submitting the same form twice does not buy two numbers"
    (docs/01_PLAN_POC.md, definition of done 4). A cancelled or abandoned tenant
    leaves the index, so the same business can legitimately sign up again.
    """

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    business_type: Mapped[BusinessType] = mapped_column(
        enum_column(BusinessType, "business_type"),
        nullable=False,
        default=BusinessType.OTHER,
    )
    contact_email: Mapped[str] = mapped_column(String(320), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    # Requested on the form; may be absent, in which case the purchase step falls
    # back to the state derived from the contact number.
    area_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # IANA name, derived from the area code during the validate step. Never
    # guessed at call time: "9-6" means nothing without it.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Los_Angeles")
    plan: Mapped[TenantPlan] = mapped_column(
        enum_column(TenantPlan, "tenant_plan"),
        nullable=False,
        default=TenantPlan.TRIAL,
    )
    #: Which ElevenLabs topology serves this tenant. Per-tenant rather than a
    #: global setting so the migration to shared vertical agents can move
    #: customers in batches, verify each batch with a test call, and roll a
    #: batch back on its own -- rather than being one switch for everyone.
    agent_mode: Mapped[AgentMode] = mapped_column(
        enum_column(AgentMode, "agent_mode"),
        nullable=False,
        default=AgentMode.PER_TENANT,
        server_default=AgentMode.PER_TENANT.value,
    )
    status: Mapped[TenantStatus] = mapped_column(
        enum_column(TenantStatus, "tenant_status"),
        nullable=False,
        default=TenantStatus.PENDING,
    )

    business_profile: Mapped[BusinessProfile | None] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", uselist=False
    )
    agent_configs: Mapped[list[AgentConfig]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    phone_numbers: Mapped[list[PhoneNumber]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    agents: Mapped[list[Agent]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    provisioning_runs: Mapped[list[ProvisioningRun]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    calls: Mapped[list[Call]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    #: The tenant's live billing state. `uselist=False` because the partial
    #: unique index guarantees at most one entitled subscription per tenant.
    subscription: Mapped[Subscription | None] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        Index(
            "uq_tenants_live_contact_email",
            "contact_email",
            unique=True,
            postgresql_where=text(
                f"status IN ('{TenantStatus.PENDING.value}', '{TenantStatus.ACTIVE.value}')"
            ),
        ),
        Index("ix_tenants_status", "status"),
    )
