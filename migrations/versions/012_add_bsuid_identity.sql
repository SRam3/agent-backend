-- Migration 012: identidad por BSUID (privacidad de número de WhatsApp)
--
-- Contexto (P14 — mensaje perdido del 2026-08-04, ejecución n8n 9459):
-- WhatsApp desplegó privacidad de número. Cuando un cliente la activa, Meta deja
-- de mandar `from` y `wa_id` en el webhook y lo identifica SOLO con su BSUID
-- (Business-Scoped User ID, formato `CO.1034312865991667`). Una clienta real
-- escribió "Hola" y el sistema la descartó en silencio: el teléfono era la única
-- identidad que sabía leer, y no venía. Diagnóstico completo: el payload es
-- pass-through verbatim de Meta y `from_user_id`/`user_id` YA llegan en todos los
-- mensajes — el problema era de parsing y de schema, no de transporte.
--
-- Esta migración abre el schema para que la identidad NO sea el teléfono:
--
--   1. client_users.bsuid — la identidad de WhatsApp, en columna propia. No se
--      mete el BSUID en phone_number: son cosas distintas (uno es identidad, el
--      otro un dato de contacto opcional) y meterlos en la misma columna miente
--      sobre lo que guarda. Además phone_number es VARCHAR(20), corto para
--      BSUIDs largos.
--
--   2. Unicidad (client_id, bsuid). Confirmado por Chakra Support: Meta emite un
--      BSUID DISTINTO por cada WABA, así que el mismo cliente físico tiene un
--      BSUID por tenant. El BSUID significa "esta persona ante ESTE negocio",
--      nunca "esta persona". Por eso la unicidad es por tenant y jamás global —
--      Meta lo diseñó así por privacidad, no es una limitación a sortear.
--
--   3. phone_number pasa a NULLABLE. Un cliente con privacidad activada es un
--      cliente perfectamente válido del que no conocemos el teléfono.
--
-- Qué NO toca, a propósito: el constraint uq_client_user_phone (client_id,
-- phone_number) queda VIVO. Dos razones:
--   (a) El código en producción lo referencia POR NOMBRE en su upsert
--       (services/ingest.py — on_conflict_do_update(constraint="uq_client_user_phone")),
--       así que dropearlo haría fallar cada ingest hasta desplegar la Fase 2.
--       Dejarlo hace que esta migración sea retrocompatible: se puede aplicar con
--       el código actual desplegado, sin ventana de rotura y sin acoplar el orden
--       migración↔deploy.
--   (b) Sigue siendo útil. Con phone_number nullable los NULL no colisionan entre
--       sí en un UNIQUE, así que el constraint conserva su invariante (un registro
--       por tenant y teléfono) exactamente entre las filas que SÍ tienen teléfono.
-- Lo único que podría querer retirarlo es la fusión phone↔BSUID, que está fuera
-- de alcance a propósito y va en su propio ADR (identidad primaria por BSUID).
--
-- Applied: 2026-08-17 (prod, manualmente vía psql, transacción única con
--          verificación: bsuid VARCHAR(140) nullable creado, phone_number pasó
--          a nullable, índice uq_client_users_client_bsuid creado sobre
--          (client_id, bsuid), y uq_client_user_phone INTACTO — confirmando que
--          el upsert del código entonces desplegado seguía resolviendo por
--          nombre de constraint. 0 de 33 filas con bsuid, como se esperaba:
--          todos los client_users existentes son pre-P14.)

-- ---------------------------------------------------------------------------
-- 1. Columna bsuid
-- ---------------------------------------------------------------------------
ALTER TABLE client_users
  ADD COLUMN IF NOT EXISTS bsuid VARCHAR(140);

COMMENT ON COLUMN client_users.bsuid IS
  'Business-Scoped User ID de WhatsApp: country code + "." + identificador '
  '(ej. CO.1034312865991667). Identidad del cliente ante ESTE tenant — Meta emite '
  'un BSUID distinto por WABA, por eso la unicidad es (client_id, bsuid) y nunca '
  'global. NULL en las filas anteriores a P14 y en clientes vistos solo por '
  'teléfono. VARCHAR(140) da margen sobre el máximo de Meta (128 + prefijo); los '
  'observados en producción miden 19.';

-- ---------------------------------------------------------------------------
-- 2. Unicidad del BSUID por tenant
-- ---------------------------------------------------------------------------
-- Índice único COMPLETO (sin WHERE) a propósito. Postgres trata cada NULL como
-- distinto, así que las filas sin BSUID no colisionan y no hace falta un índice
-- parcial. Se evita el parcial deliberadamente: obligaría al ON CONFLICT de la
-- Fase 2 a repetir el predicado (index_where) para que Postgres pueda inferir el
-- índice. Con el índice completo el upsert infiere con (client_id, bsuid) a secas.
--
-- Sirve además como el índice de lectura de la resolución de identidad
-- (SELECT ... WHERE client_id = :c AND bsuid = :b), así que no se agrega otro.
CREATE UNIQUE INDEX IF NOT EXISTS uq_client_users_client_bsuid
  ON client_users (client_id, bsuid);

-- ---------------------------------------------------------------------------
-- 3. phone_number deja de ser obligatorio
-- ---------------------------------------------------------------------------
-- La identidad ya no es el teléfono. Nótese que el modelo ORM sigue declarando
-- nullable=False hasta la Fase 2; es inofensivo — SQLAlchemy usa `nullable` para
-- generar DDL, no para validar en runtime.
ALTER TABLE client_users
  ALTER COLUMN phone_number DROP NOT NULL;
