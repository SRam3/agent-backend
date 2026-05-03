"""Tests for the image-already-sent gate helper.

Pure Python — the DB lookup that decides image_already_sent lives in
process_agent_action; the filter logic itself is a pure function so it can
be tested in isolation.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.services.agent_action import _filter_send_image_url


def test_passes_through_when_no_image_in_payload():
    """Nothing to filter — extracted_data has no send_image_url."""
    data = {"phone": "3001234567", "full_name": "Juan Pérez"}
    clean, warnings = _filter_send_image_url(data, image_already_sent=False)
    assert clean == data
    assert warnings == []


def test_passes_through_when_no_image_even_if_flag_set():
    """The flag only matters when send_image_url is present."""
    data = {"phone": "3001234567"}
    clean, warnings = _filter_send_image_url(data, image_already_sent=True)
    assert clean == data
    assert warnings == []


def test_keeps_image_when_not_yet_sent():
    """First time the LLM emits the image — let it through."""
    data = {"send_image_url": "https://example.com/coffee.jpg", "phone": "3001234567"}
    clean, warnings = _filter_send_image_url(data, image_already_sent=False)
    assert clean == data
    assert warnings == []


def test_drops_image_when_already_sent():
    """Second emission of the same image — drop it, emit warning."""
    data = {"send_image_url": "https://example.com/coffee.jpg", "phone": "3001234567"}
    clean, warnings = _filter_send_image_url(data, image_already_sent=True)
    assert "send_image_url" not in clean
    assert clean["phone"] == "3001234567"
    assert warnings == ["warning:image_already_sent"]


def test_does_not_mutate_input():
    """Caller may inspect extracted_data after — don't mutate it in place."""
    data = {"send_image_url": "https://x.jpg", "phone": "3001234567"}
    snapshot = dict(data)
    _filter_send_image_url(data, image_already_sent=True)
    assert data == snapshot


def test_handles_empty_input():
    clean, warnings = _filter_send_image_url({}, image_already_sent=True)
    assert clean == {}
    assert warnings == []


def test_treats_empty_string_as_no_image():
    """LLM sometimes emits send_image_url='' when it shouldn't have set the
    field at all. Empty string is falsy → nothing to gate."""
    data = {"send_image_url": "", "phone": "3001234567"}
    clean, warnings = _filter_send_image_url(data, image_already_sent=True)
    assert clean == data
    assert warnings == []


def test_treats_none_as_no_image():
    data = {"send_image_url": None, "phone": "3001234567"}
    clean, warnings = _filter_send_image_url(data, image_already_sent=True)
    assert clean == data
    assert warnings == []
