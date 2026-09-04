-- Migration 014: reglas de envío y cirugía del prompt (ADR-010 — parte 2 de 2)
--
-- Orden: DESPUÉS del despliegue
-- Por qué: la sección 2 retira del system_prompt_template la instrucción de redactar el
--          resumen del pedido. Si entra ANTES de que el código lo renderice, no lo manda
--          nadie: ni el LLM (ya no sabe que debe) ni el backend (aún no está desplegado).
--          La venta se estancaría en silencio justo en el paso de confirmación.
--          Regla general: ADR-012.
--
-- Segunda mitad de lo que era la migración 013, partida el 2026-09-03 (ver P33 y ADR-012).
-- La primera mitad (013) es el DDL y va antes del despliegue.
--
-- La sección 1 (reglas de envío) es indiferente al orden y viaja aquí para no partir el
-- cambio en tres archivos.
--
-- ⚠️ CAMBIO VISIBLE PARA EL CLIENTE, CONFIRMADO POR EL NEGOCIO EL 2026-09-03:
--    - Manizales pasa de $7.000 a $5.000 de envío. Es una BAJADA de precio sobre la tarifa
--      que el bot viene cotizando desde la migración 005 y con la que se cerró la venta del
--      2026-08-19 ("2 x 40.000 + ~7.000"). Confirmado explícitamente por el dueño.
--    - Pereira, Armenia, Bogotá, Cali, Bucaramanga, Barranquilla, Cartagena y Santa Marta
--      PIERDEN su tarifa y pasan a "por confirmar": eran cifras de abril (005) que nunca se
--      contrastaron contra un envío real. El operador las coordina a mano.
--
-- Applied: 2026-09-04 02:52 UTC (prod, manualmente vía psql, transacción única con
--          ON_ERROR_STOP y verificación en la misma sesión).
--          system_prompt_template: 17261 -> 15952 caracteres.
--          Los tres marcadores que debían desaparecer devuelven false: 'RESUMEN DE
--          CONFIRMACIÓN', 'equivalencia aproximada' y 'total aprox'. Las dos líneas
--          OBLIGATORIO de cantidad y molienda están presentes.
--          shipping_rules reemplazado: quedan Manizales 5000, y Medellín, Envigado y
--          Sabaneta a 15000; el bloque zones desapareció; pickup false; default
--          to_confirm. presentation creado.
--          Aplicada DESPUÉS del despliegue, según su campo Orden y ADR-012: la revisión
--          con el código que renderiza el resumen quedó corriendo y sana antes de tocar
--          el prompt.

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
