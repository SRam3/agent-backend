from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class ConversationStatus(str, Enum):
    active = "active"
    closed = "closed"
    archived = "archived"


class MessageSender(str, Enum):
    user = "user"
    agent = "agent"


class LeadStatus(str, Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    lost = "lost"
    won = "won"


class Client(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    industry: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    users: List[ClientUser] = Relationship(back_populates="client")

    def __repr__(self) -> str:
        return f"Client(id={self.id}, name={self.name})"


class ClientUser(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")
    name: str
    phone_number: str
    email: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    client: Optional[Client] = Relationship(back_populates="users")
    conversations: List[Conversation] = Relationship(back_populates="client_user")
    leads: List[Lead] = Relationship(back_populates="client_user")

    def __repr__(self) -> str:
        return f"ClientUser(id={self.id}, name={self.name})"


class Conversation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_user_id: int = Field(foreign_key="clientuser.id")
    started_at: datetime = Field(default_factory=datetime.utcnow)
    status: ConversationStatus

    client_user: Optional[ClientUser] = Relationship(back_populates="conversations")
    messages: List[Message] = Relationship(back_populates="conversation")

    def __repr__(self) -> str:
        return f"Conversation(id={self.id}, status={self.status})"


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id")
    sender: MessageSender
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    conversation: Optional[Conversation] = Relationship(back_populates="messages")

    def __repr__(self) -> str:
        return f"Message(id={self.id}, sender={self.sender})"


class Lead(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_user_id: int = Field(foreign_key="clientuser.id")
    product_name: Optional[str] = None
    value: Optional[float] = None
    status: LeadStatus
    created_at: datetime = Field(default_factory=datetime.utcnow)

    client_user: Optional[ClientUser] = Relationship(back_populates="leads")

    def __repr__(self) -> str:
        return f"Lead(id={self.id}, status={self.status})"
