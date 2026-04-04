"""Conversation state machine.

Defines valid states, transitions, and actions per state.
Pure Python — no database queries, no LLM calls.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# State definitions
# ---------------------------------------------------------------------------
STATES: dict[str, dict] = {
    "idle": {
        "transitions": ["active", "closed"],
        "actions": ["greet", "classify_intent"],
    },
    "active": {
        "transitions": ["qualifying", "selling", "human_handoff", "idle", "closed"],
        "actions": ["classify_intent", "ask_question", "search_products", "escalate"],
    },
    "qualifying": {
        "transitions": ["selling", "active", "human_handoff", "closed"],
        "actions": ["ask_question", "create_lead", "update_lead_data", "escalate"],
    },
    "selling": {
        "transitions": ["ordering", "qualifying", "active", "human_handoff", "closed"],
        "actions": [
            "search_products",
            "present_product",
            "propose_order",
            "ask_question",
            "escalate",
        ],
    },
    "ordering": {
        "transitions": ["selling", "human_handoff", "closed"],
        "actions": [
            "collect_shipping_info",
            "confirm_order",
            "modify_order",
            "cancel_order",
            "escalate",
        ],
    },
    "human_handoff": {
        "transitions": ["active", "closed"],
        "actions": ["notify_human"],
    },
    "closed": {
        "transitions": [],
        "actions": [],
    },
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class StateMachineError(Exception):
    pass


class InvalidTransitionError(StateMachineError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Transition '{current}' → '{target}' is not allowed. "
            f"Valid targets: {STATES.get(current, {}).get('transitions', [])}"
        )
        self.current = current
        self.target = target


class InvalidActionError(StateMachineError):
    def __init__(self, state: str, action: str) -> None:
        super().__init__(
            f"Action '{action}' is not allowed in state '{state}'. "
            f"Valid actions: {STATES.get(state, {}).get('actions', [])}"
        )
        self.state = state
        self.action = action


class UnknownStateError(StateMachineError):
    pass


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
def validate_transition(current: str, target: str) -> None:
    """Raise InvalidTransitionError if the transition is not allowed."""
    if current not in STATES:
        raise UnknownStateError(f"Unknown current state: '{current}'")
    if target not in STATES:
        raise UnknownStateError(f"Unknown target state: '{target}'")
    if target not in STATES[current]["transitions"]:
        raise InvalidTransitionError(current, target)


def validate_action(state: str, action: str) -> None:
    """Raise InvalidActionError if the action is not allowed in the given state."""
    if state not in STATES:
        raise UnknownStateError(f"Unknown state: '{state}'")
    if action not in STATES[state]["actions"]:
        raise InvalidActionError(state, action)


def get_available_actions(state: str) -> list[str]:
    """Return the list of actions available in the given state."""
    if state not in STATES:
        raise UnknownStateError(f"Unknown state: '{state}'")
    return list(STATES[state]["actions"])


def is_valid_state(state: str) -> bool:
    return state in STATES
