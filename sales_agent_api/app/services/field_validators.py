"""Field-level validators for the LLM's extracted_data payload.

Why this exists: the LLM has been observed sending values that are syntactically
valid (truthy strings) but semantically wrong — e.g. phone="123456",
shipping_city="acá", full_name="Juan". The DAG gates protect ORDER but not
PRECISION. These validators reject ill-formed values BEFORE they get merged
into extracted_context or persisted to the customer profile.

Pure Python — no I/O. Each validator returns (is_valid, reason).

Forward compatibility note: when we adopt structured-output (json_schema strict)
for the LLM call (Fase B), the patterns here become the source of truth for
the schema as well. Keep validators free of side effects so the rules can be
serialized into JSON Schema patterns when needed.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Callable, Optional

ValidatorResult = tuple[bool, str]
Validator = Callable[[Any], ValidatorResult]


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
# Colombian mobile: 10 digits starting with 3 (e.g. 3001234567).
# We strip optional country code (57) and non-digit characters before matching.
_PHONE_RE = re.compile(r"^3\d{9}$")

# Permissive email; good enough for sales context. Not RFC-compliant by design.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Words that mean "here/there" in Spanish — never accept as a city name.
# The bot was observed capturing "acá" as shipping_city in the past.
_DEICTIC_CITIES = {
    "aca", "acá",
    "aqui", "aquí",
    "alla", "allá",
    "ahi", "ahí",
    "por aqui", "por aquí",
    "por aca", "por acá",
    "por alla", "por allá",
    "aqui mismo", "aquí mismo",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _digits_only(value: Any) -> str:
    if value is None:
        return ""
    return "".join(c for c in str(value) if c.isdigit())


def _normalize_phone(value: Any) -> str:
    """Strip non-digits and the optional Colombia country code (57)."""
    digits = _digits_only(value)
    if len(digits) == 12 and digits.startswith("57"):
        digits = digits[2:]
    return digits


# ---------------------------------------------------------------------------
# Individual validators
# ---------------------------------------------------------------------------
def validate_phone(value: Any) -> ValidatorResult:
    """Colombian mobile: 10 digits starting with 3 after normalization."""
    digits = _normalize_phone(value)
    if not _PHONE_RE.match(digits):
        return False, f"phone must be Colombian mobile (10 digits starting with 3); got '{value}'"
    return True, ""


def validate_email(value: Any) -> ValidatorResult:
    if not isinstance(value, str):
        return False, "email must be a string"
    if not _EMAIL_RE.match(value.strip()):
        return False, f"email format invalid: '{value}'"
    return True, ""


def validate_full_name(value: Any) -> ValidatorResult:
    """At least two words, each ≥2 chars. Avoids 'Juan' or 'A B' passing."""
    if not isinstance(value, str):
        return False, "full_name must be a string"
    parts = value.strip().split()
    if len(parts) < 2:
        return False, f"full_name must include first + last name; got '{value}'"
    if any(len(p) < 2 for p in parts[:2]):
        return False, f"full_name parts too short: '{value}'"
    return True, ""


def validate_shipping_city(value: Any) -> ValidatorResult:
    if not isinstance(value, str):
        return False, "shipping_city must be a string"
    s = value.strip()
    if len(s) < 3:
        return False, f"shipping_city too short: '{value}'"
    if s.lower() in _DEICTIC_CITIES:
        return False, f"shipping_city is a deictic word, not a city: '{value}'"
    return True, ""


def validate_quantity(value: Any) -> ValidatorResult:
    """Positive integer, ≤ 999 (sanity ceiling for a coffee bag order).

    Rejects floats outright — even integer-valued ones — because the LLM
    sometimes emits 2.0 by mistake and we want to surface that as a warning
    rather than silently coerce. Booleans are also rejected (Python treats
    True/False as int subclass).
    """
    if isinstance(value, bool) or isinstance(value, float):
        return False, f"quantity must be an integer, not {type(value).__name__}; got '{value}'"
    try:
        n = int(value)
    except (ValueError, TypeError):
        return False, f"quantity must be a positive integer; got '{value}'"
    if n <= 0:
        return False, f"quantity must be positive; got {n}"
    if n > 999:
        return False, f"quantity exceeds sane ceiling (999); got {n}"
    return True, ""


def validate_product_id(
    value: Any,
    valid_product_ids: Optional[set[str]] = None,
) -> ValidatorResult:
    """Must be a valid UUID. If a catalog set is provided, must be in it.

    Catalog membership is the strong check; UUID format alone catches the
    common case where the LLM sends the SKU instead of the id.
    """
    if not isinstance(value, str):
        return False, "product_id must be a string"
    try:
        u = uuid.UUID(value)
    except (ValueError, AttributeError):
        return False, f"product_id is not a valid UUID: '{value}'"
    if valid_product_ids is not None and str(u) not in valid_product_ids:
        return False, f"product_id not in catalog: '{value}'"
    return True, ""


# ---------------------------------------------------------------------------
# Public dict + entry point
# ---------------------------------------------------------------------------
# Fields without an entry pass through unvalidated (e.g. boolean confirmations,
# free-text grind_preference, send_image_url which is gated separately).
FIELD_VALIDATORS: dict[str, Validator] = {
    "phone": validate_phone,
    "email": validate_email,
    "full_name": validate_full_name,
    "shipping_city": validate_shipping_city,
    "quantity": validate_quantity,
}


def validate_extracted_data(
    extracted_data: dict,
    valid_product_ids: Optional[set[str]] = None,
) -> tuple[dict, list[str]]:
    """Run validators over an extracted_data dict.

    Returns (clean_data, rejection_warnings):
      - clean_data: same as input minus fields that failed validation
      - rejection_warnings: side_effect strings ready to surface to the caller

    Fields not in FIELD_VALIDATORS are passed through unchanged (no validation
    rule defined for them).
    """
    clean: dict = {}
    rejections: list[str] = []
    for field, value in (extracted_data or {}).items():
        if field == "product_id":
            ok, reason = validate_product_id(value, valid_product_ids)
        elif field in FIELD_VALIDATORS:
            ok, reason = FIELD_VALIDATORS[field](value)
        else:
            ok, reason = True, ""
        if ok:
            clean[field] = value
        else:
            # Truncate reason to keep side_effect strings manageable.
            rejections.append(f"warning:invalid_{field}:{reason[:120]}")
    return clean, rejections
