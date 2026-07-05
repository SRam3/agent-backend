"""Deterministic slot validation (ADR-008 §2).

Pure Python — no I/O. Form checks only: plausibility, not veracity
(a well-formed but invented number passes; the human operator confirms
at handoff).
"""
from __future__ import annotations

import re

# Separators customers habitually type inside phone numbers.
_PHONE_SEPARATOR_RE = re.compile(r"[\s\-().]")


def is_plausible_phone(raw) -> bool:
    """E.164-lax plausibility: 7 to 15 digits, optional leading ``+``,
    spaces/dashes/parentheses/dots ignored, anything else rejected.

    Deliberately NO country logic and NO "Colombian format" — international
    customers are first-class (ADR-008). This catches the *category* of
    obvious garbage (letters, empty, too short, too long), not every fake
    number: 14 digits fit E.164 and pass.
    """
    if not isinstance(raw, str):
        return False
    text = raw.strip()
    if text.startswith("+"):
        text = text[1:]
    cleaned = _PHONE_SEPARATOR_RE.sub("", text)
    if not (cleaned.isascii() and cleaned.isdigit()):
        return False
    return 7 <= len(cleaned) <= 15
