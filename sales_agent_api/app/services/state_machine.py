"""Conversation state machine — 3 states.

States used in practice:
  - active:        normal conversation
  - human_handoff: escalated to a human operator
  - closed:        terminated

Pure Python — no DB, no I/O.
"""
from __future__ import annotations


STATES: dict[str, list[str]] = {
    "active":        ["human_handoff", "closed"],
    "human_handoff": ["active", "closed"],
    "closed":        [],
}


class StateMachineError(Exception):
    pass


class InvalidTransitionError(StateMachineError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Transition '{current}' → '{target}' is not allowed. "
            f"Valid targets: {STATES.get(current, [])}"
        )
        self.current = current
        self.target = target


class UnknownStateError(StateMachineError):
    pass


def validate_transition(current: str, target: str) -> None:
    """Raise InvalidTransitionError if the transition is not allowed."""
    if current not in STATES:
        raise UnknownStateError(f"Unknown current state: '{current}'")
    if target not in STATES:
        raise UnknownStateError(f"Unknown target state: '{target}'")
    if target not in STATES[current]:
        raise InvalidTransitionError(current, target)


def is_valid_state(state: str) -> bool:
    return state in STATES
