from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional
from uuid import UUID

from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, DateTime, ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB, NUMERIC
from sqlalchemy.types import Enum as SAEnum


class ConversationStatus(str, Enum):
    open = "open"
    closed = "closed"
    pending = "pending"


class LeadStatus(str, Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    lost = "lost"


class OrderStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"


class MessageSender(str, Enum):
    user = "user"
    agent = "agent"
    system = "system"


class MessageDirection(str, Enum):
    incoming = "incoming"
    outgoing = "outgoing"


# -------------------
# Helper columns
# -------------------
def created_ts_col():
    return Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


def updated_ts_col():
    return Column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=text("now()"),
        nullable=False,
    )


# -------------------
# Tables
# -------------------
class Client(SQLModel, table=True):
    __tablename__ = "clients"

    client_id: Optional[UUID] = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=text("uuid_generate_v4()"),
        ),
    )
    name: str = Field(index=True)
    industry: Optional[str] = None
    config: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
    password_hash: str

    created_at: datetime = Field(sa_column=created_ts_col())
    updated_at: datetime = Field(sa_column=updated_ts_col())

    # ORM conveniences (do not alter DB)
    users: List["ClientUser"] = Relationship(back_populates="client")
    products: List["Product"] = Relationship(back_populates="client")
    conversations: List["Conversation"] = Relationship(back_populates="client")
    messages: List["Message"] = Relationship(back_populates="client")


class ClientUser(SQLModel, table=True):
    __tablename__ = "client_users"
    __table_args__ = (
        UniqueConstraint("client_id", "phone", name="uq_client_users_client_phone"),
        UniqueConstraint("client_id", "email", name="uq_client_users_client_email"),
    )

    user_id: Optional[UUID] = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=text("uuid_generate_v4()"),
        ),
    )
    client_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True), ForeignKey("clients.client_id"), nullable=False
        )
    )
    name: Optional[str] = None
    phone: Optional[str] = Field(default=None, index=True)
    email: Optional[str] = Field(default=None, index=True)

    address: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
    metadata: Optional[dict] = Field(default=None, sa_column=Column(JSONB))

    created_at: datetime = Field(sa_column=created_ts_col())
    updated_at: datetime = Field(sa_column=updated_ts_col())

    client: Optional[Client] = Relationship(back_populates="users")
    conversations: List["Conversation"] = Relationship(back_populates="user")
    orders: List["Order"] = Relationship(back_populates="user")
    leads: List["Lead"] = Relationship(back_populates="user")


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    conversation_id: Optional[UUID] = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=text("uuid_generate_v4()"),
        ),
    )
    client_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True), ForeignKey("clients.client_id"), nullable=False
        )
    )
    user_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True), ForeignKey("client_users.user_id"), nullable=False
        )
    )

    started_at: datetime = Field(sa_column=created_ts_col())
    ended_at: Optional[datetime] = None

    status: ConversationStatus = Field(
        sa_column=Column(
            SAEnum(ConversationStatus, name="conversation_status"),
            nullable=False,
            server_default="open",
        )
    )

    created_at: datetime = Field(sa_column=created_ts_col())
    updated_at: datetime = Field(sa_column=updated_ts_col())

    client: Optional[Client] = Relationship(back_populates="conversations")
    user: Optional[ClientUser] = Relationship(back_populates="conversations")
    messages: List["Message"] = Relationship(back_populates="conversation")
    leads: List["Lead"] = Relationship(back_populates="conversation")


class Lead(SQLModel, table=True):
    __tablename__ = "leads"

    lead_id: Optional[UUID] = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=text("uuid_generate_v4()"),
        ),
    )
    client_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True), ForeignKey("clients.client_id"), nullable=False
        )
    )
    user_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True), ForeignKey("client_users.user_id"), nullable=False
        )
    )
    conversation_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("conversations.conversation_id"),
            nullable=False,
        )
    )

    status: LeadStatus = Field(
        sa_column=Column(
            SAEnum(LeadStatus, name="lead_status"),
            nullable=False,
            server_default="new",
        )
    )

    created_at: datetime = Field(sa_column=created_ts_col())
    updated_at: datetime = Field(sa_column=updated_ts_col())

    user: Optional[ClientUser] = Relationship(back_populates="leads")
    conversation: Optional[Conversation] = Relationship(back_populates="leads")


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    message_id: Optional[UUID] = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=text("uuid_generate_v4()"),
        ),
    )
    conversation_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("conversations.conversation_id"),
            nullable=False,
        )
    )
    client_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True), ForeignKey("clients.client_id"), nullable=False
        )
    )

    sender: MessageSender = Field(
        sa_column=Column(SAEnum(MessageSender, name="message_sender"), nullable=False)
    )
    direction: MessageDirection = Field(
        sa_column=Column(
            SAEnum(MessageDirection, name="message_direction"), nullable=False
        )
    )

    content: Optional[str] = None
    content_type: Optional[str] = None

    # keep column name "timestamp" to match your schema
    timestamp: datetime = Field(sa_column=created_ts_col())
    updated_at: datetime = Field(sa_column=updated_ts_col())

    conversation: Optional[Conversation] = Relationship(back_populates="messages")
    client: Optional[Client] = Relationship(back_populates="messages")


class Product(SQLModel, table=True):
    __tablename__ = "products"

    product_id: Optional[UUID] = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=text("uuid_generate_v4()"),
        ),
    )
    client_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True), ForeignKey("clients.client_id"), nullable=False
        )
    )
    name: str
    description: Optional[str] = None
    price: Decimal = Field(sa_column=Column(NUMERIC, nullable=False))
    image_url: Optional[str] = None
    metadata: Optional[dict] = Field(default=None, sa_column=Column(JSONB))

    created_at: datetime = Field(sa_column=created_ts_col())
    updated_at: datetime = Field(sa_column=updated_ts_col())

    client: Optional[Client] = Relationship(back_populates="products")
    orders: List["Order"] = Relationship(back_populates="product")


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    order_id: Optional[UUID] = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=text("uuid_generate_v4()"),
        ),
    )
    client_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True), ForeignKey("clients.client_id"), nullable=False
        )
    )
    user_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True), ForeignKey("client_users.user_id"), nullable=False
        )
    )
    product_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True), ForeignKey("products.product_id"), nullable=False
        )
    )

    quantity: int = 1
    status: OrderStatus = Field(
        sa_column=Column(
            SAEnum(OrderStatus, name="order_status"),
            nullable=False,
            server_default="pending",
        )
    )
    total: Decimal = Field(sa_column=Column(NUMERIC, nullable=False))

    created_at: datetime = Field(sa_column=created_ts_col())
    updated_at: datetime = Field(sa_column=updated_ts_col())

    user: Optional[ClientUser] = Relationship(back_populates="orders")
    product: Optional[Product] = Relationship(back_populates="orders")
