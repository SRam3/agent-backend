"""P14 — identity resolution for inbound messages (BSUID-first).

WhatsApp's number-privacy rollout means the phone number is no longer a
reliable identity. These tests pin the resolution ORDER and, just as
importantly, what the fallback deliberately does NOT do (write the bsuid back
onto a legacy row — that merge is the identity redesign's job, not this one).

Pure tests: the session is a stub that records what was asked and replays
canned answers, per the repo convention that tests/services needs no DB.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.api.v1.ingest import IngestMessageRequest
from app.models.core import ClientUser
from app.services.ingest import _mask_identity, _mask_phone, _resolve_client_user

CLIENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 8, 4, 19, 51, 14, tzinfo=timezone.utc)
BSUID = "CO.0000000000001234"
PHONE = "570000005678"


# ---------------------------------------------------------------------------
# Session stub
# ---------------------------------------------------------------------------
class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalar_one(self):
        return self._row


class FakeSession:
    """Records each statement and returns the next canned result."""

    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self._results.pop(0))

    # -- helpers for assertions --------------------------------------------
    @property
    def kinds(self) -> list[str]:
        """'select' or 'insert', in the order issued."""
        return [
            "insert" if s.__class__.__name__.lower().startswith("insert") else "select"
            for s in self.statements
        ]

    def sql(self, i: int) -> str:
        return str(self.statements[i]).lower()


def _existing_user(**kw) -> ClientUser:
    user = ClientUser()
    user.id = uuid.uuid4()
    user.client_id = CLIENT_ID
    user.bsuid = kw.get("bsuid")
    user.phone_number = kw.get("phone_number")
    user.display_name = kw.get("display_name")
    user.is_blocked = False
    return user


async def _resolve(session, **kw):
    return await _resolve_client_user(
        session=session,
        client_id=CLIENT_ID,
        bsuid=kw.get("bsuid"),
        phone_number=kw.get("phone_number"),
        display_name=kw.get("display_name"),
        now=NOW,
    )


# ---------------------------------------------------------------------------
# Resolution order
# ---------------------------------------------------------------------------
def test_known_bsuid_reuses_row_without_touching_phone():
    """A customer we already know by BSUID resolves on the first lookup."""
    known = _existing_user(bsuid=BSUID)
    session = FakeSession([known])

    got = asyncio.run(_resolve(session, bsuid=BSUID, display_name="Cielo"))

    assert got is known
    assert session.kinds == ["select"]  # no phone lookup, no insert
    assert got.display_name == "Cielo"
    assert got.last_contact_at == NOW


def test_privacy_customer_with_no_phone_is_created():
    """The case that used to be dropped: BSUID only, no phone at all."""
    created = _existing_user(bsuid=BSUID)
    session = FakeSession([None, created])

    got = asyncio.run(_resolve(session, bsuid=BSUID, display_name="Cielo"))

    assert got is created
    # Only one lookup: with no phone there is nothing to fall back to.
    assert session.kinds == ["select", "insert"]


def test_new_bsuid_falls_back_to_phone_for_legacy_customer():
    """A pre-P14 customer is stored phone-keyed with bsuid NULL — reuse them."""
    legacy = _existing_user(phone_number=PHONE, display_name="Sebastian")
    session = FakeSession([None, legacy])

    got = asyncio.run(_resolve(session, bsuid=BSUID, phone_number=PHONE))

    assert got is legacy
    assert session.kinds == ["select", "select"]  # bsuid, then phone. No insert.


def test_phone_fallback_does_not_write_bsuid_back():
    """The merge is the identity ADR's job. This phase must not do it."""
    legacy = _existing_user(phone_number=PHONE)
    session = FakeSession([None, legacy])

    got = asyncio.run(_resolve(session, bsuid=BSUID, phone_number=PHONE))

    assert got.bsuid is None, "fallback must not claim the legacy row for this BSUID"
    assert got.phone_number == PHONE


def test_unknown_bsuid_and_unknown_phone_inserts():
    created = _existing_user(bsuid=BSUID, phone_number=PHONE)
    session = FakeSession([None, None, created])

    got = asyncio.run(_resolve(session, bsuid=BSUID, phone_number=PHONE))

    assert got is created
    assert session.kinds == ["select", "select", "insert"]


def test_insert_is_an_upsert_so_a_concurrent_ingest_converges():
    """The advisory lock is taken later, so this step really can race."""
    created = _existing_user(bsuid=BSUID)
    session = FakeSession([None, created])

    asyncio.run(_resolve(session, bsuid=BSUID))

    assert "on conflict" in session.sql(1)


def test_no_bsuid_uses_the_original_phone_keyed_upsert():
    """n8n before Fase 3 sends only a phone. That path must be untouched."""
    created = _existing_user(phone_number=PHONE)
    session = FakeSession([created])

    got = asyncio.run(_resolve(session, phone_number=PHONE, display_name="Sebastian"))

    assert got is created
    assert session.kinds == ["insert"]  # straight to the upsert, no lookups
    assert "on conflict on constraint uq_client_user_phone" in session.sql(0)


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------
def test_request_accepts_bsuid_only():
    req = IngestMessageRequest(chakra_message_id="wamid.x", bsuid=BSUID, content="Hola")
    assert req.bsuid == BSUID
    assert req.phone_number is None


def test_request_accepts_phone_only_for_the_current_n8n():
    req = IngestMessageRequest(
        chakra_message_id="wamid.x", phone_number=PHONE, content="Hola"
    )
    assert req.phone_number == PHONE
    assert req.bsuid is None


def test_request_rejects_a_message_with_no_identity_at_all():
    with pytest.raises(ValidationError, match="at least one"):
        IngestMessageRequest(chakra_message_id="wamid.x", content="Hola")


# ---------------------------------------------------------------------------
# Masking — must survive a missing phone
# ---------------------------------------------------------------------------
def test_mask_phone_tolerates_none():
    assert _mask_phone(None) == "****"


def test_mask_identity_prefers_bsuid_and_never_says_none():
    assert _mask_identity(BSUID, None).endswith("1234")
    assert _mask_identity(None, PHONE).endswith("5678")
    assert _mask_identity(None, None) == "****"
    assert "None" not in _mask_identity(None, None)
