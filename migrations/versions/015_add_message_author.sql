-- Migration 015: quién escribió cada mensaje (ADR-013 — P29 fase 1)
--
-- Orden: ANTES del despliegue
-- Por qué: el ORM de ADR-013 declara esta columna (sales_agent_api/app/models/core.py). Si el
--          código se despliega sin ella, cada select(Message) falla y con él el ingest entero:
--          el 100% de los turnos devuelve 500 y n8n lo traga en silencio por el continueOnFail
--          de "POST Ingest Message" — todas las ejecuciones quedarían marcadas success
--          (deuda #12). Regla general: ADR-012.
--
-- Contexto (P29, deuda #14): el sistema conoce la mitad de sus conversaciones. Cuando el
-- operador humano escribe al cliente desde el número del negocio, Meta entrega un webhook con
-- `changes[0].field = "smb_message_echoes"`, y ese payload muere en dos nodos de n8n que solo
-- saben leer `messages[]`. Al menos 17 veces en la retención de n8n, que solo guarda unos días.
-- El 2026-08-19 el operador prometió una entrega para "pasado mañana" y el sistema nunca supo
-- que esa promesa existió; ese mismo día el bot inventó una "llave 1234" entre la promesa del
-- operador de compartir la llave y la llave real, porque razonaba sobre un diálogo incompleto.
--
-- Por qué una columna de AUTOR y no un valor nuevo de `direction`: desde el negocio, un echo
-- salió, así que `direction` sigue siendo 'outbound' y es correcto. Lo que faltaba era QUIÉN
-- escribió. `direction = 'operator'` habría roto `ck_message_direction` y toda query que hoy
-- cuenta outbound — incluido el circuit breaker de P8, que dejaría de ver los mensajes del bot.
--
-- VARCHAR + CHECK y no ENUM nativo, por ADR-006.
--
-- Aditiva y nullable: es segura de aplicar con el código VIEJO desplegado, que simplemente la
-- ignora. Esa es la propiedad que permite aplicarla antes del merge.
--
-- SIN ÍNDICE NUEVO, y es decisión explícita, no omisión (§8 pregunta 2 del brief). La consulta
-- de la pausa filtra por (conversation_id, author, created_at DESC), e `ix_messages_conversation_id`
-- (migración 001) ya la acota a una sola conversación. Con 673 filas en la tabla, el filtro
-- restante sobre author y created_at es ruido. Se revisa cuando `messages` crezca un orden de
-- magnitud.
--
-- Applied: PENDIENTE

-- ============================================================
-- 1. La columna (DDL)
-- ============================================================

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS author VARCHAR(20);

-- El CHECK acepta NULL a propósito: la columna es nullable y el backfill de la sección 2 no
-- puede alcanzar filas que se inserten entre el ALTER y el UPDATE dentro de esta transacción.
-- NOT VALID no hace falta: la tabla tiene 673 filas y el backfill corre en la misma sesión.
ALTER TABLE messages
    DROP CONSTRAINT IF EXISTS ck_message_author;

ALTER TABLE messages
    ADD CONSTRAINT ck_message_author
    CHECK (author IS NULL OR author IN ('bot', 'operator', 'customer'));

COMMENT ON COLUMN messages.author IS
    'Quién escribió el mensaje: bot | operator | customer. Ortogonal a direction — un echo del '
    'operador es outbound y author=operator. Los echoes son contexto, nunca hecho: ningún '
    'checkpoint, slot ni transición puede originarse en uno. ADR-013.';

-- ============================================================
-- 2. Backfill
-- ============================================================
-- Todo lo que existe hoy es del cliente o del bot: hasta esta migración no había forma de que
-- un mensaje del operador entrara a la tabla. 673 filas al 2026-09-04 (359 inbound, 314
-- outbound), así que un UPDATE completo es trivial.

UPDATE messages SET author = 'customer' WHERE direction = 'inbound'  AND author IS NULL;
UPDATE messages SET author = 'bot'      WHERE direction = 'outbound' AND author IS NULL;

-- Verificación 1 — la columna existe y es nullable (debe devolver 1 fila, is_nullable = YES):
--   SELECT column_name, data_type, is_nullable
--     FROM information_schema.columns
--    WHERE table_name = 'messages' AND column_name = 'author';
--
-- Verificación 2 — no queda ninguna fila sin autor (debe devolver 0):
--   SELECT count(*) FROM messages WHERE author IS NULL;
--
-- Verificación 3 — el backfill respeta la dirección (debe devolver 0):
--   SELECT count(*) FROM messages
--    WHERE (direction = 'inbound'  AND author <> 'customer')
--       OR (direction = 'outbound' AND author <> 'bot');
--
-- Verificación 4 — el CHECK rechaza un valor inventado (debe fallar):
--   INSERT INTO messages (conversation_id, client_id, direction, author)
--   VALUES (gen_random_uuid(), gen_random_uuid(), 'outbound', 'supervisor');
