"""Guardianes de las invariantes que no tenían prueba propia.

Estas pruebas nacieron del modelo de sistema
(`sales-ai-docs/docs/system-model/`). Cada una existe porque una invariante
declaraba cumplirse y nada en la suite la ejercitaba — que es justo la
combinación que el verificador prohíbe.

Dos son de forma distinta al resto de la suite: leen el CÓDIGO FUENTE en vez de
llamar a una función. Esa forma se usa a propósito para las invariantes
estructurales ("nadie hace X en ninguna parte"), que no se pueden probar
ejercitando un camino porque afirman algo sobre TODOS los caminos, incluidos los
que todavía no existen. Es una red gruesa, no fina: atrapa el UPDATE escrito con
el ORM, no uno escondido en un `text()` con SQL crudo. Se declara así en el
modelo y no se pretende más.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

import pytest
from app.services.state_machine import (
    STATES,
    InvalidTransitionError,
    UnknownStateError,
    is_valid_state,
    validate_transition,
)

SERVICES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "sales_agent_api", "app"
)


def _python_sources():
    """Todo el código de la app, con su ruta relativa para el mensaje de fallo."""
    for root, _dirs, files in os.walk(SERVICES_DIR):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                yield os.path.relpath(path, SERVICES_DIR), fh.read()


def _offending_lines(pattern: re.Pattern) -> list[str]:
    hits = []
    for rel_path, source in _python_sources():
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                hits.append(f"{rel_path}:{lineno}: {stripped}")
    return hits


# --------------------------------------------------------------------------- #
# INV-MSG-001 — el trail es append-only
# --------------------------------------------------------------------------- #

# `update(Message)` / `delete(Message)` construidos con el ORM, más la variante
# que pasa por la tabla. No cubre SQL crudo dentro de `text(...)`.
_MESSAGE_MUTATION = re.compile(r"\b(update|delete)\s*\(\s*Message\b|Message\.__table__\.(update|delete)\b")
_AUDIT_MUTATION = re.compile(r"\b(update|delete)\s*\(\s*AuditLog\b|AuditLog\.__table__\.(update|delete)\b")


def test_no_backend_path_updates_or_deletes_a_message():
    """INV-MSG-001: una fila de `messages` se escribe una vez y no se toca más.

    Todo postmortem de este proyecto se escribe releyendo el trail meses
    después. Una fila editada no deja rastro de haberlo sido.
    """
    hits = _offending_lines(_MESSAGE_MUTATION)
    assert not hits, (
        "Alguien mutó `messages`, que es append-only (INV-MSG-001):\n  "
        + "\n  ".join(hits)
    )


def test_no_backend_path_updates_or_deletes_an_audit_row():
    """INV-MSG-001: `audit_log` es el registro de lo que pasó, no un borrador."""
    hits = _offending_lines(_AUDIT_MUTATION)
    assert not hits, (
        "Alguien mutó `audit_log`, que es append-only (INV-MSG-001):\n  "
        + "\n  ".join(hits)
    )


# --------------------------------------------------------------------------- #
# INV-CONV-001 / INV-CONV-002 — la máquina de estados
# --------------------------------------------------------------------------- #


def test_the_state_machine_has_exactly_the_three_declared_states():
    """INV-CONV-001: tres estados, ni uno más.

    El refactor de 2026-04-19 (ADR-007) colapsó una máquina más grande porque los
    estados intermedios se volvían inalcanzables sin que nadie lo notara. Un
    cuarto estado agregado sin pensarlo repite esa historia.
    """
    assert set(STATES) == {"active", "human_handoff", "closed"}
    for state in STATES:
        assert is_valid_state(state)
    assert not is_valid_state("paused")


def test_every_declared_transition_validates():
    """INV-CONV-001: la tabla es la definición, no una sugerencia."""
    for current, targets in STATES.items():
        for target in targets:
            validate_transition(current, target)  # no debe levantar


def test_every_undeclared_transition_is_refused():
    """INV-CONV-001: lo que no está en la tabla se rechaza, incluida la identidad.

    Ninguna transición a sí mismo está declarada, y eso es deliberado: escribir
    el estado que ya se tiene siempre es un bug de quien llama.
    """
    for current in STATES:
        for target in STATES:
            if target in STATES[current]:
                continue
            with pytest.raises(InvalidTransitionError):
                validate_transition(current, target)


def test_closed_is_terminal():
    """INV-CONV-002: de `closed` no se sale.

    `confirm_payment` no bumpea `strategy_version`, así que un turno en vuelo
    cuando el operador cierra la venta pasa el chequeo de staleness y, si esto
    cediera, resucitaría la conversación a `human_handoff` — que es la venta
    duplicada del 2026-07-20 otra vez.
    """
    assert STATES["closed"] == []
    for target in ("active", "human_handoff", "closed"):
        with pytest.raises(InvalidTransitionError):
            validate_transition("closed", target)


def test_an_unknown_state_is_refused_on_both_sides():
    """INV-CONV-001: un estado que no existe no se valida ni de origen ni de destino."""
    with pytest.raises(UnknownStateError):
        validate_transition("paused", "closed")
    with pytest.raises(UnknownStateError):
        validate_transition("active", "paused")
