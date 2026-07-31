-- Migration 011: quitar `payment_confirmation` de la lista de extracción del prompt
--
-- Contexto (bug de venta duplicada, e2e del 2026-07-20, conversación 9635…bce7):
-- la venta se registró DOS veces en client_users.profile porque existían dos
-- autoridades vivas sobre el mismo hecho. ADR-009 construyó la correcta (el
-- operador revisa el comprobante y pulsa el botón → POST /operator/confirm-payment)
-- pero nunca retiró la vieja: el cliente escribía "ya pagué", el LLM proponía
-- `payment_confirmation` en extracted_data y el backend registraba la venta.
--
-- El backend ya no acepta esa propuesta (agent_action.py: OPERATOR_ONLY_FIELDS).
-- Esta migración cierra el otro extremo: dejar de PEDÍRSELO al LLM. Es la
-- alternativa B que el propio ADR-009 rechazó ("'ya pagué' no es prueba de pago"),
-- todavía escrita en el prompt de producción (venía de 006, reafirmada en 009:217).
--
-- Qué NO toca, a propósito: el comportamiento de cobro sigue igual. Las secciones
-- FLUJO DE COMPRA (pasos 5-6), COMPROBANTE DE PAGO y HANDOFF HUMANO quedan
-- intactas — el bot sigue pidiendo el comprobante y despidiéndose con calidez.
-- Lo único que se retira es la instrucción de EMITIR la clave en extracted_data.
--
-- Edición quirúrgica (no re-escribe el template): borra exactamente la línea de
-- `payment_confirmation` de la sección "EXTRACCIÓN DE DATOS (extracted_data)".
-- Idempotente: el guard LIKE hace que una re-ejecución sea no-op.
--
-- Aplicar en n8n el cambio gemelo (nodo "Build LLM Prompt" del workflow
-- cafe_arenillo_v2: quitar payment_confirmation de "Valid extracted_data keys").
--
-- No schema change.
--
-- Applied:

UPDATE clients
   SET system_prompt_template = REPLACE(
           system_prompt_template,
           E'- `payment_confirmation`: true cuando el cliente envíe el comprobante de pago.\n',
           ''
       )
 WHERE id = '00000000-0000-0000-0000-000000000001'
   AND system_prompt_template LIKE '%`payment_confirmation`%';

-- Verificación (debe devolver 0):
--   SELECT count(*) FROM clients
--    WHERE id = '00000000-0000-0000-0000-000000000001'
--      AND system_prompt_template LIKE '%payment_confirmation%';
