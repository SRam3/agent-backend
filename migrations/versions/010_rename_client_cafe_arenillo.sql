-- Migration 010: Rename client 'Café Demo' → 'Café Arenillo'
--
-- The initial seed (001_initial_schema.sql:303) set clients.name = 'Café Demo'.
-- Migration 003 renamed the product to 'Café Arenillo' and wrote the
-- system_prompt with the real business name, but clients.name was never
-- updated — leaving an internal label out of sync with the rest of the system
-- (product catalog, system_prompt, docs all say "Café Arenillo").
--
-- This normalizes clients.name. The slug ('cafe-demo') is intentionally LEFT
-- UNCHANGED: it is a stable identifier that may be referenced by routing or
-- external lookups, and renaming it carries breakage risk that a display-name
-- fix does not warrant.
--
-- No schema change. Idempotent: the WHERE guard makes a re-run a no-op.
--
-- Applied: 2026-06-16 (prod, manually via psql)

UPDATE clients
   SET name = 'Café Arenillo'
 WHERE id = '00000000-0000-0000-0000-000000000001'
   AND name = 'Café Demo';
