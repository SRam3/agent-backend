from typing import Optional, List
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field, Relationship


class Client(SQLModel, table=True):
    __tablename__ = "clients"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    industry: Optional[str] = None
    config: Optional[dict] = Field(default=None, sa_column_kwargs={"type_": "JSON"})
    password: Optional[str] = None  # Must be hashed
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    users: List["ClientUser"] = Relationship(back_populates="client")
    products: List["Product"] = Relationship(back_populates="client")
    conversations: List["Conversation"] = Relationship(back_populates="client")
    leads: List["Lead"] = Relationship(back_populates="client")
    orders: List["Order"] = Relationship(back_populates="client")


class ClientUser(SQLModel, table=True):
    __tablename__ = "client_users"

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="clients.id")
    name: str
    phone: str
    email: Optional[str] = None
    address: Optional[str] = None
    metadata: Optional[dict] = Field(default=None, sa_column_kwargs={"type_": "JSON"})
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    client: Optional[Client] = Relationship(back_populates="users")
    conversations: List["Conversation"] = Relationship(back_populates="user")
    leads: List["Lead"] = Relationship(back_populates="user")
    orders: List["Order"] = Relationship(back_populates="user")


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="clients.id")
    user_id: int = Field(foreign_key="client_users.id")
    started_at: datetime = Field(default_factory=datetime.utcnow)
    status: str  # open, pending, closed

    client: Optional[Client] = Relationship(back_populates="conversations")
    user: Optional[ClientUser] = Relationship(back_populates="conversations")
    messages: List["Message"] = Relationship(back_populates="conversation")


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversations.id")
    client_id: int = Field(foreign_key="clients.id")
    sender: str  # whatsapp_user, client, agent
    direction: str  # inbound, outbound
    content: str
    content_type: str = "text"  # or image, audio, etc.
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    conversation: Optional[Conversation] = Relationship(back_populates="messages")


class Lead(SQLModel, table=True):
    __tablename__ = "leads"

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="clients.id")
    user_id: int = Field(foreign_key="client_users.id")
    conversation_id: Optional[int] = Field(default=None, foreign_key="conversations.id")
    status: str  # new, interested, negotiating, closed_won, closed_lost
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    client: Optional[Client] = Relationship(back_populates="leads")
    user: Optional[ClientUser] = Relationship(back_populates="leads")
    conversation: Optional[Conversation] = Relationship()


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="clients.id")
    user_id: int = Field(foreign_key="client_users.id")
    product_id: int = Field(foreign_key="products.id")
    quantity: int
    status: str  # pending, paid, delivered, etc.
    total: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    client: Optional[Client] = Relationship(back_populates="orders")
    user: Optional[ClientUser] = Relationship(back_populates="orders")
    product: Optional["Product"] = Relationship(back_populates="orders")


class Product(SQLModel, table=True):
    __tablename__ = "products"

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="clients.id")
    name: str
    description: Optional[str] = None
    price: float
    image_url: Optional[str] = None
    metadata: Optional[dict] = Field(default=None, sa_column_kwargs={"type_": "jsonb"})
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    client: Optional[Client] = Relationship(back_populates="products")
    orders: List[Order] = Relationship(back_populates="product")
