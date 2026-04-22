"""SQLAlchemy 2.0 ORM models — mapped exactly to the deployed sales_ai schema."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
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
    system_prompt_template: Mapped[Optional[str]] = mapped_column(Text)
    ai_model: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("'gpt-4o-mini'")
    )
    ai_temperature: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("0.3")
    )
    business_rules: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
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
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    # Persistent customer profile across conversations. Shape documented in
    # migration 007: {first_name, full_name, email, city, shipping_address,
    # preferences, purchase_count, purchases, last_conversation_summary}.
    profile: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    first_contact_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_contact_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    is_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    client: Mapped["Client"] = relationship(back_populates="client_users")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="client_user")


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
    ai_description: Mapped[Optional[str]] = mapped_column(Text)
    image_url: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    client: Mapped["Client"] = relationship("Client", back_populates="products", foreign_keys=[client_id])


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
    extracted_context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    message_count: Mapped[int] = mapped_column(
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
    chakra_message_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True)
    ai_model_used: Mapped[Optional[str]] = mapped_column(String(100))
    ai_prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    ai_completion_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    ai_latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    extracted_data: Mapped[Optional[dict]] = mapped_column(JSONB)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages", foreign_keys=[conversation_id])


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
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
