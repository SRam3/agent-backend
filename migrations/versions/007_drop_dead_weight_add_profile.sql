-- Migration 007: Drop dead weight + add persistent customer profile
--
-- Goal: every remaining column has a real use. The customer profile now
-- persists across conversations instead of getting trapped in the per-
-- conversation extracted_context.
--
-- Three things happen:
--   1. DROP 3 dormant tables (leads, orders, order_line_items — always empty,
--      no code path populates them after the 2026-04-19 refactor).
--   2. DROP columns that are always NULL across all rows (audited via
--      SELECT count(*) FILTER WHERE col IS NOT NULL per column, all returned
--      0/N).
--   3. ADD client_users.profile JSONB — the single place where persistent
--      customer facts live: {first_name, full_name, email, city, address,
--      preferences: {grind, roast}, purchase_count, purchases: [...]}.
--   4. Tighten conversations.state CHECK to the 3 states actually used.
--
-- Applied: 2026-04-21

-- ---------------------------------------------------------------------------
-- 1. Dormant tables
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS order_line_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS leads CASCADE;

-- ---------------------------------------------------------------------------
-- 2. conversations: drop dead columns + tighten state CHECK
-- ---------------------------------------------------------------------------
ALTER TABLE conversations
  DROP COLUMN IF EXISTS previous_state,
  DROP COLUMN IF EXISTS lead_id,
  DROP COLUMN IF EXISTS order_id,
  DROP COLUMN IF EXISTS assigned_operator_id,
  DROP COLUMN IF EXISTS escalation_reason,
  DROP COLUMN IF EXISTS closed_at,
  DROP COLUMN IF EXISTS agent_turn_count;

ALTER TABLE conversations DROP CONSTRAINT IF EXISTS ck_conversation_state;
ALTER TABLE conversations ADD CONSTRAINT ck_conversation_state
  CHECK (state IN ('active', 'human_handoff', 'closed'));

-- ---------------------------------------------------------------------------
-- 3. messages: drop dead columns (idempotency handled by chakra_message_id UNIQUE)
-- ---------------------------------------------------------------------------
ALTER TABLE messages
  DROP COLUMN IF EXISTS media_url,
  DROP COLUMN IF EXISTS media_mime_type,
  DROP COLUMN IF EXISTS delivery_status,
  DROP COLUMN IF EXISTS delivered_at,
  DROP COLUMN IF EXISTS read_at,
  DROP COLUMN IF EXISTS proposed_action,
  DROP COLUMN IF EXISTS proposed_action_payload,
  DROP COLUMN IF EXISTS action_approved,
  DROP COLUMN IF EXISTS backend_decision_reason,
  DROP COLUMN IF EXISTS idempotency_key;

-- ---------------------------------------------------------------------------
-- 4. clients: drop dead columns
-- ---------------------------------------------------------------------------
ALTER TABLE clients
  DROP COLUMN IF EXISTS chakra_phone_number_id,
  DROP COLUMN IF EXISTS chakra_secret_ref,
  DROP COLUMN IF EXISTS message_retention_days,
  DROP COLUMN IF EXISTS max_tool_calls_per_turn;

-- ---------------------------------------------------------------------------
-- 5. client_users: drop mirror columns that were never populated,
--    add single source-of-truth `profile` JSONB.
-- ---------------------------------------------------------------------------
ALTER TABLE client_users
  DROP COLUMN IF EXISTS whatsapp_id,
  DROP COLUMN IF EXISTS email,
  DROP COLUMN IF EXISTS full_name,
  DROP COLUMN IF EXISTS address,
  DROP COLUMN IF EXISTS city,
  DROP COLUMN IF EXISTS identification_number,
  DROP COLUMN IF EXISTS metadata;

ALTER TABLE client_users
  ADD COLUMN IF NOT EXISTS profile JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN client_users.profile IS
  'Persistent customer profile across conversations. Shape: '
  '{first_name, full_name, email, city, shipping_address, '
  'preferences: {grind, roast}, purchase_count, purchases: [{date, product_id, quantity, total}], '
  'last_conversation_summary}. '
  'Merged from conversations.extracted_context at agent turn close and on payment_confirmed.';

-- ---------------------------------------------------------------------------
-- 6. products: drop empty tags
-- ---------------------------------------------------------------------------
ALTER TABLE products DROP COLUMN IF EXISTS tags;

-- ---------------------------------------------------------------------------
-- 7. audit_log: drop columns never populated
-- ---------------------------------------------------------------------------
ALTER TABLE audit_log
  DROP COLUMN IF EXISTS actor_id,
  DROP COLUMN IF EXISTS old_value,
  DROP COLUMN IF EXISTS metadata;
