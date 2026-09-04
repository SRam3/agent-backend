-- Migration 013: estado del resumen del pedido (ADR-010 — parte 1 de 2, solo DDL)
--
-- Orden: ANTES del despliegue
-- Por qué: el ORM de ADR-010 declara estas dos columnas (sales_agent_api/app/models/core.py).
--          Si el código se despliega sin ellas, cada select(Conversation) falla, el 100% de
--          los turnos devuelve 500 y n8n lo traga en silencio por el continueOnFail de
--          "POST Ingest Message" — todas las ejecuciones quedarían marcadas success
--          (deuda #12). Regla general: ADR-012.
--
-- Esta migración era una sola (013) con tres secciones cuyas restricciones de orden son
-- OPUESTAS: el DDL debe ir antes del despliegue y la cirugía del prompt después. Se partió
-- el 2026-09-03 en 013 (DDL, esta) y 014 (datos y prompt). Ver P33 y ADR-012.
--
-- Contexto (P15): `user_confirmation` declara un ACTO ("el cliente aceptó su pedido") pero se
-- decidía por un juicio de LENGUAJE sobre un mensaje suelto, sin nada contra qué contrastarlo.
-- Medido sobre el histórico completo: de las 5 veces que se marcó, 4 fueron falsos positivos.
-- Y ese checkpoint es la única precondición para que el botón del operador registre una venta
-- (confirm_payment.py). El 2026-08-19 se materializó: quedó en el perfil de una clienta una
-- compra con product_id NULL y total NULL.
--
-- El hallazgo que decide el diseño: no existía ninguna señal determinista de que el resumen se
-- hubiera enviado. Vivía como texto libre dentro de un response_text que redactó el LLM, así
-- que el backend no sabía que lo había mandado. Estas dos columnas crean ese hecho.
--
-- Aditiva y nullable: es segura de aplicar con el código VIEJO desplegado. Esa es justamente
-- la propiedad que permite aplicarla antes del merge.
--
-- Applied:

-- ============================================================
-- 1. Estado del resumen (DDL)
-- ============================================================
-- Columnas propias, NO dentro de extracted_context: son hechos PROPIEDAD del backend,
-- no slots propuestos por el LLM. Mezclarlos donde se mergean las propuestas del
-- modelo recrea el riesgo que obligó a inventar OPERATOR_ONLY_FIELDS. Además hace el
-- fingerprint consultable.

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS order_summary_fingerprint VARCHAR(64);

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS order_summary_sent_at TIMESTAMPTZ;

COMMENT ON COLUMN conversations.order_summary_fingerprint IS
    'sha256 del pedido tal como se presentó (7 campos + envío aplicado). ADR-010.';
COMMENT ON COLUMN conversations.order_summary_sent_at IS
    'Cuándo el backend envió ese resumen. Reloj del backend. ADR-010.';

-- Verificación (debe devolver 2):
--   SELECT count(*) FROM information_schema.columns
--    WHERE table_name = 'conversations'
--      AND column_name IN ('order_summary_fingerprint', 'order_summary_sent_at');
