"""Tests for the field_validators service.

Pure Python — no DB, no network. Each validator is exercised against the
exact patterns we have seen the LLM emit (good and bad).
"""
import sys
import os
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.services.field_validators import (
    FIELD_VALIDATORS,
    validate_email,
    validate_extracted_data,
    validate_full_name,
    validate_phone,
    validate_product_id,
    validate_quantity,
    validate_shipping_city,
)


# ---------------------------------------------------------------------------
# validate_phone
# ---------------------------------------------------------------------------
def test_phone_accepts_canonical_co_mobile():
    ok, _ = validate_phone("3001234567")
    assert ok


def test_phone_accepts_with_country_code():
    ok, _ = validate_phone("573001234567")
    assert ok


def test_phone_accepts_with_plus_and_spaces():
    ok, _ = validate_phone("+57 300 123 4567")
    assert ok


def test_phone_rejects_short_input_observed_may2():
    """The exact bug from the May 2 review: customer typed '123456'."""
    ok, reason = validate_phone("123456")
    assert not ok
    assert "Colombian" in reason


def test_phone_rejects_too_many_digits():
    ok, _ = validate_phone("12345678910")
    assert not ok


def test_phone_rejects_non_mobile_prefix():
    """Landlines (start with 6/7/8) are not accepted; we only do mobile."""
    ok, _ = validate_phone("6041234567")
    assert not ok


def test_phone_rejects_letters():
    ok, _ = validate_phone("abc")
    assert not ok


def test_phone_rejects_none():
    ok, _ = validate_phone(None)
    assert not ok


# ---------------------------------------------------------------------------
# validate_email
# ---------------------------------------------------------------------------
def test_email_accepts_simple():
    ok, _ = validate_email("juan@example.com")
    assert ok


def test_email_accepts_subdomains_and_plus():
    ok, _ = validate_email("juan+filter@mail.example.co")
    assert ok


def test_email_rejects_no_at():
    ok, _ = validate_email("juan.example.com")
    assert not ok


def test_email_rejects_no_domain():
    ok, _ = validate_email("juan@")
    assert not ok


def test_email_rejects_whitespace_inside():
    ok, _ = validate_email("juan @example.com")
    assert not ok


def test_email_rejects_non_string():
    ok, _ = validate_email(12345)
    assert not ok


# ---------------------------------------------------------------------------
# validate_full_name
# ---------------------------------------------------------------------------
def test_full_name_accepts_two_words():
    ok, _ = validate_full_name("Juan Pérez")
    assert ok


def test_full_name_accepts_three_words():
    ok, _ = validate_full_name("Sebastian Ramirez Rodriguez")
    assert ok


def test_full_name_rejects_first_only():
    """Bug observed: LLM accepted 'Sebastian' as full_name."""
    ok, reason = validate_full_name("Sebastian")
    assert not ok
    assert "first + last" in reason


def test_full_name_rejects_initials():
    ok, _ = validate_full_name("J P")
    assert not ok


def test_full_name_rejects_empty():
    ok, _ = validate_full_name("")
    assert not ok


def test_full_name_rejects_whitespace_only():
    ok, _ = validate_full_name("   ")
    assert not ok


# ---------------------------------------------------------------------------
# validate_shipping_city
# ---------------------------------------------------------------------------
def test_city_accepts_normal():
    for city in ("Manizales", "Bogotá", "Cali", "Envigado", "San Gil"):
        ok, _ = validate_shipping_city(city)
        assert ok, f"should accept {city}"


def test_city_rejects_deictic_aca():
    ok, reason = validate_shipping_city("acá")
    assert not ok
    assert "deictic" in reason


def test_city_rejects_deictic_aqui():
    ok, _ = validate_shipping_city("aquí")
    assert not ok


def test_city_rejects_deictic_alla():
    ok, _ = validate_shipping_city("Allá")  # case-insensitive
    assert not ok


def test_city_rejects_too_short():
    ok, _ = validate_shipping_city("Bo")
    assert not ok


def test_city_rejects_non_string():
    ok, _ = validate_shipping_city(123)
    assert not ok


# ---------------------------------------------------------------------------
# validate_quantity
# ---------------------------------------------------------------------------
def test_quantity_accepts_positive_int():
    for n in (1, 2, 100, 999):
        ok, _ = validate_quantity(n)
        assert ok, f"should accept {n}"


def test_quantity_accepts_string_int():
    ok, _ = validate_quantity("4")
    assert ok


def test_quantity_rejects_zero():
    ok, _ = validate_quantity(0)
    assert not ok


def test_quantity_rejects_negative():
    ok, _ = validate_quantity(-3)
    assert not ok


def test_quantity_rejects_float():
    ok, _ = validate_quantity(2.5)
    assert not ok


def test_quantity_rejects_text():
    ok, _ = validate_quantity("dos")
    assert not ok


def test_quantity_rejects_above_ceiling():
    ok, _ = validate_quantity(1000)
    assert not ok


# ---------------------------------------------------------------------------
# validate_product_id
# ---------------------------------------------------------------------------
_REAL_UUID = "36d7729d-ce9a-450d-aee6-c9bca665fc63"  # actual Café Arenillo product


def test_product_id_accepts_uuid_no_catalog_check():
    ok, _ = validate_product_id(_REAL_UUID)
    assert ok


def test_product_id_accepts_uuid_in_catalog():
    ok, _ = validate_product_id(_REAL_UUID, valid_product_ids={_REAL_UUID, "other-uuid"})
    assert ok


def test_product_id_rejects_uuid_not_in_catalog():
    other = str(uuid.uuid4())
    ok, reason = validate_product_id(other, valid_product_ids={_REAL_UUID})
    assert not ok
    assert "catalog" in reason


def test_product_id_rejects_sku_instead_of_uuid():
    """Common LLM mistake: sending the SKU as product_id."""
    ok, reason = validate_product_id("CAFE-001")
    assert not ok
    assert "UUID" in reason


def test_product_id_rejects_garbage():
    ok, _ = validate_product_id("not-a-uuid-at-all")
    assert not ok


def test_product_id_rejects_non_string():
    ok, _ = validate_product_id(12345)
    assert not ok


# ---------------------------------------------------------------------------
# validate_extracted_data (the public entry point)
# ---------------------------------------------------------------------------
def test_extracted_data_filters_invalid_fields_keeps_valid():
    extracted = {
        "phone": "123456",                # invalid
        "full_name": "Sebastian",          # invalid (first only)
        "shipping_city": "Manizales",      # valid
        "quantity": 4,                     # valid
        "user_confirmation": True,         # not in validators -> passes through
    }
    clean, warnings = validate_extracted_data(extracted)
    assert "phone" not in clean
    assert "full_name" not in clean
    assert clean["shipping_city"] == "Manizales"
    assert clean["quantity"] == 4
    assert clean["user_confirmation"] is True
    assert any("invalid_phone" in w for w in warnings)
    assert any("invalid_full_name" in w for w in warnings)


def test_extracted_data_validates_product_id_against_catalog():
    catalog = {_REAL_UUID}
    other = str(uuid.uuid4())
    clean, warnings = validate_extracted_data(
        {"product_id": other, "phone": "3001234567"},
        valid_product_ids=catalog,
    )
    assert "product_id" not in clean
    assert clean["phone"] == "3001234567"
    assert any("invalid_product_id" in w for w in warnings)


def test_extracted_data_empty_input_returns_empty():
    clean, warnings = validate_extracted_data({})
    assert clean == {}
    assert warnings == []


def test_extracted_data_none_safe():
    clean, warnings = validate_extracted_data(None)  # type: ignore[arg-type]
    assert clean == {}
    assert warnings == []


def test_extracted_data_passes_through_unmapped_fields():
    """Fields without a validator (e.g. grind_preference) should not be filtered."""
    clean, warnings = validate_extracted_data(
        {"grind_preference": "1 molido y 2 grano", "send_image_url": "https://x.com/y.jpg"}
    )
    assert clean["grind_preference"] == "1 molido y 2 grano"
    assert "send_image_url" in clean
    assert warnings == []


def test_extracted_data_warning_format_is_parseable():
    """Side-effect warnings should follow 'warning:invalid_<field>:<reason>' shape."""
    _, warnings = validate_extracted_data({"phone": "abc"})
    assert len(warnings) == 1
    parts = warnings[0].split(":", 2)
    assert parts[0] == "warning"
    assert parts[1] == "invalid_phone"
    assert len(parts[2]) > 0


def test_field_validators_dict_includes_expected_keys():
    expected = {"phone", "email", "full_name", "shipping_city", "quantity"}
    assert expected.issubset(set(FIELD_VALIDATORS.keys()))
