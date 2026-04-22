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
            lines.append(f"- {p['name']} (id: {p['id']}, sku: {p.get('sku', 'N/A')}): {price}")
            if desc:
                lines.append(f"  {desc}")
            if p.get("image_url"):
                lines.append(f"  image_url: {p['image_url']}")
        lines.append("Only mention the price if the customer asks or shows real interest.")
        lines.append("You can ONLY sell products listed above. NEVER invent or mention products not in this catalog.")
        lines.append("PRODUCT_ID RULE: extracted_data.product_id MUST be the 'id' UUID shown above, NEVER the sku.")
        if any(p.get("image_url") for p in product_catalog):
            lines.append("IMAGES: Send the product photo ONCE per conversation. Only set extracted_data.send_image_url the FIRST time the customer asks to see the product. If you already sent the image earlier in this conversation, do NOT include send_image_url again, even if the customer keeps talking about the product.")
        sections.append("\n".join(lines))

    # --- Shipping rules ---
    shipping_rules = business_rules.get("shipping_rules")
    if shipping_rules:
        lines = ["SHIPPING RULES (all values are approximate, pending carrier confirmation):"]
        currency = business_rules.get("currency", "COP")
        for city, rule in shipping_rules.items():
            if city in ("international", "zones"):
                continue
            elif city == "other":
                method = rule.get("method", "")
                cost_note = rule.get("cost_note", "")
                lines.append(f"- Other cities: {method}, {cost_note}")
            elif isinstance(rule, dict):
                method = rule.get("method", "")
                cost = rule.get("cost")
                cost_note = rule.get("cost_note", "")
                if cost is not None:
                    lines.append(f"- {city}: {method} aprox. {_format_price(cost, currency)}")
                else:
                    lines.append(f"- {city}: {method}, {cost_note}")
        # Render zones for cities not explicitly listed
        zones = shipping_rules.get("zones")
        if zones:
            lines.append("SHIPPING ZONES (for cities not listed above):")
            for zone_name, zone_info in zones.items():
                if isinstance(zone_info, dict):
                    cost = zone_info.get("cost_range", zone_info.get("cost_note", ""))
                    lines.append(f"- {zone_name}: {zone_info.get('method', 'transportadora')}, aprox. {cost}")
        international = shipping_rules.get("international")
        if international:
            lines.append(f"- International: {international}")
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


# Campos del pedido ordenados según el DAG de close_sale. La tupla es
# (clave en extracted_context, etiqueta humana, etiqueta corta para "FALTA").
_ORDER_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("product_id",          "Producto",             "producto"),
    ("quantity",            "Cantidad",             "cantidad (número de bolsas)"),
    ("grind_preference",    "Preferencia de molido","preferencia de molido (grano/molido)"),
    ("full_name",           "Nombre completo",      "nombre completo"),
    ("phone",               "Teléfono",             "teléfono"),
    ("shipping_city",       "Ciudad",               "ciudad"),
    ("shipping_address",    "Dirección",            "dirección"),
    ("user_confirmation",   "Confirmación del cliente", "confirmación del cliente"),
    ("payment_confirmation","Pago confirmado",      "comprobante de pago"),
)


def format_customer_profile(
    display_name: str | None,
    profile: dict,
) -> str:
    """Bloque de perfil del cliente — qué sabemos de él antes de esta conversación.

    Se renderiza al inicio del prompt. Si el cliente es nuevo (profile vacío),
    lo declara explícitamente para que el LLM se presente normalmente. Si es
    recurrente, lo nombra por su nombre de pila y lista compras previas y
    preferencias persistidas.
    """
    profile = profile or {}
    lines = ["=== CLIENTE ==="]

    first_name = profile.get("first_name") or (
        profile.get("full_name", "").split()[0] if profile.get("full_name") else None
    )

    if not profile and not display_name:
        lines.append("Cliente nuevo. No tenemos datos previos.")
        lines.append("INSTRUCCIÓN: Preséntate brevemente y pregunta en qué le puedes ayudar.")
        return "\n".join(lines)

    if profile:
        lines.append("Cliente que ya conocemos. Datos en archivo:")
        if first_name:
            lines.append(f"  • Nombre: {first_name}")
        if profile.get("full_name"):
            lines.append(f"  • Nombre completo: {profile['full_name']}")
        if profile.get("email"):
            lines.append(f"  • Email: {profile['email']}")
        if profile.get("city"):
            lines.append(f"  • Ciudad: {profile['city']}")
        if profile.get("shipping_address"):
            lines.append(f"  • Dirección: {profile['shipping_address']}")
        prefs = profile.get("preferences") or {}
        if prefs.get("grind"):
            lines.append(f"  • Prefiere molido: {prefs['grind']}")
        if prefs.get("roast"):
            lines.append(f"  • Prefiere tueste: {prefs['roast']}")
        pc = profile.get("purchase_count") or 0
        if pc:
            lines.append(f"  • Compras previas: {pc}")
        lines.append("")
        if first_name:
            lines.append(
                f"INSTRUCCIÓN: Dirígete a {first_name} por su nombre. No te vuelvas a presentar "
                "ni preguntes datos que ya tenemos arriba. Saluda con cercanía (cliente recurrente)."
            )
        else:
            lines.append(
                "INSTRUCCIÓN: Es cliente recurrente. No repreguntes datos ya en archivo. "
                "Saluda con cercanía."
            )
    elif display_name:
        lines.append(f"Cliente nuevo. En WhatsApp aparece como: {display_name}")
        lines.append("INSTRUCCIÓN: Preséntate brevemente y pregunta en qué le puedes ayudar.")

    return "\n".join(lines)


def format_conversation_summary(
    user_context: dict,
    extracted_context: dict,
) -> str:
    """Resumen de estado para el LLM en español — perfil + estado del pedido.

    Se inyecta cerca del inicio del system prompt. Lo relevante para el LLM es:
      1. Quién es el cliente (perfil persistente entre conversaciones)
      2. Qué datos YA tenemos de esta conversación (nunca volver a pedir)
      3. Qué datos FALTAN para cerrar la venta (referencia, no urgencia)
    """
    display_name = user_context.get("display_name")
    profile = user_context.get("profile") or {}
    ctx = extracted_context or {}

    sections = [format_customer_profile(display_name, profile)]

    # --- ESTADO DEL PEDIDO --------------------------------------------------
    order_lines = ["=== ESTADO DEL PEDIDO ==="]

    collected = []
    missing = []
    for key, label, short in _ORDER_FIELDS:
        value = ctx.get(key)
        if value:
            collected.append((label, value))
        else:
            missing.append(short)

    if collected:
        order_lines.append("Datos recopilados en esta conversación:")
        for label, value in collected:
            order_lines.append(f"  ✓ {label}: {value}")
    else:
        order_lines.append("Aún no se ha recopilado ningún dato de pedido en esta conversación.")

    if missing:
        order_lines.append("")
        order_lines.append("Aún falta recopilar (solo referencia — NO los pidas todos de golpe):")
        for short in missing:
            order_lines.append(f"  ✗ {short}")
    else:
        order_lines.append("")
        order_lines.append("Todos los datos del pedido están completos.")

    order_lines.append("")
    order_lines.append(
        "REGLAS DE USO DE ESTE BLOQUE:\n"
        "  • NUNCA vuelvas a pedir un dato marcado con ✓. Ya lo tenemos.\n"
        "  • Responde primero lo que el cliente pregunta; los datos faltantes son guía, no urgencia.\n"
        "  • Solo pide UN dato faltante a la vez, y solo cuando la conversación lo lleve naturalmente.\n"
        "  • Si el cliente dice \"ya te lo dije\", créele: revisa arriba antes de volver a preguntar."
    )

    sections.append("\n".join(order_lines))
    return "\n\n".join(sections)


def _format_price(amount: float | int, currency: str = "COP") -> str:
    """Format a price for display."""
    if currency == "COP":
        return f"${int(amount):,} COP".replace(",", ".")
    return f"{amount} {currency}"
