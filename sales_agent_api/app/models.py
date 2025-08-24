from typing import Optional, List
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy import Column, JSON, String, ForeignKey
from sqlmodel import SQLModel, Field, Relationship
import hashlib


class Client(SQLModel, table=True):
    __tablename__ = "clients"
    __table_args__ = {"extend_existing": True}

    client_id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PGUUID(as_uuid=True), primary_key=True)
    )
    name: str
    industry: Optional[str] = None
    config: Optional[dict] = Field(default=None, sa_column=Column(JSON, name="config"))
    password_hash: Optional[str] = Field(default=None, sa_column=Column("password_hash", String) ,description="Hashed password")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    users: List["ClientUser"] = Relationship(back_populates="client")
    products: List["Product"] = Relationship(back_populates="client")
    conversations: List["Conversation"] = Relationship(back_populates="client")
    leads: List["Lead"] = Relationship(back_populates="client")
    orders: List["Order"] = Relationship(back_populates="client")

    def set_password(self, raw_password: str) -> None:
        """Hash and store the client's password."""
        self.password = hashlib.sha256(raw_password.encode()).hexdigest()


class ClientUser(SQLModel, table=True):
    __tablename__ = "client_users"
    __table_args__ = {"extend_existing": True}

    user_id: Optional[int] = Field(default=None, primary_key=True)
    client_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("clients.client_id"),  
            nullable=False
        )
    )
    name: str
    phone: str
    email: Optional[str] = None
    address: Optional[str] = None
    metadata_: Optional[dict] = Field(
        default=None, sa_column=Column("metadata", JSON), alias="metadata"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    client: Optional[Client] = Relationship(back_populates="users")
    conversations: List["Conversation"] = Relationship(back_populates="user")
    leads: List["Lead"] = Relationship(back_populates="user")
    orders: List["Order"] = Relationship(back_populates="user")

    @property
    def id(self) -> Optional[int]:
        return self.user_id


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"
    __table_args__ = {"extend_existing": True}

    conversation_id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="clients.client_id")
    user_id: int = Field(foreign_key="client_users.user_id")
    started_at: datetime = Field(default_factory=datetime.utcnow)
    status: str  # open, pending, closed

    client: Optional[Client] = Relationship(back_populates="conversations")
    user: Optional[ClientUser] = Relationship(back_populates="conversations")
    messages: List["Message"] = Relationship(back_populates="conversation")


class Message(SQLModel, table=True):
    __tablename__ = "messages"
    __table_args__ = {"extend_existing": True}

    message_id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversations.conversation_id")
    client_id: int = Field(foreign_key="clients.client_id")
    sender: str  # whatsapp user, client, agent
    direction: str  # inbound, outbound
    content: str
    content_type: str = "text"  # or image, audio, etc.
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    conversation: Optional[Conversation] = Relationship(back_populates="messages")


class Lead(SQLModel, table=True):
    __tablename__ = "leads"
    __table_args__ = {"extend_existing": True}

    lead_id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="clients.client_id")
    user_id: int = Field(foreign_key="client_users.user_id")
    conversation_id: Optional[int] = Field(
        default=None, foreign_key="conversations.conversation_id"
    )
    status: str  # new, interested, negotiating, closed_won, closed_lost
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    client: Optional[Client] = Relationship(back_populates="leads")
    user: Optional[ClientUser] = Relationship(back_populates="leads")
    conversation: Optional[Conversation] = Relationship()


class Order(SQLModel, table=True):
    __tablename__ = "orders"
    __table_args__ = {"extend_existing": True}

    order_id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="clients.client_id")
    user_id: int = Field(foreign_key="client_users.user_id")
    product_id: int = Field(foreign_key="products.product_id")
    quantity: int
    status: str  # pending, paid, delivered, etc.
    total: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    client: Optional[Client] = Relationship(back_populates="orders")
    user: Optional[ClientUser] = Relationship(back_populates="orders")
    product: Optional["Product"] = Relationship(back_populates="orders")


class Product(SQLModel, table=True):
    __tablename__ = "products"
    __table_args__ = {"extend_existing": True}

    product_id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="clients.client_id")
    name: str
    description: Optional[str] = None
    price: float
    image_url: Optional[str] = None
    metadata_: Optional[dict] = Field(
        default=None, sa_column=Column("metadata", JSON), alias="metadata"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    client: Optional[Client] = Relationship(back_populates="products")
    orders: List[Order] = Relationship(back_populates="product")
