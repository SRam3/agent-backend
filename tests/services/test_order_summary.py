"""Tests for the order_summary service — pure-python only (ADR-010).

The centre of gravity is the historical regression suite: the five
conversations in which `user_confirmation` was ever set. Four were false
positives; one was legitimate. All five are here, and the legitimate one is as
obligatory as the other four — a gate that rejects everything also scores zero
false positives.

Also covered: the fingerprint (what counts as a change and what does not), city
normalisation (the customer types without accents), the no-total rule when
shipping is unknown, and the render's tone constraints.
"""
import sys
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.services.order_summary import (
    FIXED,
    INBOUND_PREDATES_SUMMARY,
    NO_SUMMARY,
    ORDER_MODIFIED_THIS_TURN,
    SUMMARY_REQUIRED,
    SUMMARY_STALE,
    TO_CONFIRM,
    Shipping,
    SummaryState,
    canonical_city,
    coerce_quantity,
    compute_fingerprint,
    compute_total,
    evaluate_user_confirmation,
    format_money,
    normalize_city,
    order_mutations,
    render_summary,
    resolve_shipping,
    should_render_summary,
    summary_missing_fields,
)

PRICE = Decimal("40000")
SHIPPING_RULES = {
    "cities": {
        "Manizales": {"cost": 5000},
        "Medellín": {"cost": 15000},
        "Envigado": {"cost": 15000},
        "Sabaneta": {"cost": 15000},
    },
    "pickup": False,
}
BUSINESS_RULES = {"shipping_rules": SHIPPING_RULES}
MANIZALES = Shipping(cost=Decimal("5000"), status=FIXED)
UNKNOWN = Shipping(cost=None, status=TO_CONFIRM)

T0 = datetime(2026, 8, 19, 15, 0, 0, tzinfo=timezone.utc)


def order(**overrides) -> dict:
    """A complete, summarisable order. Overrides tweak one field at a time."""
    base = {
        "product_id": "1f1f1f1f-0000-0000-0000-000000000001",
        "quantity": 2,
        "grind_preference": "grano",
        "full_name": "Juan Pérez",
        "phone": "3001234567",
        "shipping_city": "Manizales",
        "shipping_address": "Cra 28 A # 48-30",
    }
    base.update(overrides)
    return base


def state_for(context: dict, shipping: Shipping = MANIZALES, sent_at=T0) -> SummaryState:
    """The state the backend would have persisted after presenting `context`."""
    return SummaryState(fingerprint=compute_fingerprint(context, shipping), sent_at=sent_at)


# ---------------------------------------------------------------------------
# The five historical cases (ADR-010 §5)
# ---------------------------------------------------------------------------
def test_2026_05_01_bare_quantity_is_rejected():
    """`"2"` — a quantity, read as a confirmation. No summary had been sent,
    and the turn itself changes the order."""
    prior = order(quantity=None)
    accepted = {"quantity": 2, "user_confirmation": True}
    ok, reasons = evaluate_user_confirmation(
        prior_context=prior,
        merged_context={**prior, **accepted},
        accepted=accepted,
        state=SummaryState(fingerprint=None, sent_at=None),
        trigger_message_at=T0,
        shipping=MANIZALES,
    )
    assert ok is False
    assert NO_SUMMARY in reasons
    assert ORDER_MODIFIED_THIS_TURN in reasons


def test_2026_07_20_address_correction_is_rejected():
    """`"Unidad campestre sorry"` — a correction of the address, read as a
    confirmation. A summary existed, but this turn moves the order out from
    under it."""
    prior = order()
    accepted = {"shipping_address": "Unidad campestre", "user_confirmation": True}
    ok, reasons = evaluate_user_confirmation(
        prior_context=prior,
        merged_context={**prior, **accepted},
        accepted=accepted,
        state=state_for(prior),
        trigger_message_at=T0 + timedelta(seconds=30),
        shipping=MANIZALES,
    )
    assert ok is False
    assert ORDER_MODIFIED_THIS_TURN in reasons
    assert SUMMARY_STALE in reasons


def test_2026_08_01_inbound_arrived_before_the_summary():
    """`"enviame una foto del producto"` — it entered 795 ms BEFORE the summary
    was persisted. Nothing else is wrong with it: the order is untouched and the
    summary is current. Only condition 3 catches this one."""
    prior = order()
    accepted = {"user_confirmation": True}
    ok, reasons = evaluate_user_confirmation(
        prior_context=prior,
        merged_context={**prior, **accepted},
        accepted=accepted,
        state=state_for(prior),
        trigger_message_at=T0 - timedelta(milliseconds=795),
        shipping=MANIZALES,
    )
    assert ok is False
    assert reasons == (INBOUND_PREDATES_SUMMARY,)


def test_2026_08_19_no_backend_summary_ever_existed():
    """`"Barrio El Campin"` — a fragment of an address. The LLM asked
    "¿Todo bien con esos datos?" and granted itself the answer in the same JSON:
    the customer had not seen any summary, it was being sent right then."""
    prior = order(shipping_address=None)
    accepted = {"shipping_address": "Barrio El Campin", "user_confirmation": True}
    ok, reasons = evaluate_user_confirmation(
        prior_context=prior,
        merged_context={**prior, **accepted},
        accepted=accepted,
        state=SummaryState(fingerprint=None, sent_at=None),
        trigger_message_at=T0,
        shipping=MANIZALES,
    )
    assert ok is False
    assert NO_SUMMARY in reasons


def test_2026_07_15_the_legitimate_confirmation_passes():
    """`"Si gracias"` after the summary, with nothing changed. This test is as
    obligatory as the four above: a gate that rejects everything would also show
    zero false positives, and would have blocked the only real sale."""
    prior = order()
    accepted = {"user_confirmation": True}
    ok, reasons = evaluate_user_confirmation(
        prior_context=prior,
        merged_context={**prior, **accepted},
        accepted=accepted,
        state=state_for(prior),
        trigger_message_at=T0 + timedelta(seconds=12),
        shipping=MANIZALES,
    )
    assert ok is True
    assert reasons == ()


def test_confirmation_in_the_same_second_is_rejected():
    """Strict `>`: the summary's own instant does not count as "after". Two
    different clocks meet here (ours for sent_at, Meta's for the inbound), so
    this boundary is measured in production before it is relaxed."""
    prior = order()
    ok, reasons = evaluate_user_confirmation(
        prior_context=prior,
        merged_context={**prior, "user_confirmation": True},
        accepted={"user_confirmation": True},
        state=state_for(prior),
        trigger_message_at=T0,
        shipping=MANIZALES,
    )
    assert ok is False
    assert INBOUND_PREDATES_SUMMARY in reasons


def test_missing_trigger_timestamp_fails_closed():
    """No trigger timestamp (a conversation from before this change) refuses.
    A refusal costs a turn of friction; a wrong acceptance records a sale."""
    prior = order()
    ok, reasons = evaluate_user_confirmation(
        prior_context=prior,
        merged_context={**prior, "user_confirmation": True},
        accepted={"user_confirmation": True},
        state=state_for(prior),
        trigger_message_at=None,
        shipping=MANIZALES,
    )
    assert ok is False
    assert INBOUND_PREDATES_SUMMARY in reasons


def test_shipping_change_alone_makes_the_summary_stale():
    """The price the customer agreed to is part of what they agreed to: an edit
    to business_rules invalidates a standing summary even if the order is
    untouched."""
    prior = order()
    ok, reasons = evaluate_user_confirmation(
        prior_context=prior,
        merged_context={**prior, "user_confirmation": True},
        accepted={"user_confirmation": True},
        state=state_for(prior, shipping=Shipping(cost=Decimal("7000"), status=FIXED)),
        trigger_message_at=T0 + timedelta(seconds=30),
        shipping=MANIZALES,
    )
    assert ok is False
    assert SUMMARY_STALE in reasons


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------
def test_fingerprint_is_sensitive_to_every_summary_field():
    base = compute_fingerprint(order(), MANIZALES)
    changes = {
        "product_id": "1f1f1f1f-0000-0000-0000-000000000002",
        "quantity": 3,
        "grind_preference": "molido",
        "full_name": "Juana Pérez",
        "phone": "3009999999",
        "shipping_city": "Medellín",
        "shipping_address": "Cra 28 A # 48-31",
    }
    assert set(changes) == set(SUMMARY_REQUIRED)
    for field, value in changes.items():
        assert compute_fingerprint(order(**{field: value}), MANIZALES) != base, field


def test_fingerprint_covers_the_applied_shipping():
    assert compute_fingerprint(order(), MANIZALES) != compute_fingerprint(order(), UNKNOWN)


def test_fingerprint_ignores_cosmetic_variation():
    """The LLM re-proposes the whole cumulative extracted_data every turn. If a
    flip in capitalisation counted as a change, the backend would re-send the
    summary on the exact turn where tone matters most."""
    base = compute_fingerprint(order(), MANIZALES)
    assert compute_fingerprint(order(quantity="2"), MANIZALES) == base
    assert compute_fingerprint(order(full_name="  juan   pérez "), MANIZALES) == base
    assert compute_fingerprint(order(phone="300 123 45 67"), MANIZALES) == base
    assert compute_fingerprint(order(shipping_city="manizales"), MANIZALES) == base


def test_fingerprint_is_stable_across_calls():
    assert compute_fingerprint(order(), MANIZALES) == compute_fingerprint(order(), MANIZALES)


def test_order_mutations_lists_only_real_changes():
    prior = order()
    assert order_mutations(prior, {"quantity": "2", "full_name": "Juan Pérez"}) == []
    assert order_mutations(prior, {"quantity": 5}) == ["quantity"]
    assert order_mutations(prior, {"quantity": 5, "shipping_city": "Bogotá"}) == [
        "quantity",
        "shipping_city",
    ]


def test_order_mutations_ignores_non_summary_fields():
    """roast_preference is an order detail but does not appear in the summary,
    so it neither obsoletes it nor blocks a confirmation."""
    assert order_mutations(order(), {"roast_preference": "medio"}) == []


# ---------------------------------------------------------------------------
# City normalisation and shipping
# ---------------------------------------------------------------------------
def test_city_lookup_survives_missing_accents_and_case():
    for written in ("medellin", "Medellín", "MEDELLIN", "  medellín  ", "MeDeLLin"):
        assert resolve_shipping(written, SHIPPING_RULES) == Shipping(
            cost=Decimal("15000"), status=FIXED
        ), written


def test_manizales_is_five_thousand_fixed():
    assert resolve_shipping("manizales", SHIPPING_RULES).cost == Decimal("5000")


def test_unknown_city_is_to_confirm_not_a_guess():
    for city in ("Bogotá", "Pereira", "Cali", "", None):
        assert resolve_shipping(city, SHIPPING_RULES).status == TO_CONFIRM, city


def test_legacy_flat_shipping_rules_still_resolve():
    """Code deploys on merge; the migration is applied by hand afterwards. In
    that window the old (migration 005) shape must still resolve, or every city
    silently falls to "to be confirmed"."""
    legacy = {
        "Manizales": {"method": "domicilio", "cost": 7000},
        "zones": {"Eje Cafetero": {"cost_range": "7.000 - 10.000"}},
        "international": "no disponible actualmente",
    }
    assert resolve_shipping("manizales", legacy).cost == Decimal("7000")
    assert resolve_shipping("zones", legacy).status == TO_CONFIRM


def test_canonical_city_uses_the_business_spelling():
    assert canonical_city("medellin", SHIPPING_RULES) == "Medellín"
    assert canonical_city("Cartagena", SHIPPING_RULES) == "Cartagena"


def test_normalize_city_strips_accents_case_and_padding():
    assert normalize_city("  SANTA   MARTA ") == "santa marta"
    assert normalize_city("Bogotá") == normalize_city("bogota")


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
def test_total_is_coffee_plus_known_shipping():
    assert compute_total(2, PRICE, MANIZALES) == Decimal("85000")


def test_no_total_when_shipping_is_unknown():
    """A "total" that quietly leaves shipping out is a wrong price promised to
    a customer."""
    assert compute_total(2, PRICE, UNKNOWN) is None


def test_money_format_matches_the_adr():
    assert format_money(Decimal("85000")) == "$85.000"
    assert format_money(5000) == "$5.000"


def test_coerce_quantity_rejects_nonsense():
    assert coerce_quantity("2") == 2
    assert coerce_quantity(0) is None
    assert coerce_quantity(-1) is None
    assert coerce_quantity("dos") is None
    assert coerce_quantity(None) is None


# ---------------------------------------------------------------------------
# should_render_summary
# ---------------------------------------------------------------------------
EMPTY_STATE = SummaryState(fingerprint=None, sent_at=None)


def test_renders_once_all_seven_fields_are_present():
    assert should_render_summary(order(), EMPTY_STATE, PRICE, MANIZALES) is True


def test_never_renders_without_a_resolved_product():
    """H6, resolved structurally: no price without product_id, no summary
    without price, no confirmation without summary."""
    assert should_render_summary(order(product_id=None), EMPTY_STATE, PRICE, MANIZALES) is False
    assert should_render_summary(order(), EMPTY_STATE, None, MANIZALES) is False


def test_never_renders_without_the_grind():
    """Beans or ground is what actually gets shipped: an order that does not say
    which one cannot be fulfilled."""
    assert should_render_summary(order(grind_preference=None), EMPTY_STATE, PRICE, MANIZALES) is False


def test_never_renders_without_a_usable_quantity():
    assert should_render_summary(order(quantity=None), EMPTY_STATE, PRICE, MANIZALES) is False
    assert should_render_summary(order(quantity="dos"), EMPTY_STATE, PRICE, MANIZALES) is False


def test_does_not_repeat_the_same_summary():
    """Idempotent by construction — which is what lets the circuit breaker stay
    where it is: a backend summary cannot loop with itself."""
    context = order()
    assert should_render_summary(context, state_for(context), PRICE, MANIZALES) is False


def test_renders_again_once_the_order_changes():
    presented = order()
    corrected = order(quantity=3)
    assert should_render_summary(corrected, state_for(presented), PRICE, MANIZALES) is True


def test_does_not_render_once_the_customer_confirmed():
    context = order(user_confirmation=True)
    assert should_render_summary(context, EMPTY_STATE, PRICE, MANIZALES) is False


def test_summary_missing_fields_reports_what_is_missing():
    assert summary_missing_fields(order()) == []
    assert summary_missing_fields(order(grind_preference=None)) == ["grind_preference"]
    assert summary_missing_fields({}) == list(SUMMARY_REQUIRED)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def test_summary_reads_like_the_adr_example():
    text = render_summary(order(), PRICE, MANIZALES, "es", BUSINESS_RULES)
    assert text == (
        "Va el pedido entonces: 2 bolsas de 340g en grano para Juan Pérez, "
        "al 3001234567, en Manizales, Cra 28 A # 48-30. "
        "El café son $80.000 y el envío $5.000, total $85.000. "
        "¿Todo bien con esos datos?"
    )


def test_summary_never_uses_an_em_dash():
    """That character is vanishingly rare in casual WhatsApp writing and gives
    the bot away, precisely where sounding human matters most."""
    for language in ("es", "en"):
        for shipping in (MANIZALES, UNKNOWN):
            text = render_summary(order(), PRICE, shipping, language, BUSINESS_RULES)
            assert "—" not in text


def test_summary_is_not_a_form():
    """Label style ("Nombre:", "Teléfono:") is what gave the old prompt away."""
    text = render_summary(order(), PRICE, MANIZALES, "es", BUSINESS_RULES)
    assert "Nombre:" not in text and "Teléfono:" not in text and "\n" not in text


def test_summary_in_english_uses_the_live_language():
    text = render_summary(order(), PRICE, MANIZALES, "en", BUSINESS_RULES)
    assert "Here's the order then:" in text
    assert "total $85.000" in text
    assert "Does that all look right?" in text


def test_summary_states_shipping_is_pending_instead_of_a_total():
    text = render_summary(order(shipping_city="Bogotá"), PRICE, UNKNOWN, "es", BUSINESS_RULES)
    assert "total" not in text
    assert "El envío a Bogotá lo confirmamos contigo" in text
    assert "El café son $80.000." in text


def test_summary_uses_the_canonical_city_spelling():
    text = render_summary(order(shipping_city="medellin"), PRICE, MANIZALES, "es", BUSINESS_RULES)
    assert "en Medellín," in text


def test_summary_singularises_one_bag():
    text = render_summary(order(quantity=1), PRICE, MANIZALES, "es", BUSINESS_RULES)
    assert "1 bolsa de 340g" in text
    assert "bolsas" not in text


def test_summary_repeats_an_unusual_grind_verbatim():
    """Customers say "2 en grano y 2 molidos". The backend does not parse that,
    it repeats it, which is what a person paraphrasing an order would do."""
    text = render_summary(
        order(quantity=4, grind_preference="2 en grano y 2 molidos"),
        PRICE, MANIZALES, "es", BUSINESS_RULES,
    )
    assert "2 en grano y 2 molidos" in text


def test_grind_agreement_does_not_break_the_sentence():
    """A prepositional grind attaches straight to the unit; anything else is set
    off by commas, or it disagrees in gender and number ("2 bolsas ... molido")."""
    def line(**kw):
        return render_summary(order(**kw), PRICE, MANIZALES, "es", BUSINESS_RULES).split(" para ")[0]

    assert line() == "Va el pedido entonces: 2 bolsas de 340g en grano"
    assert line(grind_preference="molido") == "Va el pedido entonces: 2 bolsas de 340g, molido,"
    assert line(quantity=1, grind_preference="molido") == "Va el pedido entonces: 1 bolsa de 340g, molido,"
