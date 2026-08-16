# Brief de implementación — Captura oportunista de ORDER_FIELDS en el directive

> **Origen**: diagnóstico del slot perdido en la primera venta (Hipótesis B confirmada).
> El LLM extrajo `{}` en el turno donde la clienta dijo "500 molido honey rojo", porque
> el directive solo pedía `product_id` y las keys de ORDER_FIELDS solo viven enterradas
> en el muro del prompt (que el modelo ignora). Consecuencia: re-pregunta + doble
> confirmación.
>
> **Sistema REAL**: backend FastAPI en prod, Postgres 6 tablas post-007, migración 010,
> P2/P3/P8/ADR-008 mergeados. Edita código vivo. NO rediseña. NO toca schema.
>
> **Disciplina**: plan por archivo antes de codear, espera confirmación. Un commit con
> tests, suite verde. Rama nueva desde main. NO desplegar.

---

## La causa raíz (confirmada en datos)

- `extracted_data` del turno de 19:58 = `{}` (el LLM no propuso nada). Backend y n8n
  verificados: no filtraron; el `{}` salió del modelo.
- El LLM captura fielmente lo que el **directive** le orienta (capturó teléfono cuando
  el directive pidió teléfono, etc.) pero ignora lo que solo está en el muro del prompt.
- `quantity`/`grind_preference`/`roast_preference` son ORDER_FIELDS: NO son
  `required_fields` de ningún checkpoint del DAG, así que el directive nunca los
  menciona. La única referencia es una línea genérica al fondo del prompt → ignorada.

**Por tanto el fix NO es tocar el `system_prompt_template`** (ahí ya está la línea y no
funciona — sería repetir el error de 6 migraciones). El fix va en la construcción del
**directive** (`goal_strategy.py`), que es la parte que el LLM sí obedece.

## Las tres condiciones de diseño (confirmadas)

1. **En el directive, no en el prompt**: la orientación se añade al texto del directive
   que produce el `GoalStrategyEngine`, no al prompt en DB. Sin migración.
2. **Oportunista, NO bloqueante**: la línea orienta "si el cliente menciona molienda o
   cantidad, captúrala" — NUNCA "pregunta por la molienda" ni la convierte en paso
   obligatorio. Los ORDER_FIELDS siguen sin ser checkpoints; el DAG no cambia; el cierre
   no gana un paso. Es captura, no gatekeeping.
3. **Contextual a la fase temprana**: la orientación aparece SOLO mientras `product_id`
   NO esté resuelto (fase donde el cliente habla del producto y sus características). Una
   vez elegido el producto, la línea desaparece del directive para no ensuciar las fases
   de nombre/dirección/pago donde molienda/cantidad ya no vienen al caso.

## El fix

En `goal_strategy.py`, donde se construye el `directive` / su texto:

- Cuando el checkpoint `product_matched` está pendiente (es decir, `product_id` aún no
  está en el contexto), añadir al directive una línea corta y de prioridad visible (no
  enterrada), del tipo:
  `ORDER DETAILS: if the customer mentions grind, roast, or quantity, capture it into`
  `extracted_data (grind_preference / roast_preference / quantity). Do NOT ask for these`
  `proactively — only capture what they volunteer.`
  (Ajustar el wording al estilo existente del directive; mantenerlo breve.)
- Cuando `product_id` YA está resuelto, esa línea NO se incluye.
- No tocar los `required_fields`, ni el orden del DAG, ni los gates. ORDER_FIELDS siguen
  fuera del DAG; solo se mejora su captura vía orientación del LLM.

## Tests (puros, sin DB) — en `tests/services/test_goal_strategy.py`

1. **Aparece en fase temprana**: con `product_id` ausente en el contexto, el directive
   generado INCLUYE la orientación de ORDER_FIELDS.
2. **Desaparece tras elegir producto**: con `product_id` presente, el directive NO
   incluye la línea de ORDER_FIELDS.
3. **No es bloqueante**: verificar que la presencia/ausencia de la línea NO cambia qué
   checkpoint está activo ni los `required_fields` — el DAG se comporta idéntico. (El
   fix solo añade texto orientativo; no altera la lógica de progreso.)
4. **Wording no imperativo de pregunta**: el texto orienta captura, no instruye a
   preguntar (aserción sobre el contenido del directive: contiene "capture"/"if the
   customer mentions", no "ask for grind").
5. **Regresión**: los ~22 tests existentes de goal_strategy siguen verdes; el directive
   de fases posteriores (lead_qualified, shipping, etc.) es byte-idéntico al actual.

## Cierre

- Un commit: cambio en goal_strategy.py + tests. Verde.
- NO migración, NO schema, NO tocar system_prompt_template, NO n8n, NO deploy.
- Reportar: archivo:línea del cambio, tests añadidos, y confirmación de que el directive
  de las fases post-product es idéntico (regresión).

## Nota para el futuro (NO implementar)

Este fix es ESTRECHO: resuelve la captura de ORDER_FIELDS. El diagnóstico reveló un
patrón MÁS AMPLIO —el LLM solo captura lo que pregunta, ignora datos espontáneos de
cualquier campo (nombre, ciudad, cantidad dichos de una vez)—. NO se ataca aquí. Si
aparece evidencia de que ese patrón cuesta ventas en otros campos, será su propio ADR.
Registrado como limitación conocida, no como tarea.
