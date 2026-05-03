"""Tests for operator_notifier helpers.

Pure Python — covers chat_id extraction and message body building. The
DB-touching notify_operators() and the HTTP _http_telegram_sender are
exercised via integration tests / a manual smoke test once the bot is
configured (see PR description).
"""
import sys
import os
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.services.operator_notifier import (
    _escape_html,
    _extract_chat_ids,
    build_operator_message,
)


# ---------------------------------------------------------------------------
# _extract_chat_ids
# ---------------------------------------------------------------------------
def test_chat_ids_from_business_rules():
    rules = {"operators": {"telegram_chat_ids": ["123456", "789012"]}}
    assert _extract_chat_ids(rules) == ["123456", "789012"]


def test_chat_ids_coerces_ints_to_str():
    """Telegram chat_ids are large positive ints; YAML/JSON may decode as int."""
    rules = {"operators": {"telegram_chat_ids": [123456789, "abc"]}}
    assert _extract_chat_ids(rules) == ["123456789", "abc"]


def test_chat_ids_drops_falsy_entries():
    rules = {"operators": {"telegram_chat_ids": ["valid", "", None, 0, "another"]}}
    assert _extract_chat_ids(rules) == ["valid", "another"]


def test_chat_ids_empty_when_no_operators_block():
    assert _extract_chat_ids({}) == []
    assert _extract_chat_ids({"other_key": True}) == []


def test_chat_ids_empty_when_operators_has_no_telegram_list():
    assert _extract_chat_ids({"operators": {}}) == []
    assert _extract_chat_ids({"operators": {"email": ["x@y.com"]}}) == []


def test_chat_ids_handles_none_business_rules():
    assert _extract_chat_ids(None) == []


# ---------------------------------------------------------------------------
# build_operator_message
# ---------------------------------------------------------------------------
_CONV_ID = uuid.UUID("c420db40-1234-1234-1234-123456789abc")


def test_message_includes_all_present_fields():
    text = build_operator_message(
        client_name="Café Arenillo",
        conversation_id=_CONV_ID,
        extracted_context={
            "full_name": "Juan Pérez",
            "phone": "3001234567",
            "shipping_city": "Manizales",
            "shipping_address": "Calle 10A #58-6 casa 6",
            "quantity": 4,
            "grind_preference": "1 molida y 3 grano",
        },
        customer_profile={},
    )
    assert "NUEVO PEDIDO PARA REVISAR" in text
    assert "Café Arenillo" in text
    assert "Juan Pérez" in text
    assert "3001234567" in text
    assert "Manizales" in text
    assert "Calle 10A #58-6 casa 6" in text
    assert "Cantidad: 4" in text
    assert "Molido: 1 molida y 3 grano" in text
    assert str(_CONV_ID) in text


def test_message_falls_back_to_profile_when_context_missing():
    """When extracted_context lacks fields (returning customer who just paid),
    use the persistent profile values."""
    text = build_operator_message(
        client_name="Café Arenillo",
        conversation_id=_CONV_ID,
        extracted_context={"quantity": 2},
        customer_profile={
            "full_name": "Sebastian Ramirez",
            "phone": "3107148477",
            "city": "Villamaría",
            "shipping_address": "Calle 10A #586",
        },
    )
    assert "Sebastian Ramirez" in text
    assert "3107148477" in text
    assert "Villamaría" in text
    assert "Calle 10A #586" in text


def test_message_handles_completely_empty_context_and_profile():
    """Should not crash, but show placeholders so operator knows what's missing."""
    text = build_operator_message(
        client_name="Café Arenillo",
        conversation_id=_CONV_ID,
        extracted_context={},
        customer_profile={},
    )
    assert "(sin nombre)" in text
    assert "(sin teléfono)" in text
    assert "(sin ciudad)" in text
    assert "(sin dirección)" in text
    assert "(sin cantidad)" in text


def test_message_omits_grind_line_when_empty():
    text = build_operator_message(
        client_name="X",
        conversation_id=_CONV_ID,
        extracted_context={"quantity": 1},
        customer_profile={},
    )
    assert "Molido:" not in text


def test_message_includes_conversation_id_as_html_code():
    text = build_operator_message(
        client_name="X",
        conversation_id=_CONV_ID,
        extracted_context={},
        customer_profile={},
    )
    assert f"<code>{_CONV_ID}</code>" in text


def test_message_includes_detection_timestamp():
    text = build_operator_message(
        client_name="X",
        conversation_id=_CONV_ID,
        extracted_context={},
        customer_profile={},
    )
    assert "Detectado:" in text
    assert "COT" in text


# ---------------------------------------------------------------------------
# _escape_html — protect against operator names with HTML chars
# ---------------------------------------------------------------------------
def test_html_escapes_angle_brackets():
    assert _escape_html("<script>") == "&lt;script&gt;"


def test_html_escapes_ampersand():
    assert _escape_html("Smith & Co") == "Smith &amp; Co"


def test_message_escapes_customer_input():
    """If a customer's full_name contains <, the message must escape it
    so Telegram doesn't reject parse_mode=HTML."""
    text = build_operator_message(
        client_name="X",
        conversation_id=_CONV_ID,
        extracted_context={"full_name": "Juan <hacker>"},
        customer_profile={},
    )
    assert "<hacker>" not in text
    assert "&lt;hacker&gt;" in text
