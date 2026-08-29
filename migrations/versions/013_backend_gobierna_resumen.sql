-- Migration 013: el backend gobierna el resumen del pedido (ADR-010)
--
-- Contexto (P15): `user_confirmation` declara un ACTO ("el cliente aceptó su pedido")
-- pero se decidía por un juicio de LENGUAJE sobre un mensaje suelto, sin nada contra
-- qué contrastarlo. Medido sobre el histórico completo (47 conversaciones, 616
-- mensajes): de las 5 veces que se marcó, 4 fueron falsos positivos — 80%. Y ese
-- checkpoint es la única precondición para que el botón del operador registre una
-- venta (confirm_payment.py:78). El 2026-08-19 se materializó: quedó en el perfil de
-- una clienta una compra con product_id NULL y total NULL.
--
-- El hallazgo que decide el diseño: no existía ninguna señal determinista de que el
-- resumen se hubiera enviado. Vivía como texto libre dentro de un response_text que
-- redactó el LLM, así que el backend no sabía que lo había mandado. Esta migración
-- crea ese hecho y retira del prompt las dos responsabilidades que lo impedían.
--
-- Tres secciones, un solo archivo:
--   1. DDL — las dos columnas del estado del resumen (lo único que toca el schema).
--   2. business_rules — reglas de envío y presentación como DATOS.
--   3. system_prompt_template — cirugía quirúrgica, patrón de la 011.
--
-- La sección 3 NO es limpieza de prompt: es parte del fix. Mientras el prompt siga
-- enseñando a redactar resúmenes con total, el LLM los redactará en los turnos en que
-- el backend no renderiza (fingerprint sin cambios) y su texto saldrá sin reemplazar.
-- Se volvería a prometer dinero calculado por el modelo.
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


-- ============================================================
-- 2. Reglas de envío y presentación (datos, no código)
-- ============================================================
-- Reemplazo COMPLETO de shipping_rules. Solo sobreviven las ciudades con una tarifa
-- real y fija. Pereira, Armenia, Bogotá, Cali, Bucaramanga, Barranquilla, Cartagena y
-- Santa Marta pierden su tarifa, y el bloque `zones` entero desaparece: eran cifras de
-- abril (005) que nunca se contrastaron contra un envío real. Sostenerlas es inventar
-- tarifas. Esas ciudades pasan a "por confirmar" y el operador coordina a mano.
--
-- Es un cambio VISIBLE para el cliente: el bot dejará de cotizar esas ciudades.
--
-- El backend lee `cities` por nombre normalizado (sin tildes, sin mayúsculas), así que
-- las tildes de aquí son ortografía de cara al cliente, no clave de búsqueda.

UPDATE clients
SET business_rules = jsonb_set(
    business_rules,
    '{shipping_rules}',
    '{
        "cities": {
            "Manizales": {"cost": 5000, "method": "domicilio"},
            "Medellín": {"cost": 15000, "method": "transportadora"},
            "Envigado": {"cost": 15000, "method": "transportadora"},
            "Sabaneta": {"cost": 15000, "method": "transportadora"}
        },
        "default": "to_confirm",
        "pickup": false,
        "international": "no disponible actualmente"
    }'::jsonb
)
WHERE id = '00000000-0000-0000-0000-000000000001';

-- Presentación: el sustantivo que usa el resumen renderizado por el backend. Vive en
-- datos para que un segundo cliente que venda otra cosa sea una edición, no un parche.
UPDATE clients
SET business_rules = jsonb_set(
    business_rules,
    '{presentation}',
    '{
        "es": "bolsas de 340g",
        "es_singular": "bolsa de 340g",
        "en": "340g bags",
        "en_singular": "340g bag"
    }'::jsonb
)
WHERE id = '00000000-0000-0000-0000-000000000001';


-- ============================================================
-- 3. system_prompt_template (cirugía, idempotente)
-- ============================================================

-- 3a. PESO Y CANTIDAD → regla dura. El bloque viejo (006, reafirmado en 009:142-145)
-- ofrecía calcular equivalencias de libras y kilos, y el modelo lo obedecía a medias
-- (bug del 2026-04-20: el cliente pidió 2 libras, el bot dijo 6 bolsas). Se vende en
-- bolsas de 340g y no se hacen conversiones.
UPDATE clients
   SET system_prompt_template = REPLACE(
           system_prompt_template,
           E'- PESO Y CANTIDAD: Trabajamos en bolsas de 340g. Si el cliente pide en libras o kilos, NO recomiendes cantidad por tu cuenta. Dile que trabajas en bolsas de 340g y pregúntale si quiere que le calcules una equivalencia aproximada. Solo haces la cuenta cuando el cliente explícitamente la pida.\n  Ejemplo MAL hecho (NO): "Para 2 libras te recomendaría 3 bolsas."\n  Ejemplo BIEN hecho: "Nosotros manejamos bolsas de 340g para cuidar la frescura. ¿Quieres que te calcule a cuántas bolsas equivalen 2 libras?"\n  Solo si el cliente responde "sí", haces la conversión y la presentas como aproximada (ej. "2 libras son unos 908g, más o menos 2 bolsas y media; te sirven 2 o 3 bolsas").\n',
           E'- PESO Y CANTIDAD: solo se vende en bolsas de 340g. Las libras, la media libra y los kilos sueltos no existen como presentación. Si el cliente los menciona, dile que se vende en bolsas de 340g y sigue la conversación con naturalidad. NO hagas conversiones ni ofrezcas calcularlas.\n'
       )
 WHERE id = '00000000-0000-0000-0000-000000000001'
   AND system_prompt_template LIKE '%equivalencia aproximada%';

-- 3b. RESUMEN DE CONFIRMACIÓN → fuera. El resumen lo redacta, calcula y envía el
-- backend (ADR-010 §1 y §2). Ojo: aquí vivía la ÚNICA instrucción del sistema que
-- empujaba a conseguir la cantidad ("Y el cliente ya haya indicado cuántas bolsas
-- quiere"). Su relevo es el directive (goal_strategy.py), no otra línea de prompt.
UPDATE clients
   SET system_prompt_template = REGEXP_REPLACE(
           system_prompt_template,
           E'RESUMEN DE CONFIRMACIÓN:.*?FLUJO DE COMPRA',
           E'FLUJO DE COMPRA'
       )
 WHERE id = '00000000-0000-0000-0000-000000000001'
   AND system_prompt_template LIKE '%RESUMEN DE CONFIRMACIÓN%';

-- 3c. FLUJO DE COMPRA paso 3: el resumen ya no lo presenta el LLM.
UPDATE clients
   SET system_prompt_template = REPLACE(
           system_prompt_template,
           E'3. Presentas el resumen con todos los datos.\n',
           E'3. El sistema envía el resumen del pedido automáticamente cuando ya tiene todo. NO lo escribas tú, y nunca menciones precios totales ni valores de envío: esos los calcula el sistema.\n'
       )
 WHERE id = '00000000-0000-0000-0000-000000000001'
   AND system_prompt_template LIKE '%3. Presentas el resumen con todos los datos.%';

-- 3d. Molienda y cantidad son requisito de venta: sin ellos el backend no puede
-- enviar el resumen, y la venta se estancaría en silencio. El directive los pide
-- cuando son lo único que falta; esta línea evita que el prompt lo contradiga.
UPDATE clients
   SET system_prompt_template = REPLACE(
           system_prompt_template,
           E'- `quantity`: número entero de bolsas',
           E'- `quantity` (OBLIGATORIO: sin cantidad el sistema no envía el resumen): número entero de bolsas'
       )
 WHERE id = '00000000-0000-0000-0000-000000000001'
   AND system_prompt_template LIKE '%`quantity`: número entero de bolsas%';

UPDATE clients
   SET system_prompt_template = REPLACE(
           system_prompt_template,
           E'- `grind_preference`: preferencia de molido como texto.',
           E'- `grind_preference` (OBLIGATORIO: es lo que se despacha, y sin ella el sistema no envía el resumen): preferencia de molido como texto.'
       )
 WHERE id = '00000000-0000-0000-0000-000000000001'
   AND system_prompt_template LIKE '%`grind_preference`: preferencia de molido como texto.%';

-- Lo que NO se toca, a propósito: la línea de `user_confirmation` en EXTRACCIÓN DE
-- DATOS. ADR-010 §5 conserva la propuesta del LLM — juzgar si un enunciado es una
-- afirmación ES tarea de lenguaje. Lo que cambia es CUÁNDO el backend puede aceptarla.

-- Verificación (todas deben devolver 0):
--   SELECT count(*) FROM clients WHERE id = '00000000-0000-0000-0000-000000000001'
--     AND (system_prompt_template LIKE '%RESUMEN DE CONFIRMACIÓN%'
--       OR system_prompt_template LIKE '%equivalencia aproximada%'
--       OR system_prompt_template LIKE '%total aprox%');
-- Y registrar la longitud antes/después en el encabezado -- Applied:
--   SELECT length(system_prompt_template) FROM clients
--    WHERE id = '00000000-0000-0000-0000-000000000001';
