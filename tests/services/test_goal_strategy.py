"""Tests for GoalStrategyEngine (post-simplification).

The close_sale DAG no longer has `intent_identified` (inferred from product_matched)
nor `order_created` (orders are not materialized in DB — payment handoff is manual).

All pure Python — no database, no network, no LLM calls.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

import pytest
from app.services.goal_strategy import (
    BLOCKED,
    COMPLETE,
    IN_PROGRESS,
    PENDING,
    GoalStrategyEngine,
)

engine = GoalStrategyEngine()


# ---------------------------------------------------------------------------
# close_sale DAG progression
# ---------------------------------------------------------------------------

def test_01_empty_data_targets_product():
    """With no data collected, engine targets product_matched."""
    d = engine.compute("close_sale", {})
    assert d.current_checkpoint == "product_matched"
    assert "product_id" in d.missing_fields
    assert d.progress_pct == 0
    assert not d.all_complete


def test_02_product_set_targets_lead_qualified():
    """With product known, next checkpoint is lead_qualified."""
    d = engine.compute("close_sale", {"product_id": "abc"})
    assert d.current_checkpoint == "lead_qualified"
    assert "full_name" in d.missing_fields


def test_03_product_and_name_still_needs_phone():
    """With product + name but no phone, still on lead_qualified."""
    d = engine.compute("close_sale", {"product_id": "abc", "full_name": "Juan"})
    assert d.current_checkpoint == "lead_qualified"
    assert "phone" in d.missing_fields


def test_04_qualified_targets_shipping():
    """With product + name + phone, target shipping_info_collected."""
    d = engine.compute(
        "close_sale",
        {"product_id": "abc", "full_name": "Juan", "phone": "3001234567"},
    )
    assert d.current_checkpoint == "shipping_info_collected"
    assert "shipping_address" in d.missing_fields or "shipping_city" in d.missing_fields


def test_05_shipping_address_but_missing_city():
    """Partial shipping — still on shipping_info_collected with city missing."""
    d = engine.compute(
        "close_sale",
        {
            "product_id": "abc",
            "full_name": "Juan",
            "phone": "3001234567",
            "shipping_address": "Calle 10",
        },
    )
    assert d.current_checkpoint == "shipping_info_collected"
    assert "shipping_city" in d.missing_fields


def test_06_full_shipping_targets_user_confirmed():
    """With all shipping info + lead, target user_confirmed (summary stage)."""
    d = engine.compute(
        "close_sale",
        {
            "product_id": "abc",
            "full_name": "Juan",
            "phone": "3001234567",
            "shipping_address": "Calle 10",
            "shipping_city": "Manizales",
        },
    )
    assert d.current_checkpoint == "user_confirmed"
    assert "user_confirmation" in d.missing_fields


def test_07_user_confirmed_targets_payment():
    """With user_confirmation set, target payment_confirmed."""
    d = engine.compute(
        "close_sale",
        {
            "product_id": "abc",
            "full_name": "Juan",
            "phone": "3001234567",
            "shipping_address": "Calle 10",
            "shipping_city": "Manizales",
            "user_confirmation": True,
        },
    )
    assert d.current_checkpoint == "payment_confirmed"
    assert "payment_confirmation" in d.missing_fields


# ---------------------------------------------------------------------------
# All complete
# ---------------------------------------------------------------------------

def test_08_all_complete_returns_100():
    """When all fields are present, progress should be 100% and all_complete=True."""
    d = engine.compute(
        "close_sale",
        {
            "product_id": "abc",
            "full_name": "Juan",
            "phone": "3001234567",
            "shipping_address": "Calle 10",
            "shipping_city": "Manizales",
            "user_confirmation": True,
            "payment_confirmation": True,
        },
    )
    assert d.all_complete is True
    assert d.progress_pct == 100
    assert d.missing_fields == []


# ---------------------------------------------------------------------------
# Business rule overrides
# ---------------------------------------------------------------------------

def test_09_skip_lead_qualification_jumps_to_shipping():
    """skip_lead_qualification=True should remove lead_qualified checkpoint."""
    d = engine.compute(
        "close_sale",
        {"product_id": "abc"},
        business_rules={"skip_lead_qualification": True},
    )
    assert d.current_checkpoint == "shipping_info_collected"
    assert "full_name" not in d.missing_fields


def test_10_require_id_number_adds_field():
    """require_id_number=True should add identification_number to lead_qualified."""
    d = engine.compute(
        "close_sale",
        {"product_id": "abc"},
        business_rules={"require_id_number": True},
    )
    assert d.current_checkpoint == "lead_qualified"
    assert "identification_number" in d.missing_fields


def test_11_require_email_adds_field():
    """require_email=True should add email to lead_qualified fields."""
    d = engine.compute(
        "close_sale",
        {"product_id": "abc"},
        business_rules={"require_email": True},
    )
    assert d.current_checkpoint == "lead_qualified"
    assert "email" in d.missing_fields


# ---------------------------------------------------------------------------
# Progress percentages
# ---------------------------------------------------------------------------

def test_12_progress_0_at_start():
    d = engine.compute("close_sale", {})
    assert d.progress_pct == 0


def test_13_progress_increases_as_checkpoints_complete():
    """Progress % should increase as checkpoints complete."""
    d0 = engine.compute("close_sale", {})
    d1 = engine.compute("close_sale", {"product_id": "abc"})
    d2 = engine.compute(
        "close_sale",
        {"product_id": "abc", "full_name": "Juan", "phone": "3001234567"},
    )
    assert d0.progress_pct < d1.progress_pct
    assert d1.progress_pct < d2.progress_pct


def test_14_progress_100_when_all_complete():
    d = engine.compute(
        "close_sale",
        {
            "product_id": "abc",
            "full_name": "Juan",
            "phone": "3001234567",
            "shipping_address": "Calle 10",
            "shipping_city": "Manizales",
            "user_confirmation": True,
            "payment_confirmation": True,
        },
    )
    assert d.progress_pct == 100


# ---------------------------------------------------------------------------
# to_prompt() formatting
# ---------------------------------------------------------------------------

def test_15_prompt_contains_progress():
    d = engine.compute("close_sale", {})
    prompt = d.to_prompt()
    assert "SALES PROGRESS" in prompt
    assert "HINT" in prompt


def test_16_prompt_contains_progress_bar():
    d = engine.compute("close_sale", {})
    prompt = d.to_prompt()
    assert "PROGRESS" in prompt
    assert "%" in prompt
    assert "░" in prompt or "█" in prompt


def test_17_prompt_lists_missing_fields():
    d = engine.compute("close_sale", {})
    prompt = d.to_prompt()
    assert "NEXT INFO NEEDED" in prompt
    assert "product_id" in prompt


def test_18_prompt_answers_customer_first():
    d = engine.compute("close_sale", {"product_id": "abc"})
    prompt = d.to_prompt()
    assert "warm, natural conversation" in prompt
    assert "Do NOT mention or push toward the next step" in prompt


def test_19_no_intent_checkpoint_remains():
    """Regression: intent_identified should no longer be a checkpoint."""
    d = engine.compute("close_sale", {})
    assert d.current_checkpoint != "intent_identified"
    # And a large collected_data without `intent` should still reach all_complete
    d2 = engine.compute(
        "close_sale",
        {
            "product_id": "abc",
            "full_name": "Juan",
            "phone": "3001234567",
            "shipping_address": "Calle 10",
            "shipping_city": "Manizales",
            "user_confirmation": True,
            "payment_confirmation": True,
        },
    )
    assert d2.all_complete
