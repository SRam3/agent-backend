"""GoalStrategyEngine — deterministic checkpoint DAG navigator.

Given a goal and the data collected so far, computes what's missing,
what's blocked, and what the optimal next move is.

Pure Python — no I/O, no LLM calls. Runs in microseconds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Checkpoint status
# ---------------------------------------------------------------------------
COMPLETE = "complete"
BLOCKED = "blocked"
IN_PROGRESS = "in_progress"
PENDING = "pending"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Checkpoint:
    name: str
    required_fields: list[str]
    blocked_by: list[str] = field(default_factory=list)
    # human-readable label for prompt formatting
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.name.replace("_", " ").title()


@dataclass
class StrategyDirective:
    goal: str
    progress_pct: int
    current_checkpoint: str
    current_checkpoint_label: str
    next_action: str
    missing_fields: list[str]
    completed_checkpoints: list[str]
    all_complete: bool

    def to_prompt(self) -> str:
        """Format the directive as a soft hint for the LLM system prompt.

        The directive suggests what info to collect next, but never overrides
        the customer's current question.  The agent must answer the customer
        first, then steer toward the goal only when the moment feels natural.
        """
        bar_filled = int(self.progress_pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)

        lines = [
            f"SALES PROGRESS: [{bar}] {self.progress_pct}%",
        ]

        if self.all_complete:
            lines.append(
                "All purchase info is collected. If natural, confirm with the customer and close politely.",
            )
        else:
            lines.append(f"NEXT INFO NEEDED: {', '.join(self.missing_fields)}")
            lines.append(
                f"HINT (low priority, only when the conversation flows there naturally): {self.next_action.lower()}",
            )

        lines += [
            "IMPORTANT: Your priority is to have a warm, natural conversation. Answer what the customer asks.",
            "Do NOT mention or push toward the next step unless the customer brings it up.",
            "Never end a message with 'puedo ayudarte con el pedido' or similar sales push.",
        ]

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Goal definitions
# ---------------------------------------------------------------------------
def _build_close_sale_checkpoints(business_rules: dict) -> list[Checkpoint]:
    """Build the close_sale DAG, applying client-specific overrides.

    Intent is implicit: if the customer reaches product_matched, they expressed
    buying interest. No separate intent_identified checkpoint.
    """
    checkpoints = [
        Checkpoint(
            name="product_matched",
            label="Product matched",
            required_fields=["product_id"],
            blocked_by=[],
        ),
    ]

    # lead_qualified checkpoint — may be skipped by business rule
    if not business_rules.get("skip_lead_qualification", False):
        lead_fields = ["full_name", "phone"]
        if business_rules.get("require_id_number", False):
            lead_fields.append("identification_number")
        if business_rules.get("require_email", False):
            lead_fields.append("email")
        checkpoints.append(
            Checkpoint(
                name="lead_qualified",
                label="Lead qualified",
                required_fields=lead_fields,
                blocked_by=[],
            )
        )

    shipping_blocked_by = ["lead_qualified"] if not business_rules.get("skip_lead_qualification", False) else []
    checkpoints.append(
        Checkpoint(
            name="shipping_info_collected",
            label="Shipping info collected",
            required_fields=["shipping_address", "shipping_city"],
            blocked_by=shipping_blocked_by,
        )
    )

    checkpoints.append(
        Checkpoint(
            name="user_confirmed",
            label="User confirmed",
            required_fields=["user_confirmation"],
            blocked_by=["product_matched", "shipping_info_collected"],
        )
    )

    checkpoints.append(
        Checkpoint(
            name="payment_confirmed",
            label="Payment confirmed",
            required_fields=["payment_confirmation"],
            blocked_by=["user_confirmed"],
        )
    )

    return checkpoints


GOAL_BUILDERS: dict[str, callable] = {
    "close_sale": _build_close_sale_checkpoints,
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class GoalStrategyEngine:
    """Deterministic DAG navigator.

    Usage:
        engine = GoalStrategyEngine()
        directive = engine.compute("close_sale", collected_data, business_rules)
        prompt_text = directive.to_prompt()
    """

    def compute(
        self,
        goal: str,
        collected_data: dict,
        business_rules: Optional[dict] = None,
    ) -> StrategyDirective:
        if business_rules is None:
            business_rules = {}

        builder = GOAL_BUILDERS.get(goal)
        if builder is None:
            # Unknown goal — return a generic "keep going" directive
            return StrategyDirective(
                goal=goal,
                progress_pct=0,
                current_checkpoint="unknown",
                current_checkpoint_label="Unknown goal",
                next_action="Continue the conversation naturally.",
                missing_fields=[],
                completed_checkpoints=[],
                all_complete=False,
            )

        checkpoints = builder(business_rules)
        statuses = self._evaluate(checkpoints, collected_data)

        completed = [cp.name for cp in checkpoints if statuses[cp.name] == COMPLETE]
        total = len(checkpoints)
        progress_pct = int(len(completed) / total * 100) if total else 100

        all_complete = len(completed) == total

        if all_complete:
            return StrategyDirective(
                goal=goal,
                progress_pct=100,
                current_checkpoint="all_complete",
                current_checkpoint_label="All steps complete",
                next_action="The sale is complete. Confirm with the customer and close politely.",
                missing_fields=[],
                completed_checkpoints=completed,
                all_complete=True,
            )

        # Find first actionable (non-blocked, non-complete) checkpoint
        actionable = next(
            (cp for cp in checkpoints if statuses[cp.name] in (IN_PROGRESS, PENDING)),
            None,
        )

        if actionable is None:
            # All remaining checkpoints are blocked — shouldn't normally happen
            actionable = next(cp for cp in checkpoints if statuses[cp.name] != COMPLETE)

        missing_fields = [
            f for f in actionable.required_fields
            if not collected_data.get(f)
        ]

        next_action = self._action_text(actionable, missing_fields)

        return StrategyDirective(
            goal=goal,
            progress_pct=progress_pct,
            current_checkpoint=actionable.name,
            current_checkpoint_label=actionable.label,
            next_action=next_action,
            missing_fields=missing_fields,
            completed_checkpoints=completed,
            all_complete=False,
        )

    # ------------------------------------------------------------------
    def _evaluate(
        self, checkpoints: list[Checkpoint], collected_data: dict
    ) -> dict[str, str]:
        statuses: dict[str, str] = {}

        for cp in checkpoints:
            # Check if all required fields are present
            fields_present = [
                bool(collected_data.get(f)) for f in cp.required_fields
            ]
            all_present = all(fields_present)
            any_present = any(fields_present)

            if all_present:
                statuses[cp.name] = COMPLETE
                continue

            # Check if any dependency is not yet complete
            blocked = any(
                statuses.get(dep) != COMPLETE for dep in cp.blocked_by
            )
            if blocked:
                statuses[cp.name] = BLOCKED
            elif any_present:
                statuses[cp.name] = IN_PROGRESS
            else:
                statuses[cp.name] = PENDING

        return statuses

    def _action_text(self, checkpoint: Checkpoint, missing_fields: list[str]) -> str:
        if not missing_fields:
            return f"Complete the '{checkpoint.label}' step."

        field = missing_fields[0]
        prompts = {
            "product_id": "Help the customer choose a product from the catalog.",
            "full_name": "Try to learn the customer's name.",
            "identification_number": "Ask for the customer's ID number.",
            "email": "Ask for the customer's email.",
            "phone": "Try to learn the customer's phone number for the carrier.",
            "shipping_address": "Try to learn the full delivery address (neighborhood, street, number, apartment).",
            "shipping_city": "Try to learn the customer's city.",
            "user_confirmation": "Present an order summary with all the collected data and ask the customer to confirm.",
            "payment_confirmation": "Share payment methods and ask the customer to send payment receipt once paid.",
        }
        return prompts.get(field, f"Ask for the customer's {field.replace('_', ' ')}.")
