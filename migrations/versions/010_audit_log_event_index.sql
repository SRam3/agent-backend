-- Migration 010: Index for audit_log health queries
--
-- The /api/v1/internal/integration-health endpoint queries audit_log for
-- the most recent n8n_ping event ("when did we last hear from n8n?"). The
-- table also receives a heartbeat per minute, so it grows steadily — without
-- an index, the lookup degrades from O(1) to O(N) over time.
--
-- This index supports both the n8n-ping freshness query and any future
-- "events of type X in the last N minutes" query without scanning the
-- whole table.
--
-- Applied: 2026-05-03

CREATE INDEX IF NOT EXISTS ix_audit_log_event_created
  ON audit_log (event_type, created_at DESC);

COMMENT ON INDEX ix_audit_log_event_created IS
  'Supports observability queries on audit_log: "last event of type X" and
   "events of type X since T". Critical for /api/v1/internal/integration-health
   which polls the latest n8n_ping timestamp on every check.';
