from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4()}"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("cus"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    phone_lookup_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    phone_last4: Mapped[str] = mapped_column(String(4), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    permissions: Mapped[list["Permission"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    purchases: Mapped[list["Purchase"]] = relationship(back_populates="customer")


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("customer_id", "permission_type", name="uq_permission_customer_type"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("per"))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    permission_type: Mapped[str] = mapped_column(String(24), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    choice_recorded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    customer: Mapped[Customer] = relationship(back_populates="permissions")


class Purchase(Base):
    __tablename__ = "purchases"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_purchase_idempotency_key"),
        Index("ix_purchase_customer_date", "customer_id", "purchased_at"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("pur"))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="purchases")
    items: Mapped[list["PurchaseItem"]] = relationship(back_populates="purchase", cascade="all, delete-orphan")


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("itm"))
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    purchase: Mapped[Purchase] = relationship(back_populates="items")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("cam"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    product_name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    available_stock: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    message_template_input: Mapped[str] = mapped_column(Text, nullable=False)
    message_preview: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(32), default="whatsapp_simulated", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessageJob(Base):
    __tablename__ = "message_jobs"
    __table_args__ = (Index("ix_job_customer_created", "customer_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("job"))
    job_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(32), default="whatsapp_simulated", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    purchase_id: Mapped[str | None] = mapped_column(ForeignKey("purchases.id"), nullable=True, index=True)
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True, index=True)
    message_body: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
