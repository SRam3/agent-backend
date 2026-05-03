-- Migration 011: Telegram operator list for Café Arenillo
--
-- Adds a `operators` block to clients.business_rules so the backend can
-- look up who to notify when a conversation auto-escalates to human_handoff.
--
-- Shape:
--   business_rules.operators.telegram_chat_ids: ["...", "..."]
--
-- IMPORTANT — Telegram chat_id ≠ phone number:
--   Telegram identifies users by a numeric chat_id assigned by Telegram,
--   NOT by their phone number. The bot can only message a user who has
--   sent /start to it at least once. To find a chat_id:
--     1. The operator opens Telegram, searches for the bot, sends /start
--     2. Either: ask @userinfobot for their numeric ID, OR check the
--        Telegram bot's incoming /start update (chat.id field)
--   Once we have the bot wired up and the operator runs /start, replace
--   the placeholder below with the real chat_id and re-apply.
--
-- For now we seed Sebastian's phone number as a placeholder so the
-- structure is in place; calls to Telegram with this value will return
-- "chat not found" until it's swapped for the real chat_id. The notifier
-- handles this gracefully (logs warning + side_effect, doesn't crash).
--
-- Applied: 2026-05-03

UPDATE clients
   SET business_rules = jsonb_set(
         business_rules,
         '{operators}'::text[],
         '{"telegram_chat_ids": ["3107148477"]}'::jsonb,
         true
       )
 WHERE id = '00000000-0000-0000-0000-000000000001';
