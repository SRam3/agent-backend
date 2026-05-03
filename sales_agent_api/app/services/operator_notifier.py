"""Telegram operator notifier.

Triggered after auto_escalate (conversation reaches human_handoff with
all purchase data collected). Sends a structured message with the
customer + order details to every chat_id listed in
clients.business_rules.operators.telegram_chat_ids.

Why Telegram (and not WhatsApp / email):
  - Push notification on the operator's phone within 1-2s
  - Free at our volume
  - Setup is 10 minutes via @BotFather
  - WhatsApp Business requires pre-approved templates for outbound
    operator-direction messages — too much overhead for a small team

Operating model:
  - One Telegram bot for the platform, owned by us
  - Each operator sends /start to the bot once to receive their chat_id
  - chat_ids are configured per client in business_rules.operators
  - The bot token lives in env (TELEGRAM_BOT_TOKEN), bootstrapped from
    Key Vault secret 'telegram-bot-token' on app startup

Failure model: best-effort. Telegram down or chat_id invalid → log
warning + add side_effect; never break the conversation flow.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Client, ClientUser, Conversation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM-style sender abstraction so tests can inject a fake
# ---------------------------------------------------------------------------
class TelegramSender(Protocol):
    async def __call__(self, chat_id: str, text: str) -> bool:
        """Returns True on success, False on failure (chat not found, etc.)."""
        ...


_TELEGRAM_API_BASE = "https://api.telegram.org"


async def _http_telegram_sender(chat_id: str, text: str) -> bool:
    """Default sender: posts to Telegram Bot API via httpx."""
    import httpx  # lazy import to keep tests light

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set; skipping operator notification")
        return False

    url = f"{_TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload)
            if r.status_code == 200:
                return True
            logger.warning(
                "Telegram sendMessage failed: chat_id=%s status=%s body=%s",
                chat_id, r.status_code, r.text[:200],
            )
            return False
    except Exception as exc:
        logger.warning("Telegram sendMessage crashed: chat_id=%s err=%s", chat_id, exc)
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
async def notify_operators(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    sender: Optional[TelegramSender] = None,
) -> dict:
    """Build the operator notification text and send it to every configured
    chat_id. Returns a summary {sent_count, failure_count, chat_ids_targeted}.

    Best-effort: per-chat failures are counted but never raised.
    """
    sender = sender or _http_telegram_sender

    # Load conversation + client + customer for the message body
    conv_row = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation: Optional[Conversation] = conv_row.scalar_one_or_none()
    if conversation is None:
        return {"sent_count": 0, "failure_count": 0, "chat_ids_targeted": 0,
                "skipped": "conversation_not_found"}

    client_row = await session.execute(select(Client).where(Client.id == client_id))
    client: Optional[Client] = client_row.scalar_one_or_none()
    if client is None:
        return {"sent_count": 0, "failure_count": 0, "chat_ids_targeted": 0,
                "skipped": "client_not_found"}

    cu_row = await session.execute(
        select(ClientUser).where(ClientUser.id == conversation.client_user_id)
    )
    client_user: Optional[ClientUser] = cu_row.scalar_one_or_none()

    chat_ids = _extract_chat_ids(client.business_rules or {})
    if not chat_ids:
        return {"sent_count": 0, "failure_count": 0, "chat_ids_targeted": 0,
                "skipped": "no_operators_configured"}

    text = build_operator_message(
        client_name=client.name,
        conversation_id=conversation_id,
        extracted_context=conversation.extracted_context or {},
        customer_profile=(client_user.profile if client_user else None) or {},
    )

    sent = 0
    failed = 0
    for chat_id in chat_ids:
        ok = await sender(chat_id, text)
        if ok:
            sent += 1
        else:
            failed += 1

    return {
        "sent_count": sent,
        "failure_count": failed,
        "chat_ids_targeted": len(chat_ids),
    }


# ---------------------------------------------------------------------------
# Pure helpers — testable without DB or HTTP
# ---------------------------------------------------------------------------
def _extract_chat_ids(business_rules: dict) -> list[str]:
    """Pull the operator chat_id list out of business_rules. Always returns
    a list of strings; coerces ints (Telegram IDs are large ints) to str."""
    operators = (business_rules or {}).get("operators") or {}
    raw = operators.get("telegram_chat_ids") or []
    return [str(x) for x in raw if x]


def build_operator_message(
    client_name: str,
    conversation_id: uuid.UUID,
    extracted_context: dict,
    customer_profile: dict,
) -> str:
    """Compose the Telegram message body. HTML format (parse_mode=HTML)."""
    ctx = extracted_context or {}
    profile = customer_profile or {}

    # Customer identity: prefer extracted_context (this conversation),
    # fall back to profile (long-lived).
    full_name = ctx.get("full_name") or profile.get("full_name") or "(sin nombre)"
    phone = ctx.get("phone") or profile.get("phone") or "(sin teléfono)"
    city = ctx.get("shipping_city") or profile.get("city") or "(sin ciudad)"
    address = ctx.get("shipping_address") or profile.get("shipping_address") or "(sin dirección)"

    quantity = ctx.get("quantity") or "(sin cantidad)"
    grind = ctx.get("grind_preference") or ""

    now_cot = _now_in_cot_str()

    lines = [
        f"<b>NUEVO PEDIDO PARA REVISAR</b> — {_escape_html(client_name)}",
        "",
        f"<b>Cliente:</b> {_escape_html(full_name)}",
        f"<b>Teléfono:</b> {_escape_html(str(phone))}",
        f"<b>Ciudad:</b> {_escape_html(str(city))}",
        f"<b>Dirección:</b> {_escape_html(str(address))}",
        "",
        "<b>Pedido:</b>",
        f"  • Cantidad: {_escape_html(str(quantity))}",
    ]
    if grind:
        lines.append(f"  • Molido: {_escape_html(str(grind))}")
    lines.extend([
        "",
        f"<b>Conversación:</b> <code>{conversation_id}</code>",
        f"<b>Detectado:</b> {now_cot}",
    ])
    return "\n".join(lines)


def _escape_html(s: str) -> str:
    """Telegram HTML mode requires escaping <, >, &."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _now_in_cot_str() -> str:
    """Render now() in Colombia local time without a timezone library
    (Colombia is UTC-5 with no DST)."""
    from datetime import timedelta as _td
    cot = datetime.now(timezone.utc) - _td(hours=5)
    return cot.strftime("%Y-%m-%d %H:%M:%S COT")
