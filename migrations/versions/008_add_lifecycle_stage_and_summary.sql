-- Migration 008: CRM lifecycle stage + extended profile shape
--
-- Goal: enable the "vendor with memory" model. Two changes:
--
--   1. Add client_users.lifecycle_stage so we can filter/segment customers
--      ("engaged but didn't buy", "customers", "dormant") with a simple
--      indexed query instead of scanning JSONB.
--
--   2. Document the extended profile JSONB shape that conversation_summary.py
--      will start populating: language, communication_style, last_conversation_summary,
--      and richer purchase records. No DDL needed for those — JSONB is flexible
--      — but the comment is the contract.
--
-- Backfill: existing client_users with at least one recorded purchase are
-- marked as 'customer'; everyone else stays at the default 'new'.
--
-- Applied: 2026-04-30

-- ---------------------------------------------------------------------------
-- 1. lifecycle_stage column + check + index
-- ---------------------------------------------------------------------------
ALTER TABLE client_users
  ADD COLUMN IF NOT EXISTS lifecycle_stage VARCHAR(20) NOT NULL DEFAULT 'new';

ALTER TABLE client_users DROP CONSTRAINT IF EXISTS ck_client_users_lifecycle_stage;
ALTER TABLE client_users ADD CONSTRAINT ck_client_users_lifecycle_stage
  CHECK (lifecycle_stage IN ('new', 'engaged', 'customer', 'dormant'));

CREATE INDEX IF NOT EXISTS ix_client_users_client_lifecycle
  ON client_users (client_id, lifecycle_stage);

COMMENT ON COLUMN client_users.lifecycle_stage IS
  'CRM segmentation. Transitions:
     new      -> first row created, no engagement yet
     engaged  -> agent_action accepted at least one strategy slot (lead in motion)
     customer -> at least one payment_confirmed purchase (auto-escalated)
     dormant  -> set by future maintenance job after long inactivity';

-- ---------------------------------------------------------------------------
-- 2. Backfill from existing purchase history
-- ---------------------------------------------------------------------------
UPDATE client_users
   SET lifecycle_stage = 'customer'
 WHERE COALESCE((profile->>'purchase_count')::int, 0) > 0
   AND lifecycle_stage = 'new';

-- ---------------------------------------------------------------------------
-- 3. Updated profile shape comment (the contract for what may live here)
-- ---------------------------------------------------------------------------
COMMENT ON COLUMN client_users.profile IS
  'Persistent customer profile across conversations. Shape:
   {
     first_name, full_name, email, phone, city, shipping_address,
     preferences: { grind, roast },
     language: "es" | "en",
     communication_style: "formal" | "casual" | "direct",
     last_conversation_summary: {
       conversation_id, summarized_at, outcome, summary,
       interest_level, products_discussed, objections, pending_intent
     },
     purchase_count,
     purchases: [{ date, product_id, quantity, total, conversation_id }]
   }
   Updated by agent_action.py (stable facts) and conversation_summary.py (memory).';
