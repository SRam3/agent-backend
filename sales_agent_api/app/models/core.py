"""SQLAlchemy 2.0 ORM models — mapped exactly to the deployed sales_ai schema."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# clients
# ---------------------------------------------------------------------------
class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    chakra_phone_number_id: Mapped[Optional[str]] = mapped_column(String(50))
    chakra_secret_ref: Mapped[Optional[str]] = mapped_column(String(255))
    system_prompt_template: Mapped[Optional[str]] = mapped_column(Text)
    ai_model: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("'gpt-4o-mini'")
    )
    ai_temperature: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("0.3")
    )
    max_tool_calls_per_turn: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("3")
    )
    business_rules: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    message_retention_days: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    # relationships
    client_users: Mapped[list["ClientUser"]] = relationship("ClientUser", back_populates="client")
    products: Mapped[list["Product"]] = relationship("Product", back_populates="client")
    conversations: Mapped[list["Conversation"]] = relationship("Conversation", back_populates="client", foreign_keys="[Conversation.client_id]")


# ---------------------------------------------------------------------------
# client_users
# ---------------------------------------------------------------------------
class ClientUser(Base):
    __tablename__ = "client_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    whatsapp_id: Mapped[Optional[str]] = mapped_column(String(50))
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    address: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(String(100))
    identification_number: Mapped[Optional[str]] = mapped_column(String(50))
    first_contact_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_contact_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    is_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    client: Mapped["Client"] = relationship(back_populates="client_users")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="client_user")
    leads: Mapped[list["Lead"]] = relationship(back_populates="client_user")
    orders: Mapped[list["Order"]] = relationship(back_populates="client_user")


# ---------------------------------------------------------------------------
# products
# ---------------------------------------------------------------------------
class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    sku: Mapped[Optional[str]] = mapped_column(String(100))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    ai_description: Mapped[Optional[str]] = mapped_column(Text)
    image_url: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    client: Mapped["Client"] = relationship("Client", back_populates="products", foreign_keys=[client_id])
    order_line_items: Mapped[list["OrderLineItem"]] = relationship("OrderLineItem", back_populates="product")


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------
class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    client_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("client_users.id"), nullable=False
    )
    state: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'active'")
    )
    previous_state: Mapped[Optional[str]] = mapped_column(String(30))
    extracted_context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id")
    )
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id")
    )
    assigned_operator_id: Mapped[Optional[str]] = mapped_column(String(100))
    escalation_reason: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    agent_turn_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # strategy tracking (migration 002)
    active_goal: Mapped[Optional[str]] = mapped_column(String(50))
    current_checkpoint: Mapped[Optional[str]] = mapped_column(String(100))
    progress_pct: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("0"))
    strategy_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_strategy_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    strategy_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)

    client: Mapped["Client"] = relationship("Client", back_populates="conversations", foreign_keys=[client_id])
    client_user: Mapped["ClientUser"] = relationship("ClientUser", back_populates="conversations", foreign_keys=[client_user_id])
    messages: Mapped[list["Message"]] = relationship("Message", back_populates="conversation", foreign_keys="[Message.conversation_id]")
    # One-way relationships to avoid circular back_populates issues
    lead: Mapped[Optional["Lead"]] = relationship(
        "Lead", foreign_keys=[lead_id], viewonly=True
    )
    order: Mapped[Optional["Order"]] = relationship(
        "Order", foreign_keys=[order_id], viewonly=True
    )


# ---------------------------------------------------------------------------
# leads
# ---------------------------------------------------------------------------
class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    client_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("client_users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'new'")
    )
    intent: Mapped[Optional[str]] = mapped_column(String(255))
    score: Mapped[Optional[int]] = mapped_column(Integer)
    qualification_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    source_conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id")
    )
    assigned_to: Mapped[Optional[str]] = mapped_column(String(100))
    qualified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    won_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lost_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lost_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    client_user: Mapped["ClientUser"] = relationship("ClientUser", back_populates="leads", foreign_keys=[client_user_id])
    source_conversation: Mapped[Optional["Conversation"]] = relationship(
        "Conversation", foreign_keys=[source_conversation_id], viewonly=True
    )
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="lead", foreign_keys="[Order.lead_id]")


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------
class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    message_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'text'")
    )
    content: Mapped[Optional[str]] = mapped_column(Text)
    media_url: Mapped[Optional[str]] = mapped_column(String(2048))
    media_mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    chakra_message_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("uuid_generate_v4()::text")
    )
    delivery_status: Mapped[Optional[str]] = mapped_column(String(20))
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ai_model_used: Mapped[Optional[str]] = mapped_column(String(100))
    ai_prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    ai_completion_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    ai_latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    proposed_action: Mapped[Optional[str]] = mapped_column(String(50))
    action_approved: Mapped[Optional[bool]] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # decision explainability (migration 002)
    proposed_action_payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    extracted_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    backend_decision_reason: Mapped[Optional[str]] = mapped_column(Text)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages", foreign_keys=[conversation_id])


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    client_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("client_users.id"), nullable=False
    )
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'draft'")
    )
    shipping_name: Mapped[Optional[str]] = mapped_column(String(255))
    shipping_address: Mapped[Optional[str]] = mapped_column(Text)
    shipping_city: Mapped[Optional[str]] = mapped_column(String(100))
    shipping_phone: Mapped[Optional[str]] = mapped_column(String(20))
    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    shipping_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    source_conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id")
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    client_user: Mapped["ClientUser"] = relationship("ClientUser", back_populates="orders", foreign_keys=[client_user_id])
    lead: Mapped[Optional["Lead"]] = relationship("Lead", back_populates="orders", foreign_keys=[lead_id])
    source_conversation: Mapped[Optional["Conversation"]] = relationship(
        "Conversation", foreign_keys=[source_conversation_id], viewonly=True
    )
    line_items: Mapped[list["OrderLineItem"]] = relationship(
        "OrderLineItem", back_populates="order", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# order_line_items
# ---------------------------------------------------------------------------
class OrderLineItem(Base):
    __tablename__ = "order_line_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    order: Mapped["Order"] = relationship("Order", back_populates="line_items", foreign_keys=[order_id])
    product: Mapped["Product"] = relationship("Product", back_populates="order_line_items", foreign_keys=[product_id])


# ---------------------------------------------------------------------------
# audit_log
# ---------------------------------------------------------------------------
class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[Optional[str]] = mapped_column(String(100))
    old_value: Mapped[Optional[dict]] = mapped_column(JSONB)
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
