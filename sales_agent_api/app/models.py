from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship


class Client(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str

    users: List["ClientUser"] | None = Relationship(back_populates="client")


class ClientUser(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")
    name: str
    phone_number: str

    client: Optional[Client] = Relationship(back_populates="users")
