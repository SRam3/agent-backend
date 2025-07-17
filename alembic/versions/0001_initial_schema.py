"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2025-07-11 18:18:59.324328

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conversation_status = sa.Enum(
        "open",
        "pending",
        "closed",
        name="conversation_status",
    )
    lead_status = sa.Enum(
        "new",
        "interested",
        "negotiating",
        "closed_won",
        "closed_lost",
        name="lead_status",
    )


    op.create_table(
        "clients",
        sa.Column(
            "id",
            sa.Integer,
            sa.Identity(always=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("industry", sa.Text, nullable=True),
        sa.Column("phone", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "client_users",
        sa.Column("id", sa.Integer, sa.Identity(always=True), primary_key=True),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("phone", sa.Text, nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer, sa.Identity(always=True), primary_key=True),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id"), nullable=False),
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("client_users.id"), nullable=False
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", conversation_status, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "conversation_id", sa.Integer, sa.ForeignKey("conversations.id"), nullable=False
        ),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("content_type", sa.Text, nullable=True),
        sa.Column("wa_msg_id", sa.Text, nullable=True),
        sa.Column("wa_status", sa.Text, nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
    )

    op.create_table(
        "leads",
        sa.Column("id", sa.Integer, sa.Identity(always=True), primary_key=True),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id"), nullable=False),
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("client_users.id"), nullable=False
        ),
        sa.Column(
            "conversation_id",
            sa.Integer,
            sa.ForeignKey("conversations.id"),
            nullable=True,
        ),
        sa.Column("status", lead_status, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("leads")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("client_users")
    op.drop_table("clients")
    op.execute(sa.text("DROP TYPE IF EXISTS lead_status"))
    op.execute(sa.text("DROP TYPE IF EXISTS conversation_status"))
