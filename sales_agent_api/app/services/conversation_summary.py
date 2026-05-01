"""Conversation compaction service.

When a conversation goes quiet for >24h or is closed/handed off, we compact
it into a structured summary that lives in client_users.profile. The next
conversation starts with the LLM already knowing who the customer is, what
they were buying, and how they like to be talked to.

This is the "vendor with memory" mechanism. The agent itself never calls
the LLM (per ADR-002); this service does — but it's an internal task,
asynchronous to the chat turn, not part of the agent loop.

Contract:
  summarize_conversation(session, conversation_id, llm=None) -> dict | None

  - Returns the summary dict that was written to profile.last_conversation_summary,
    or None if the conversation was empty / OPENAI_API_KEY missing / LLM failed.
  - The DB write is included; caller controls commit.
  - `llm` is injectable for tests; default uses OpenAI gpt-4o-mini.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import ClientUser, Conversation, Message, Product

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured-output schema (OpenAI json_schema, strict mode)
# ---------------------------------------------------------------------------
SUMMARY_SCHEMA: dict = {
    "name": "conversation_summary",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "summary",
            "outcome",
            "interest_level",
            "language",
            "communication_style",
            "products_discussed",
            "objections",
            "pending_intent",
        ],
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "Two or three sentences in Spanish describing what the "
                    "customer wanted, what was agreed, and how the conversation "
                    "ended. Concrete, no fluff."
                ),
            },
            "outcome": {
                "type": "string",
                "enum": [
                    "purchased",
                    "handed_off",
                    "abandoned_at_product",
                    "abandoned_at_lead",
                    "abandoned_at_shipping",
                    "abandoned_at_confirmation",
                    "abandoned_at_payment",
                    "no_intent",
                ],
            },
            "interest_level": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "language": {
                "type": "string",
                "enum": ["es", "en"],
                "description": "Language the customer spoke.",
            },
            "communication_style": {
                "type": "string",
                "enum": ["formal", "casual", "direct"],
                "description": (
                    "How the customer writes. 'direct' = short and to the point, "
                    "'casual' = warm/informal, 'formal' = uses usted / business tone."
                ),
            },
            "products_discussed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Product UUIDs mentioned. Empty list if none.",
            },
            "objections": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Short tags of objections raised by the customer "
                    "(e.g. 'precio', 'envío_caro', 'tiempo_entrega'). Empty if none."
                ),
            },
            "pending_intent": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["product_id", "quantity", "notes"],
                        "properties": {
                            "product_id": {"type": ["string", "null"]},
                            "quantity": {"type": ["integer", "null"]},
                            "notes": {"type": ["string", "null"]},
                        },
                    },
                ],
                "description": (
                    "What the customer was about to buy when the conversation "
                    "stopped. null if no buying intent in flight."
                ),
            },
        },
    },
}


# ---------------------------------------------------------------------------
# LLM client abstraction (so tests can inject a fake)
# ---------------------------------------------------------------------------
class SummarizerLLM(Protocol):
    async def __call__(self, system_prompt: str, user_prompt: str) -> dict:
        ...


_DEFAULT_MODEL = "gpt-4o-mini"


async def _openai_summarizer(system_prompt: str, user_prompt: str) -> dict:
    """Default LLM caller. Lazy-imports openai so tests don't need the package."""
    from openai import AsyncOpenAI  # noqa: WPS433 — lazy import is intentional

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=os.getenv("SUMMARY_MODEL", _DEFAULT_MODEL),
        temperature=0.2,
        response_format={"type": "json_schema", "json_schema": SUMMARY_SCHEMA},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    raw = response.choices[0].message.content or "{}"
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def summarize_conversation(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    llm: Optional[SummarizerLLM] = None,
) -> Optional[dict]:
    """Generate a structured summary of a conversation and persist it to the
    client_user's profile. Returns the persisted summary dict or None if it
    couldn't be generated.

    Safe to call repeatedly — overwrites profile.last_conversation_summary
    with the freshest take for the given conversation.
    """
    conv_row = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation: Optional[Conversation] = conv_row.scalar_one_or_none()
    if conversation is None:
        logger.warning("summarize_conversation: conversation %s not found", conversation_id)
        return None

    msgs_row = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    messages: list[Message] = list(msgs_row.scalars().all())
    if not messages:
        logger.info("summarize_conversation: no messages for %s, skipping", conversation_id)
        return None

    products_row = await session.execute(
        select(Product).where(Product.client_id == conversation.client_id)
    )
    product_map = {str(p.id): p.name for p in products_row.scalars().all()}

    system_prompt = _build_system_prompt(product_map)
    user_prompt = _build_user_prompt(conversation, messages, product_map)

    summarizer = llm or _openai_summarizer
    try:
        summary = await summarizer(system_prompt, user_prompt)
    except Exception as exc:  # network, JSON, missing key — never break the caller
        logger.warning(
            "summarize_conversation: LLM call failed for %s: %s",
            conversation_id, exc,
        )
        return None

    # Stamp metadata the LLM doesn't own
    summary["conversation_id"] = str(conversation_id)
    summary["summarized_at"] = datetime.now(timezone.utc).isoformat()

    await _persist_to_profile(
        session,
        client_user_id=conversation.client_user_id,
        summary=summary,
    )
    return summary


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------
def _build_system_prompt(product_map: dict[str, str]) -> str:
    catalog = "\n".join(f"  - {pid}: {name}" for pid, name in product_map.items()) or "  (catálogo vacío)"
    return (
        "Eres un asistente que comprime conversaciones de venta por WhatsApp en "
        "un resumen estructurado. Tu salida se usará en la siguiente conversación "
        "para que un agente recuerde al cliente y reduzca fricción.\n\n"
        "Reglas:\n"
        " - Sé concreto: qué quería, qué objeciones tuvo, cómo terminó.\n"
        " - El idioma se infiere del cliente, no del agente.\n"
        " - communication_style se basa en CÓMO escribe el cliente.\n"
        " - products_discussed solo contiene UUIDs del catálogo:\n"
        f"{catalog}\n"
        " - pending_intent solo si el cliente tenía intención clara de compra "
        "que NO se concretó. Si la conversación cerró sin intent, ponlo en null.\n"
        " - outcome refleja el estado real al cierre de la conversación."
    )


def _build_user_prompt(
    conversation: Conversation,
    messages: list[Message],
    product_map: dict[str, str],
) -> str:
    extracted = conversation.extracted_context or {}
    state = conversation.state

    lines: list[str] = []
    lines.append(f"CONVERSACIÓN ID: {conversation.id}")
    lines.append(f"ESTADO FINAL: {state}")
    lines.append(f"CHECKPOINT FINAL: {conversation.current_checkpoint or 'n/a'}")
    lines.append(f"PROGRESO: {conversation.progress_pct or 0}%")
    lines.append("")
    lines.append("DATOS RECOPILADOS DURANTE LA CONVERSACIÓN:")
    if extracted:
        for k, v in extracted.items():
            lines.append(f"  - {k}: {v}")
    else:
        lines.append("  (ninguno)")
    lines.append("")
    lines.append("MENSAJES (orden cronológico):")
    for m in messages:
        actor = "CLIENTE" if m.direction == "inbound" else "AGENTE"
        content = (m.content or "").strip().replace("\n", " ")
        if len(content) > 500:
            content = content[:500] + "…"
        lines.append(f"  [{actor}] {content}")
    lines.append("")
    lines.append("Genera el resumen estructurado.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
async def _persist_to_profile(
    session: AsyncSession,
    client_user_id: uuid.UUID,
    summary: dict,
) -> None:
    cu_row = await session.execute(
        select(ClientUser).where(ClientUser.id == client_user_id)
    )
    client_user: Optional[ClientUser] = cu_row.scalar_one_or_none()
    if client_user is None:
        logger.warning("summarize_conversation: client_user %s vanished", client_user_id)
        return

    profile = dict(client_user.profile or {})

    # The summary owns three top-level fields on the profile.
    profile["last_conversation_summary"] = summary
    if summary.get("language"):
        profile["language"] = summary["language"]
    if summary.get("communication_style"):
        profile["communication_style"] = summary["communication_style"]

    await session.execute(
        update(ClientUser)
        .where(ClientUser.id == client_user_id)
        .values(profile=profile)
    )


# ---------------------------------------------------------------------------
# Helpers used by the lazy hook in ingest.py
# ---------------------------------------------------------------------------
def needs_summary(profile: dict | None, conversation_id: uuid.UUID) -> bool:
    """True if the profile has no summary yet for this conversation_id."""
    if not profile:
        return True
    last = profile.get("last_conversation_summary") or {}
    return str(last.get("conversation_id")) != str(conversation_id)
