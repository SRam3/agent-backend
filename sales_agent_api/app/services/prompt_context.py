"""Prompt context formatting for the LLM system prompt.

Transforms structured backend data (business_rules, product_catalog,
user_context) into text blocks that n8n injects into the LLM prompt.

Pure Python — no I/O, no LLM calls.
"""
from __future__ import annotations


def format_business_context(
    business_rules: dict,
    product_catalog: list[dict],
) -> str:
    """Format business rules and product catalog as a text block for the LLM.

    Produces a structured block covering:
      - Product catalog (name, price, description)
      - Shipping rules by city
      - Payment methods (with instruction on when to share)
      - Discount rules
    """
    sections: list[str] = []

    # --- Product catalog ---
    if product_catalog:
        lines = ["PRODUCT CATALOG:"]
        currency = business_rules.get("currency", "COP")
        for p in product_catalog:
            price = _format_price(p.get("price", 0), currency)
            desc = p.get("ai_description") or p.get("description") or ""
            lines.append(f"- {p['name']} ({p.get('sku', 'N/A')}): {price}")
            if desc:
                lines.append(f"  {desc}")
        lines.append("Only mention the price if the customer asks or shows real interest.")
        sections.append("\n".join(lines))

    # --- Shipping rules ---
    shipping_rules = business_rules.get("shipping_rules")
    if shipping_rules:
        lines = ["SHIPPING RULES:"]
        for city, rule in shipping_rules.items():
            if city == "international":
                lines.append(f"- International: {rule}")
            elif city == "other":
                method = rule.get("method", "")
                cost_note = rule.get("cost_note", "")
                lines.append(f"- Other cities: {method}, {cost_note}")
            elif isinstance(rule, dict):
                method = rule.get("method", "")
                cost = rule.get("cost")
                cost_note = rule.get("cost_note", "")
                if cost is not None:
                    currency = business_rules.get("currency", "COP")
                    lines.append(f"- {city}: {method} {_format_price(cost, currency)}")
                else:
                    lines.append(f"- {city}: {method}, {cost_note}")
        sections.append("\n".join(lines))

    # --- Payment methods ---
    payment_methods = business_rules.get("payment_methods")
    if payment_methods:
        lines = ["PAYMENT METHODS (share ONLY when the customer confirms they want to buy):"]
        for pm in payment_methods:
            if pm.get("type") == "bank_transfer":
                lines.append(
                    f"- {pm.get('bank', '')} {pm.get('account_type', '')}: {pm.get('account', '')}"
                )
            elif pm.get("type") == "nequi":
                lines.append(f"- Nequi: {pm.get('number', '')}")
            else:
                lines.append(f"- {pm.get('type', 'unknown')}: {pm}")
        sections.append("\n".join(lines))

    # --- Discount rules ---
    discount_rules = business_rules.get("discount_rules")
    if discount_rules:
        lines = ["DISCOUNT RULES:"]
        no_discount = discount_rules.get("no_discount_message")
        if no_discount:
            lines.append(f"- Small quantities: {no_discount}")
        bulk_threshold = discount_rules.get("bulk_threshold")
        bulk_message = discount_rules.get("bulk_message")
        if bulk_threshold and bulk_message:
            lines.append(f"- {bulk_threshold}+ units: {bulk_message}")
        lines.append("Never invent discount percentages.")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def format_conversation_summary(
    user_context: dict,
    extracted_context: dict,
) -> str:
    """Generate a brief summary of what we already know about the customer.

    Helps the LLM have context without reading all 20 recent messages.
    """
    known: list[str] = []

    # From user_context (profile data)
    display_name = user_context.get("display_name")
    if display_name:
        known.append(f"display_name: {display_name}")

    if user_context.get("has_full_name"):
        known.append("full name: on file")
    if user_context.get("has_email"):
        known.append("email: on file")
    if user_context.get("has_address"):
        known.append("address: on file")
    if user_context.get("has_city"):
        known.append("city: on file")

    # From extracted_context (conversation-level data)
    for field in ("intent", "product_id", "full_name", "shipping_address",
                  "shipping_city", "order_id", "user_confirmation",
                  "payment_confirmation"):
        value = extracted_context.get(field)
        if value:
            known.append(f"{field}: {value}")

    if not known:
        return "CUSTOMER CONTEXT: New customer, no data collected yet."

    lines = ["CUSTOMER CONTEXT (data already collected):"]
    for item in known:
        lines.append(f"  - {item}")

    return "\n".join(lines)


def _format_price(amount: float | int, currency: str = "COP") -> str:
    """Format a price for display."""
    if currency == "COP":
        return f"${int(amount):,} COP".replace(",", ".")
    return f"{amount} {currency}"
