"""Tests for prompt context formatting.

Pure Python — no database, no network, no LLM calls.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.services.prompt_context import (
    format_business_context,
    format_conversation_summary,
    format_customer_profile,
    _format_price,
)


# ---------------------------------------------------------------------------
# format_business_context
# ---------------------------------------------------------------------------

CAFE_ARENILLO_RULES = {
    "currency": "COP",
    "shipping_rules": {
        "Manizales": {"method": "domicilio", "cost": 7000},
        "Medellín": {"method": "Uber envíos", "cost_note": "variable según distancia"},
        "Bogotá": {"method": "transportadora", "cost_note": "desde 20.000 si supera 2kg"},
        "other": {"method": "transportadora", "cost_note": "se confirma según transportadora"},
        "international": "no disponible actualmente",
    },
    "payment_methods": [
        {"type": "bank_transfer", "bank": "Bancolombia", "account_type": "ahorros", "account": "05965752562"},
        {"type": "nequi", "number": "3107148477"},
    ],
    "discount_rules": {
        "no_discount_message": "No ofrecemos descuento por unidad",
        "bulk_threshold": 10,
        "bulk_message": "Podemos revisar un precio especial por volumen",
    },
}

CAFE_PRODUCT = [
    {
        "id": "some-uuid",
        "name": "Café Arenillo",
        "description": "Variedad Castillo, proceso honey, 340g.",
        "sku": "CAFE-001",
        "price": 40000,
        "ai_description": "Café especial de Manizales. Variedad Castillo, proceso honey, 340g.",
    }
]


def test_business_context_includes_product():
    result = format_business_context(CAFE_ARENILLO_RULES, CAFE_PRODUCT)
    assert "PRODUCT CATALOG" in result
    assert "Café Arenillo" in result
    assert "CAFE-001" in result
    assert "$40.000 COP" in result
    assert "ONLY sell products listed above" in result


def test_business_context_includes_shipping():
    result = format_business_context(CAFE_ARENILLO_RULES, CAFE_PRODUCT)
    assert "SHIPPING RULES" in result
    assert "Manizales" in result
    assert "$7.000 COP" in result
    assert "Medellín" in result
    assert "International" in result


def test_business_context_includes_payment():
    result = format_business_context(CAFE_ARENILLO_RULES, CAFE_PRODUCT)
    assert "PAYMENT METHODS" in result
    assert "Bancolombia" in result
    assert "05965752562" in result
    assert "Nequi" in result
    assert "3107148477" in result
    assert "ONLY when" in result


def test_business_context_includes_discounts():
    result = format_business_context(CAFE_ARENILLO_RULES, CAFE_PRODUCT)
    assert "DISCOUNT RULES" in result
    assert "No ofrecemos descuento" in result
    assert "10+" in result
    assert "Never invent discount" in result


def test_business_context_empty_rules():
    """Graceful fallback with empty data."""
    result = format_business_context({}, [])
    assert result == ""


def test_business_context_partial_rules():
    """Works with only some sections present."""
    result = format_business_context({"currency": "COP"}, CAFE_PRODUCT)
    assert "PRODUCT CATALOG" in result
    assert "SHIPPING" not in result
    assert "PAYMENT" not in result


# ---------------------------------------------------------------------------
# format_conversation_summary
# ---------------------------------------------------------------------------

def test_summary_new_customer():
    result = format_conversation_summary({}, {})
    assert "=== CLIENTE ===" in result
    assert "Cliente nuevo" in result
    assert "=== ESTADO DEL PEDIDO ===" in result
    assert "Aún no se ha recopilado" in result


def test_summary_with_display_name_only():
    result = format_conversation_summary({"display_name": "Juan"}, {})
    assert "Juan" in result
    assert "Cliente nuevo" in result


def test_summary_with_extracted_context_marks_collected():
    result = format_conversation_summary(
        {},
        {"product_id": "abc-uuid", "full_name": "Juan Pérez", "shipping_city": "Manizales"},
    )
    assert "✓ Producto: abc-uuid" in result
    assert "✓ Nombre completo: Juan Pérez" in result
    assert "✓ Ciudad: Manizales" in result
    # the ones not collected yet appear as missing
    assert "✗ teléfono" in result
    assert "✗ dirección" in result


def test_summary_returning_customer_profile():
    result = format_conversation_summary(
        {"profile": {"full_name": "Juan Pérez", "shipping_address": "Calle 10 #5-20", "city": "Manizales"}},
        {},
    )
    assert "Cliente que ya conocemos" in result
    assert "Nombre completo: Juan Pérez" in result
    assert "Dirección: Calle 10 #5-20" in result
    assert "Ciudad: Manizales" in result
    assert "Dirígete a Juan por su nombre" in result


def test_summary_combines_profile_and_context():
    result = format_conversation_summary(
        {"display_name": "Juan", "profile": {"full_name": "Juan Pérez", "purchase_count": 2}},
        {"product_id": "abc", "phone": "3001234567"},
    )
    assert "Juan" in result
    assert "Compras previas: 2" in result
    assert "✓ Teléfono: 3001234567" in result
    assert "✓ Producto: abc" in result


def test_profile_block_new_customer():
    result = format_customer_profile(None, {})
    assert "Cliente nuevo" in result
    assert "Preséntate brevemente" in result


def test_profile_block_returning_customer_with_preferences():
    result = format_customer_profile(
        "Juan",
        {
            "first_name": "Juan",
            "preferences": {"grind": "granos enteros", "roast": "medio"},
            "purchase_count": 3,
        },
    )
    assert "Juan" in result
    assert "Prefiere molido: granos enteros" in result
    assert "Prefiere tueste: medio" in result
    assert "Compras previas: 3" in result


def test_summary_all_complete():
    """When every order field is collected, no missing block."""
    result = format_conversation_summary(
        {},
        {
            "product_id": "abc",
            "full_name": "Juan Pérez",
            "phone": "3001234567",
            "shipping_city": "Manizales",
            "shipping_address": "Calle 10",
            "user_confirmation": True,
            "payment_confirmation": True,
        },
    )
    assert "Todos los datos del pedido están completos" in result
    assert "✗" not in result


# ---------------------------------------------------------------------------
# _format_price
# ---------------------------------------------------------------------------

def test_format_price_cop():
    assert _format_price(40000, "COP") == "$40.000 COP"
    assert _format_price(7000, "COP") == "$7.000 COP"
    assert _format_price(0, "COP") == "$0 COP"


def test_format_price_other_currency():
    assert _format_price(99.99, "USD") == "99.99 USD"
