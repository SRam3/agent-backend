"""Order summary rendering, pricing and confirmation gating (ADR-010).

The backend owns the operational data that reaches the customer. The LLM keeps
the conversation; it loses the arithmetic and the declaration of business facts.

Why this module exists at all: `user_confirmation` declares an ACT ("the
customer accepted their order"), but until now it was decided by a judgement of
LANGUAGE over a single message, with nothing to contrast it against. There was
no deterministic signal that the summary had ever been sent — it lived as free
text inside a `response_text` the LLM wrote, so the backend did not know it had
sent it. Measured over the full history: 4 of 5 `user_confirmation` were false
positives (80%), and that checkpoint is the only precondition for the operator
button to record a sale (confirm_payment.py).

So the fix is not "judge the message better": it is to CREATE the fact to judge
it against. The backend renders and sends the summary itself, fingerprints the
order as presented, and only then accepts a confirmation — and only while that
fingerprint still holds.

Pure Python — no I/O, no DB, no LLM. Everything this module decides is unit
testable, which is the point: the whole gate is a pure function of (what the
customer's order looks like now, what we last presented, when).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

# Fields that must ALL be present before the backend will render a summary.
# `quantity` and `grind_preference` are here even though they are not DAG
# checkpoints (they are ORDER_FIELDS): the first because there is no arithmetic
# without it, the second because grind is what actually gets shipped — an order
# that does not say beans-or-ground cannot be fulfilled. Making them required
# is what forces the directive to ask for them (goal_strategy.py).
SUMMARY_REQUIRED: tuple[str, ...] = (
    "product_id",
    "quantity",
    "grind_preference",
    "full_name",
    "phone",
    "shipping_city",
    "shipping_address",
)

#: The order as PRESENTED. Any change here obsoletes the summary that was sent.
#: The applied shipping is folded in separately (see :func:`compute_fingerprint`)
#: so that an edit to business_rules also invalidates a standing summary.
FINGERPRINT_FIELDS: tuple[str, ...] = SUMMARY_REQUIRED

# Shipping resolution outcomes.
FIXED = "fixed"
TO_CONFIRM = "to_confirm"

# Why a proposed user_confirmation was refused. One constant per condition of
# ADR-010 §5 — kept distinguishable on purpose: in production these are the only
# instrument for knowing which condition is doing the work, and whether any of
# them produces false NEGATIVES.
NO_SUMMARY = "no_summary"
SUMMARY_STALE = "summary_stale"
INBOUND_PREDATES_SUMMARY = "inbound_predates_summary"
ORDER_MODIFIED_THIS_TURN = "order_modified_this_turn"

_WHITESPACE_RE = re.compile(r"\s+")
_NON_DIGITS_RE = re.compile(r"\D")


@dataclass(frozen=True)
class Shipping:
    """The shipping cost actually applied to this order.

    ``cost`` is None exactly when ``status`` is TO_CONFIRM. A summary with
    TO_CONFIRM shipping never states a total: promising a total that silently
    excludes shipping is the same invented-number failure this ADR removes.
    """

    cost: Optional[Decimal]
    status: str


@dataclass(frozen=True)
class SummaryState:
    """What the backend last presented, as persisted on the conversation.

    Read ONCE at the start of a turn. Evaluating the gate against a summary the
    same turn is writing would let condition 1 grant itself — the exact vice of
    2026-08-19 (the LLM asked and answered itself in one JSON), only moved to
    the backend.
    """

    fingerprint: Optional[str]
    sent_at: Optional[datetime]


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def normalize_city(raw) -> str:
    """Fold a city name to a comparable key: no accents, no case, no padding.

    Until ADR-010 the city was "resolved" by an LLM reading text. Now it is a
    dict lookup, and "medellin", "Medellín" and "MEDELLÍN" are three different
    keys. Customers routinely skip accents on WhatsApp, so without this the
    customer from Medellín silently falls into "shipping to be confirmed".
    """
    if not raw:
        return ""
    decomposed = unicodedata.normalize("NFD", str(raw))
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return _WHITESPACE_RE.sub(" ", stripped).strip().casefold()


def coerce_quantity(value) -> Optional[int]:
    """Best-effort positive int. The LLM sends 2 and "2" interchangeably."""
    if value is None:
        return None
    try:
        quantity = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return quantity if quantity > 0 else None


def _normalize_field(field: str, value) -> str:
    """Canonical form of one order field, for fingerprint and change detection.

    Cosmetic variation must NOT count as a change. The LLM re-proposes the whole
    cumulative extracted_data every turn, so a flip in capitalisation would
    otherwise obsolete the summary and re-send it — summary spam, on the exact
    turn where tone matters most.
    """
    if value is None:
        return ""
    if field == "quantity":
        quantity = coerce_quantity(value)
        return "" if quantity is None else str(quantity)
    if field == "shipping_city":
        return normalize_city(value)
    if field == "phone":
        return _NON_DIGITS_RE.sub("", str(value))
    if field == "product_id":
        return str(value).strip().casefold()
    return _WHITESPACE_RE.sub(" ", str(value)).strip().casefold()


# ---------------------------------------------------------------------------
# Shipping and money
# ---------------------------------------------------------------------------
def _shipping_cities(shipping_rules: Optional[dict]) -> dict:
    """The city → rule mapping, tolerating both shapes.

    Migration 013 writes the explicit shape ``{"cities": {...}}``. The legacy
    shape (migration 005) is flat, with "zones"/"international" as reserved
    keys. Both are read because the code deploys on merge to main while the
    migration is applied by hand afterwards: for that window the old rules must
    still resolve, or every city would silently fall to "to be confirmed".
    """
    rules = shipping_rules or {}
    cities = rules.get("cities")
    if isinstance(cities, dict):
        return cities
    return {
        key: value
        for key, value in rules.items()
        if isinstance(value, dict) and key not in ("zones", "international", "cities")
    }


def resolve_shipping(city, shipping_rules: Optional[dict]) -> Shipping:
    """Shipping cost for a city. Unknown city ⇒ TO_CONFIRM, never a guess.

    ADR-010 §3: only cities with a real, fixed rate carry a number. Everything
    else is coordinated by hand and the summary says so. Quoting a rate nobody
    ever verified against an actual shipment is inventing tariffs.
    """
    target = normalize_city(city)
    if not target:
        return Shipping(cost=None, status=TO_CONFIRM)
    for name, rule in _shipping_cities(shipping_rules).items():
        if normalize_city(name) != target:
            continue
        cost = rule.get("cost") if isinstance(rule, dict) else None
        if cost is None:
            return Shipping(cost=None, status=TO_CONFIRM)
        return Shipping(cost=Decimal(str(cost)), status=FIXED)
    return Shipping(cost=None, status=TO_CONFIRM)


def canonical_city(city, shipping_rules: Optional[dict]) -> str:
    """The city's spelling as the business writes it, when we know it.

    The customer types "medellin"; echoing that back in the summary reads
    careless. When the city matches a known rule we use the business's own
    spelling (accents included); otherwise we repeat what the customer wrote
    rather than guessing at capitalisation.
    """
    target = normalize_city(city)
    if not target:
        return ""
    for name in _shipping_cities(shipping_rules):
        if normalize_city(name) == target:
            return str(name)
    return str(city).strip()


def compute_coffee_subtotal(quantity, unit_price) -> Optional[Decimal]:
    """``quantity × unit_price``. None when either is unusable."""
    units = coerce_quantity(quantity)
    if units is None or unit_price is None:
        return None
    return Decimal(str(unit_price)) * units


def compute_total(quantity, unit_price, shipping: Shipping) -> Optional[Decimal]:
    """Coffee + shipping. None when the shipping cost is not known yet.

    Returning None rather than the bare subtotal is deliberate: a "total" that
    quietly leaves shipping out is a wrong price promised to a customer.
    """
    subtotal = compute_coffee_subtotal(quantity, unit_price)
    if subtotal is None:
        return None
    if shipping.status != FIXED or shipping.cost is None:
        return None
    return subtotal + shipping.cost


def format_money(amount) -> str:
    """``$80.000`` — Colombian thousands separator, no currency suffix.

    Distinct from prompt_context._format_price on purpose: that one appends
    "COP" because it goes into the prompt. This text goes to the customer.
    """
    return "${:,}".format(int(Decimal(str(amount)))).replace(",", ".")


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------
def compute_fingerprint(context: dict, shipping: Shipping) -> str:
    """Stable hash of the order exactly as it would be presented.

    Covers the seven summary fields plus the applied shipping, so an edit to
    business_rules.shipping_rules also obsoletes a standing summary — the price
    the customer agreed to is part of what they agreed to.
    """
    context = context or {}
    payload = {field: _normalize_field(field, context.get(field)) for field in FINGERPRINT_FIELDS}
    payload["_shipping"] = "{}:{}".format(
        shipping.status,
        "" if shipping.cost is None else str(int(shipping.cost)),
    )
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def order_mutations(prior_context: dict, accepted: dict) -> list[str]:
    """Summary fields this turn actually CHANGES (sorted, canonical comparison).

    Only fields the turn proposes are considered, and only when their canonical
    value differs from what was already there — a re-proposal of the same value
    is not a modification.
    """
    prior_context = prior_context or {}
    accepted = accepted or {}
    changed = [
        field
        for field in FINGERPRINT_FIELDS
        if field in accepted
        and _normalize_field(field, accepted[field])
        != _normalize_field(field, prior_context.get(field))
    ]
    return sorted(changed)


# ---------------------------------------------------------------------------
# The confirmation gate (ADR-010 §5)
# ---------------------------------------------------------------------------
def evaluate_user_confirmation(
    prior_context: dict,
    merged_context: dict,
    accepted: dict,
    state: SummaryState,
    trigger_message_at: Optional[datetime],
    shipping: Shipping,
) -> tuple[bool, tuple[str, ...]]:
    """Decide whether a user_confirmation proposed by the LLM may be accepted.

    The LLM still judges the language (deciding whether an utterance is an
    affirmation IS a language task). The backend judges the context: all four
    conditions must hold.

    Returns ``(accepted, reasons)``; ``reasons`` is empty on acceptance and
    otherwise lists EVERY condition that failed, not just the first. Several
    historical false positives fail more than one, and knowing which is what
    makes the gate measurable in production.
    """
    reasons: list[str] = []

    # 1. A summary was actually presented in this conversation.
    if not state.fingerprint or state.sent_at is None:
        reasons.append(NO_SUMMARY)
    else:
        # 2. It is still current: nothing about the order has changed since.
        if compute_fingerprint(merged_context, shipping) != state.fingerprint:
            reasons.append(SUMMARY_STALE)
        # 3. The message that triggered this turn arrived AFTER we sent it.
        #    A missing trigger timestamp fails closed — the cost of a refusal is
        #    a turn of friction, the cost of a wrong acceptance is a recorded sale.
        if trigger_message_at is None or trigger_message_at <= state.sent_at:
            reasons.append(INBOUND_PREDATES_SUMMARY)

    # 4. This very turn does not modify the order. Overlaps with condition 2 by
    #    design: 2 catches a change persisted on an earlier turn, 4 catches one
    #    riding along with the confirmation itself.
    if order_mutations(prior_context, accepted):
        reasons.append(ORDER_MODIFIED_THIS_TURN)

    return (not reasons), tuple(reasons)


# ---------------------------------------------------------------------------
# Rendering (ADR-010 §1)
# ---------------------------------------------------------------------------
def summary_missing_fields(context: dict) -> list[str]:
    """Which of the seven required fields are still missing, in order."""
    context = context or {}
    missing = [field for field in SUMMARY_REQUIRED if not context.get(field)]
    if "quantity" not in missing and coerce_quantity(context.get("quantity")) is None:
        missing.append("quantity")
    return missing


def should_render_summary(
    merged_context: dict,
    state: SummaryState,
    unit_price,
    shipping: Shipping,
) -> bool:
    """True when the backend must send (or re-send) the order summary.

    Idempotent by construction: it only fires when there is no summary yet or
    when the fingerprint moved. The backend therefore can NEVER repeat the same
    summary, which is why the circuit breaker can stay where it is, comparing
    the LLM's text before the merge.

    H6 is resolved structurally here: the summary needs the price, the price
    needs product_id, so there is no summary without a resolved product — and
    no confirmation without a summary.
    """
    merged_context = merged_context or {}
    if summary_missing_fields(merged_context) or unit_price is None:
        return False
    if merged_context.get("user_confirmation"):
        return False
    return compute_fingerprint(merged_context, shipping) != state.fingerprint


def _presentation(business_rules: Optional[dict], language: str, plural: bool) -> str:
    """Unit noun for the order line ("bolsas de 340g" / "340g bags").

    Data, not code: the presentation belongs to the tenant's business_rules, so
    a second client selling something else is a data edit and not a patch here.
    """
    default = {
        "es": ("bolsa de 340g", "bolsas de 340g"),
        "en": ("340g bag", "340g bags"),
    }[language]
    rules = (business_rules or {}).get("presentation") or {}
    singular = rules.get("{}_singular".format(language)) or default[0]
    return (rules.get(language) or default[1]) if plural else singular


def _grind_phrase(grind, language: str) -> str:
    """Render the grind clause, passing unusual answers through verbatim.

    Customers say "grano", "molido", and also "2 en grano y 2 molidos". The
    backend does not try to parse that: it repeats what they said, which is
    what a person paraphrasing an order would do.
    """
    normalized = _normalize_field("grind_preference", grind)
    known = {
        "grano": {"es": "en grano", "en": "whole bean"},
        "en grano": {"es": "en grano", "en": "whole bean"},
        "molido": {"es": "molido", "en": "ground"},
        "molida": {"es": "molido", "en": "ground"},
    }
    if normalized in known:
        return known[normalized][language]
    return str(grind).strip()


def render_summary(
    context: dict,
    unit_price,
    shipping: Shipping,
    language: str,
    business_rules: Optional[dict] = None,
) -> str:
    """The order summary, in running prose, in the customer's language.

    Deliberately NOT a form ("Name:", "Phone:") — the label style gives the bot
    away. And no em dash anywhere: that character is vanishingly rare in casual
    WhatsApp writing and reads as machine-written. Comma or period instead.
    """
    language = "en" if language == "en" else "es"
    context = context or {}
    quantity = coerce_quantity(context.get("quantity"))
    subtotal = compute_coffee_subtotal(quantity, unit_price)
    total = compute_total(quantity, unit_price, shipping)

    unit = _presentation(business_rules, language, plural=(quantity or 0) != 1)
    grind = _grind_phrase(context.get("grind_preference"), language)
    name = str(context.get("full_name", "")).strip()
    phone = str(context.get("phone", "")).strip()
    city = canonical_city(context.get("shipping_city"), business_rules and business_rules.get("shipping_rules"))
    address = str(context.get("shipping_address", "")).strip()

    if language == "en":
        head = "Here's the order then: {} {}, {}, for {}, at {}, in {}, {}.".format(
            quantity, unit, grind, name, phone, city, address
        )
        if total is not None:
            money = " The coffee is {} and shipping {}, total {}.".format(
                format_money(subtotal), format_money(shipping.cost), format_money(total)
            )
        else:
            money = (
                " The coffee is {}. Shipping to {} we confirm with you and I'll let"
                " you know the cost.".format(format_money(subtotal), city)
            )
        return head + money + " Does that all look right?"

    # A prepositional grind ("en grano") attaches straight to the unit; anything
    # else has to be set off by commas or it disagrees in gender and number
    # ("2 bolsas ... molido"). This also keeps a verbatim answer like "2 en grano
    # y 2 molidos" grammatical where it lands.
    order_line = (
        "{} {} {}".format(quantity, unit, grind)
        if grind.startswith("en ")
        else "{} {}, {},".format(quantity, unit, grind)
    )
    head = "Va el pedido entonces: {} para {}, al {}, en {}, {}.".format(
        order_line, name, phone, city, address
    )
    if total is not None:
        money = " El café son {} y el envío {}, total {}.".format(
            format_money(subtotal), format_money(shipping.cost), format_money(total)
        )
    else:
        money = (
            " El café son {}. El envío a {} lo confirmamos contigo y te aviso"
            " el valor.".format(format_money(subtotal), city)
        )
    return head + money + " ¿Todo bien con esos datos?"
