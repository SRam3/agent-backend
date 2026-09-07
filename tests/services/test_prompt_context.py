"""Tests for prompt context formatting.

Pure Python — no database, no network, no LLM calls.
"""
import sys
import os

import pytest

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


ARENILLO_RULES_POST_013 = {
    "currency": "COP",
    "shipping_rules": {
        "cities": {
            "Manizales": {"cost": 5000},
            "Medellín": {"cost": 15000},
            "Envigado": {"cost": 15000},
            "Sabaneta": {"cost": 15000},
        },
        "default": "to_confirm",
        "pickup": False,
        "international": "no disponible actualmente",
    },
}


def test_business_context_includes_shipping():
    """Legacy (migration 005) shape still resolves: the code ships on merge and
    013 is applied by hand afterwards."""
    result = format_business_context(CAFE_ARENILLO_RULES, CAFE_PRODUCT)
    assert "SHIPPING RULES" in result
    assert "Manizales" in result
    assert "$7.000 COP" in result
    assert "International" in result


def test_only_cities_with_a_real_rate_are_quoted():
    """ADR-010 §3. A city with a hedge instead of a figure ("variable según
    distancia") is NOT listed: it falls under the confirm-afterwards rule. The
    old per-zone ranges were numbers nobody ever checked against a shipment."""
    result = format_business_context(CAFE_ARENILLO_RULES, CAFE_PRODUCT)
    assert "Medellín" not in result
    assert "Any other city" in result


def test_fixed_rates_are_stated_without_hedging():
    """No "aprox." on a rate we actually know: hedging it reads as guessing."""
    result = format_business_context(ARENILLO_RULES_POST_013, CAFE_PRODUCT)
    assert "- Manizales: $5.000 COP" in result
    assert "- Medellín: $15.000 COP" in result
    assert "aprox" not in result.lower()


def test_shipping_block_forbids_inventing_a_rate_and_doing_totals():
    """The two things ADR-010 takes away from the model: quoting a shipping cost
    it does not have, and computing money."""
    result = format_business_context(ARENILLO_RULES_POST_013, CAFE_PRODUCT)
    assert "NEVER quote, estimate or invent a figure" in result
    assert "never state a total or do the arithmetic yourself" in result


def test_no_pickup_at_the_farm_is_stated():
    """A new rule: today the LLM improvises when asked."""
    result = format_business_context(ARENILLO_RULES_POST_013, CAFE_PRODUCT)
    assert "no pickup at the farm" in result


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
    assert "El cliente se llama Juan" in result


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
    assert "preséntate brevemente" in result


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
            "quantity": 4,
            "grind_preference": "2 molidos y 2 en grano",
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


def test_summary_shows_quantity_and_grind_when_collected():
    result = format_conversation_summary(
        {},
        {"product_id": "abc", "quantity": 4, "grind_preference": "grano"},
    )
    assert "✓ Cantidad: 4" in result
    assert "✓ Preferencia de molido: grano" in result


def test_summary_missing_quantity_and_grind():
    result = format_conversation_summary({}, {"product_id": "abc"})
    assert "✗ cantidad (número de bolsas)" in result
    assert "✗ preferencia de molido (grano/molido)" in result


def test_summary_shows_roast_when_collected():
    result = format_conversation_summary({}, {"roast_preference": "medio"})
    assert "✓ Preferencia de tueste: medio" in result


def test_summary_never_requests_roast_when_absent():
    """Café de tueste único: roast shows ✓ if volunteered, but is NEVER asked
    for proactively, so it must not appear in the 'Aún falta recopilar' list."""
    result = format_conversation_summary({}, {"product_id": "abc"})
    missing_lines = [ln for ln in result.splitlines() if ln.strip().startswith("✗")]
    assert not any("tueste" in ln.lower() for ln in missing_lines)
    # And no roast label at all when not collected
    assert "Preferencia de tueste" not in result


# ---------------------------------------------------------------------------
# _format_price
# ---------------------------------------------------------------------------

def test_format_price_cop():
    assert _format_price(40000, "COP") == "$40.000 COP"
    assert _format_price(7000, "COP") == "$7.000 COP"
    assert _format_price(0, "COP") == "$0 COP"


def test_format_price_other_currency():
    assert _format_price(99.99, "USD") == "99.99 USD"


# ---------------------------------------------------------------------------
# Memory block: last_conversation_summary, language, communication_style
# ---------------------------------------------------------------------------

def test_profile_block_renders_last_conversation_summary():
    result = format_customer_profile(
        "Juan",
        {
            "first_name": "Juan",
            "last_conversation_summary": {
                "summary": "Pidió 3 bolsas de Café Arenillo. No envió comprobante.",
                "outcome": "abandoned_at_payment",
                "interest_level": "high",
                "objections": ["precio_envío"],
            },
        },
    )
    assert "MEMORIA DE LA ÚLTIMA CONVERSACIÓN" in result
    assert "Pidió 3 bolsas" in result
    assert "abandoned_at_payment" in result
    assert "Nivel de interés: high" in result
    assert "precio_envío" in result


def test_profile_block_renders_pending_intent_when_present():
    result = format_customer_profile(
        "Juan",
        {
            "first_name": "Juan",
            "last_conversation_summary": {
                "summary": "Quería 3 bolsas pero no confirmó.",
                "outcome": "abandoned_at_confirmation",
                "interest_level": "medium",
                "pending_intent": {
                    "product_id": "uuid-cafe-001",
                    "quantity": 3,
                    "notes": None,
                },
            },
        },
    )
    assert "Quedó a punto de comprar" in result
    assert "cantidad=3" in result
    assert "producto=uuid-cafe-001" in result


def test_profile_block_skips_pending_intent_when_null():
    result = format_customer_profile(
        "Juan",
        {
            "first_name": "Juan",
            "last_conversation_summary": {
                "summary": "Solo preguntó por el menú.",
                "outcome": "no_intent",
                "interest_level": "low",
                "pending_intent": None,
            },
        },
    )
    assert "Quedó a punto de comprar" not in result


def test_profile_block_renders_language_english():
    result = format_customer_profile(
        "John",
        {"first_name": "John", "language": "en"},
    )
    assert "INSTRUCCIÓN DE IDIOMA" in result
    assert "INGLÉS" in result


def test_profile_block_renders_language_spanish():
    result = format_customer_profile(
        "Juan",
        {"first_name": "Juan", "language": "es"},
    )
    assert "INSTRUCCIÓN DE IDIOMA" in result
    assert "español" in result


# ---------------------------------------------------------------------------
# live_language (ADR-008): idioma detectado en vivo, prioridad sobre profile
# ---------------------------------------------------------------------------

def test_live_language_english_with_empty_profile():
    """El caso del stand: cliente NUEVO (profile vacío) escribiendo en inglés.
    Hoy no se emite instrucción alguna; con live_language debe emitirse."""
    result = format_customer_profile(None, {}, live_language="en")
    assert "INSTRUCCIÓN DE IDIOMA" in result
    assert "INGLÉS" in result


def test_live_language_english_with_display_name_only():
    result = format_customer_profile("John", {}, live_language="en")
    assert "Cliente nuevo" in result
    assert "INSTRUCCIÓN DE IDIOMA" in result
    assert "INGLÉS" in result


def test_live_language_beats_profile_language():
    """live_language (este turno) tiene prioridad sobre profile.language (diferido)."""
    result = format_customer_profile(
        "John",
        {"first_name": "John", "language": "es"},
        live_language="en",
    )
    assert "INGLÉS" in result
    assert "escribe en español" not in result


def test_live_language_spanish_beats_profile_english():
    """Caso inverso: el perfil dice inglés pero el cliente vuelve al español."""
    result = format_customer_profile(
        "Juan",
        {"first_name": "Juan", "language": "en"},
        live_language="es",
    )
    assert "escribe en español" in result
    assert "INGLÉS" not in result


def test_no_live_language_is_byte_identical_to_current_behavior():
    """Snapshot literal del bloque de cliente. Actualizado el 2026-09-06 al
    volver la instrucción consciente del turno: el texto cambió a propósito, y
    lo que este test protege sigue siendo lo mismo — que sin `live_language` no
    se cuela ninguna línea de idioma (ADR-008)."""
    assert format_customer_profile(None, {}) == (
        "=== CLIENTE ===\n"
        "Cliente nuevo. No tenemos datos previos.\n"
        "INSTRUCCIÓN: Este es tu primer mensaje de la conversación: "
        "preséntate brevemente y pregunta en qué le puedes ayudar."
    )
    assert format_customer_profile("Juan", {}) == (
        "=== CLIENTE ===\n"
        "Cliente nuevo. En WhatsApp aparece como: Juan\n"
        "INSTRUCCIÓN: Este es tu primer mensaje de la conversación: "
        "preséntate brevemente y pregunta en qué le puedes ayudar."
    )
    assert format_customer_profile("John", {"first_name": "John", "language": "en"}) == (
        "=== CLIENTE ===\n"
        "Cliente que ya conocemos. Datos en archivo:\n"
        "  • Nombre: John\n"
        "\n"
        "INSTRUCCIÓN DE IDIOMA: el cliente escribe en INGLÉS. Respóndele en inglés.\n"
        "\n"
        "INSTRUCCIÓN: El cliente se llama John. Es un cliente que ya conocemos: "
        "no te presentes como si fuera la primera vez ni preguntes datos que ya "
        "tenemos arriba. Este es tu primer mensaje de la conversación, así que "
        "salúdalo con cercanía."
    )


def test_summary_passes_live_language_through():
    result = format_conversation_summary({}, {}, live_language="en")
    assert "INSTRUCCIÓN DE IDIOMA" in result
    assert "INGLÉS" in result


def test_format_language_directive():
    from app.services.prompt_context import format_language_directive

    assert format_language_directive("en") == "LANGUAGE (overrides all else): reply in English."
    assert format_language_directive("es") == "LANGUAGE (overrides all else): reply in Spanish."


def test_profile_block_renders_communication_style():
    casual = format_customer_profile("Juan", {"first_name": "Juan", "communication_style": "casual"})
    assert "tono cálido e informal" in casual

    formal = format_customer_profile("Juan", {"first_name": "Juan", "communication_style": "formal"})
    assert "tono formal" in formal

    direct = format_customer_profile("Juan", {"first_name": "Juan", "communication_style": "direct"})
    assert "breve y directo" in direct


def test_profile_block_no_memory_block_when_no_summary():
    """Returning customer without a previous summary doesn't render the block."""
    result = format_customer_profile(
        "Juan",
        {"first_name": "Juan", "purchase_count": 1},
    )
    assert "MEMORIA DE LA ÚLTIMA CONVERSACIÓN" not in result
    assert "Quedó a punto de comprar" not in result


# ===========================================================================
# El saludo es del primer turno, no de todos (2026-09-06)
# ===========================================================================
# El bloque de cliente se reinyecta en CADA turno. Mientras la instrucción de
# saludar se repetía turno a turno, competía de frente con el
# `system_prompt_template`, que manda saludar UNA SOLA vez y no repetir el
# nombre en cada mensaje. Cuando dos instrucciones se contradicen el modelo
# elige: el 2026-09-06 saludó dos veces ("Hola, Sebastián" en el turno 1 y otra
# vez en el 2) y nombró al cliente en seis de diez mensajes.
_RECURRENTE = {"first_name": "Sebastián", "full_name": "Sebastián Ramirez", "purchase_count": 3}


def test_el_primer_turno_si_manda_saludar():
    result = format_customer_profile("Sebastián", _RECURRENTE, is_first_turn=True)

    assert "salúdalo con cercanía" in result.lower()
    assert "primer mensaje de la conversación" in result


def test_a_partir_del_segundo_turno_la_orden_de_saludar_desaparece():
    result = format_customer_profile("Sebastián", _RECURRENTE, is_first_turn=False)

    assert "salúdalo con cercanía" not in result.lower()
    assert "primer mensaje de la conversación" not in result


def test_a_partir_del_segundo_turno_la_prohibicion_es_explicita():
    """Quitar la orden positiva no basta: la ausencia de instrucción pesa menos
    que una negativa. El prompt de persona YA decía "saluda UNA SOLA vez" y el
    modelo lo ignoró mientras hubo una orden positiva compitiendo."""
    result = format_customer_profile("Sebastián", _RECURRENTE, is_first_turn=False)

    assert "YA SALUDASTE" in result
    assert "NO vuelvas a saludar" in result
    assert "NO repitas su nombre en cada mensaje" in result


def test_el_nombre_es_un_hecho_no_una_orden_permanente():
    """"Dirígete a X por su nombre" en cada turno es lo que hizo que el bot
    nombrara al cliente en seis de diez mensajes, contradiciendo al prompt de
    persona. El nombre se declara; usarlo o no lo decide la persona."""
    for first_turn in (True, False):
        result = format_customer_profile("Sebastián", _RECURRENTE, is_first_turn=first_turn)
        assert "El cliente se llama Sebastián" in result
        assert "Dirígete a" not in result


@pytest.mark.parametrize(
    "display_name,profile,caso",
    [
        (None, {}, "nuevo anónimo"),
        ("Juan", {}, "nuevo con nombre de WhatsApp"),
        ("Sebastián", _RECURRENTE, "recurrente"),
    ],
)
def test_los_tres_casos_de_cliente_respetan_el_turno(display_name, profile, caso):
    """El defecto era idéntico en los tres caminos del bloque, no solo en el
    del cliente recurrente. Se arreglan los tres o queda medio arreglado."""
    primero = format_customer_profile(display_name, profile, is_first_turn=True)
    despues = format_customer_profile(display_name, profile, is_first_turn=False)

    assert primero != despues, f"el caso «{caso}» ignora el turno"
    assert "YA SALUDASTE" in despues
    assert "YA SALUDASTE" not in primero


def test_el_default_saluda_para_no_romper_a_quien_no_pase_el_turno():
    """Aditivo a propósito: omitir el argumento se comporta como antes."""
    assert format_customer_profile("Sebastián", _RECURRENTE) == format_customer_profile(
        "Sebastián", _RECURRENTE, is_first_turn=True
    )


def test_format_conversation_summary_propaga_el_turno():
    """El ingest habla con el resumen, no con el bloque de perfil directamente."""
    despues = format_conversation_summary(
        {"display_name": "Sebastián", "profile": _RECURRENTE}, {}, is_first_turn=False
    )

    assert "YA SALUDASTE" in despues
    assert "salúdalo con cercanía" not in despues.lower()
