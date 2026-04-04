-- ============================================================
-- Migration 002: Peer-review hardening
-- Adds: strategy tracking on conversations, decision explainability
--       on messages, and ensures updated_at trigger coverage.
-- ============================================================

-- ============================================================
-- conversations: strategy tracking columns
-- ============================================================
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS active_goal          VARCHAR(50),
    ADD COLUMN IF NOT EXISTS current_checkpoint   VARCHAR(100),
    ADD COLUMN IF NOT EXISTS progress_pct         INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS strategy_version     INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_strategy_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS strategy_snapshot    JSONB;

CREATE INDEX IF NOT EXISTS ix_conversations_goal
    ON conversations (client_id, active_goal);

COMMENT ON COLUMN conversations.strategy_version IS
    'Incremented on every ingest. Must be echoed back in agent/action to detect stale context.';

COMMENT ON COLUMN conversations.strategy_snapshot IS
    'Snapshot of last GoalStrategyEngine result for debugging.';


-- ============================================================
-- messages: decision explainability columns
-- ============================================================
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS proposed_action_payload   JSONB,
    ADD COLUMN IF NOT EXISTS extracted_data            JSONB,
    ADD COLUMN IF NOT EXISTS backend_decision_reason   TEXT;

COMMENT ON COLUMN messages.proposed_action_payload IS
    'Full payload the agent sent for the proposed action (for audit).';

COMMENT ON COLUMN messages.extracted_data IS
    'Data extracted by the agent from the conversation (for strategy updates).';

COMMENT ON COLUMN messages.backend_decision_reason IS
    'Human-readable reason for the backend decision (approved/rejected/pass-through).';
