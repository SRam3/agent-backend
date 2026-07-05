"""Deterministic language detection for inbound messages (ADR-008 §1).

Pure Python — no I/O, no dependencies. Scope is es/en only: the backend
detects the customer's language per turn and orders the LLM to reply in it,
instead of hoping the model obeys a rule buried in the prompt. If the
heuristic's precision ever proves insufficient, the agreed escalation path
is `langid` (see ADR-008, Alternativa D).
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-záéíóúüñ]+")

# Orthography that (between es/en) only Spanish produces. Decisive on sight.
# Accented vowels are NOT here: they leak into English via loanwords ("café"),
# so they count as evidence below instead of deciding outright.
_DECISIVE_ES_CHARS = frozenset("ñ¿¡")
_ACCENTED_VOWELS = frozenset("áéíóúü")

# High-frequency words that belong to exactly one of the two languages.
# Tokens valid in both ("no", "a", "me", "ok", "he", "son", "sin", "ya",
# "cafe") are deliberately absent so they add no noise. Unaccented variants
# are included because WhatsApp users routinely skip accents.
_ES_WORDS = frozenset({
    # saludos / cortesía
    "hola", "buenas", "buenos", "dias", "tardes", "noches", "gracias",
    "favor", "claro", "bueno", "listo", "vale", "si",
    # función
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del",
    "al", "que", "y", "en", "es", "esta", "estas", "estoy", "estamos",
    "soy", "eres", "para", "con", "pero", "porque", "como", "cuanto",
    "cuanta", "cuando", "donde", "quien", "cual", "aqui", "alla", "ahi",
    "mas", "muy", "tambien", "hasta", "desde", "entre", "sobre", "otro",
    "otra", "todo", "toda", "todos", "nada", "algo", "usted", "ustedes",
    "tu", "te", "le", "les", "lo", "mi", "mis", "su", "sus", "este",
    "esto", "ese", "eso", "esa",
    # verbos / dominio de venta
    "quiero", "quisiera", "necesito", "busco", "tengo", "tienes", "tiene",
    "puedo", "puede", "pueden", "comprar", "compra", "pagar", "pago",
    "enviar", "envio", "envian", "domicilio", "direccion", "ciudad",
    "nombre", "apellido", "telefono", "celular", "numero", "pedido",
    "precio", "cuesta", "molido", "grano", "tostado", "bolsa", "bolsas",
})
_EN_WORDS = frozenset({
    # greetings / courtesy
    "hello", "hi", "hey", "thanks", "thank", "please", "yes", "yeah",
    "good", "great",
    # function
    "the", "an", "and", "or", "you", "your", "yours", "i", "im", "it",
    "its", "is", "are", "was", "were", "be", "been", "do", "does", "did",
    "dont", "cant", "wont", "didnt", "isnt", "doesnt", "not", "have",
    "has", "had", "will", "would", "can", "could", "should", "what",
    "when", "where", "which", "who", "how", "why", "this", "that",
    "these", "those", "my", "mine", "we", "us", "our", "they", "them",
    "their", "she", "in", "on", "at", "of", "to", "for", "with", "from",
    "about", "if", "but", "so", "just", "some", "any", "more", "much",
    "many", "there", "here",
    # verbs / sales domain
    "want", "need", "like", "get", "buy", "sell", "send", "ship",
    "shipping", "pay", "payment", "price", "speak", "english", "spanish",
    "live", "know", "tell", "help", "order", "coffee", "ground", "beans",
    "delivery", "address", "city", "name", "phone", "number",
})


def detect_language(text: str) -> str:
    """Detect the language of an inbound customer message: ``"es"`` or ``"en"``.

    Spanish-only orthography (ñ ¿ ¡) is decisive. Otherwise, unambiguous
    stopword hits per language are counted (accented vowels add Spanish
    evidence) and the higher count wins. Ties, empty and no-signal messages
    fall back to ``"es"`` — the business default.
    """
    if not text:
        return "es"
    lowered = text.lower()
    if any(ch in _DECISIVE_ES_CHARS for ch in lowered):
        return "es"

    tokens = _TOKEN_RE.findall(lowered)
    es_hits = sum(1 for t in tokens if t in _ES_WORDS)
    es_hits += sum(1 for ch in lowered if ch in _ACCENTED_VOWELS)
    en_hits = sum(1 for t in tokens if t in _EN_WORDS)
    return "en" if en_hits > es_hits else "es"
