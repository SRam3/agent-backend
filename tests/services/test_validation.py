"""Tests for deterministic slot validation (ADR-008 §2).

Pure Python — no database, no network, no LLM calls.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.services.validation import is_plausible_phone


# ---------------------------------------------------------------------------
# Plausible numbers (E.164-lax: 7-15 digits)
# ---------------------------------------------------------------------------

def test_stand_case_14_digits_passes():
    """El número del stand: 14 dígitos CABE en E.164 (≤15) → True.
    El problema del stand no era el largo sino la ausencia total de
    validación. Rechazar >13 sería otra decisión de negocio (no asumida)."""
    assert is_plausible_phone("31071484777779") is True


def test_colombian_mobile_with_country_code():
    assert is_plausible_phone("+57 300 123 4567") is True


def test_international_us_number():
    assert is_plausible_phone("+1 415 555 2671") is True


def test_local_10_digits_bare():
    assert is_plausible_phone("3001234567") is True


def test_minimum_7_digits():
    assert is_plausible_phone("1234567") is True


def test_maximum_15_digits():
    assert is_plausible_phone("123456789012345") is True


def test_common_separators_ignored():
    assert is_plausible_phone("(310) 714-8477") is True
    assert is_plausible_phone("310.714.8477") is True


# ---------------------------------------------------------------------------
# Obvious garbage rejected
# ---------------------------------------------------------------------------

def test_letters_rejected():
    assert is_plausible_phone("hola") is False
    assert is_plausible_phone("300 123 456A") is False


def test_too_short_rejected():
    assert is_plausible_phone("123") is False
    assert is_plausible_phone("123456") is False


def test_too_long_rejected():
    assert is_plausible_phone("1234567890123456") is False


def test_empty_and_non_string_rejected():
    assert is_plausible_phone("") is False
    assert is_plausible_phone("   ") is False
    assert is_plausible_phone(None) is False
    assert is_plausible_phone(3001234567) is False


def test_plus_only_allowed_at_start():
    assert is_plausible_phone("+573001234567") is True
    assert is_plausible_phone("300+1234567") is False
    assert is_plausible_phone("++573001234567") is False
