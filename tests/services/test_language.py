"""Tests for deterministic language detection (ADR-008 §1).

Pure Python — no database, no network, no LLM calls.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.services.language import detect_language


# ---------------------------------------------------------------------------
# English detection
# ---------------------------------------------------------------------------

def test_detects_english_stand_case():
    """El mensaje literal del stand 2026-06-16 que hoy recibe respuesta en español."""
    assert detect_language("Do you speak english? I live in medellin") == "en"


def test_detects_english_purchase_intent():
    assert detect_language("Hi, I want to buy some coffee beans, how much is shipping?") == "en"


def test_detects_english_short_greeting():
    assert detect_language("hello, do you ship to Bogota?") == "en"


def test_detects_english_without_apostrophes():
    """Escritura típica de chat: contracciones sin apóstrofe."""
    assert detect_language("im interested, whats the price") == "en"


# ---------------------------------------------------------------------------
# Spanish detection
# ---------------------------------------------------------------------------

def test_detects_spanish_purchase_intent():
    assert detect_language("Hola, quiero comprar café molido") == "es"


def test_detects_spanish_without_accents():
    """WhatsApp real: sin tildes ni signos de apertura."""
    assert detect_language("buenas, cuanto vale el envio a manizales?") == "es"


def test_spanish_orthography_is_decisive():
    assert detect_language("¿Tienen domicilio?") == "es"
    assert detect_language("mañana le pago") == "es"


def test_english_loanword_cafe_does_not_flip_to_spanish():
    """'café' aparece en inglés como préstamo; el resto de la frase debe pesar más."""
    assert detect_language("I want to buy café for my trip") == "en"


# ---------------------------------------------------------------------------
# Fallback: ante duda, "es" (default del negocio)
# ---------------------------------------------------------------------------

def test_empty_message_falls_back_to_spanish():
    assert detect_language("") == "es"


def test_no_signal_falls_back_to_spanish():
    assert detect_language("ok") == "es"
    assert detect_language("123") == "es"
    assert detect_language("👍") == "es"


def test_ambiguous_short_message_falls_back_to_spanish():
    # "no" es token compartido es/en: no aporta señal en ninguna dirección.
    assert detect_language("no") == "es"


def test_mixed_message_with_spanish_majority():
    assert detect_language("Hello, quiero comprar el descafeinado") == "es"


def test_mixed_message_with_english_majority():
    assert detect_language("hola, do you have coffee beans for espresso? I need two bags") == "en"
