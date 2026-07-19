# Diagnóstico — Slot perdido en la primera venta (Hipótesis B confirmada)

**Fecha del diagnóstico**: 2026-07-19 · **Turno analizado**: 2026-07-15 19:58 UTC (conversación `31e4…09c8`) · **Origen**: `hallazgos-primera-venta.md` §2 · **Fix derivado**: `docs/briefs/impl-brief-order-fields-directive.md`

**Convención de evidencia**: timestamps UTC y message_ids = confirmado con SELECT read-only contra la Postgres viva; `[n8n]` = leído del workflow vivo `cafe_arenillo_v2`; `archivo:línea` = repo. PII enmascarada.

---

## La pregunta

En el turno donde la clienta dijo **"Me interesa la de 500 molido honey rojo"** (19:58:04), el bot no capturó producto, cantidad ni molienda — los re-preguntó ~5.5 minutos después, causando doble confirmación. Tres hipótesis a discriminar:

- **A** — el LLM extrajo pero el backend lo descartó (pariente del bug P2) → fix determinista en backend.
- **B** — el LLM no extrajo nada en ese turno → fix en orientación del LLM (prompt/directive).
- **C** — el 500g inexistente rompió la reconciliación pedido↔catálogo → fix en manejo de presentación inexistente.

## Veredicto: B, concluyente

El LLM no propuso ningún slot en ese turno. La evidencia cierra las tres fronteras posibles de pérdida:

### 1. El payload crudo del turno es `{}`

El outbound que responde al mensaje es `fe96a840-…-5aef1041b411` (19:58:17, gpt-4o-mini). Su `messages.extracted_data` literal: **`{}`**. Y ese campo guarda la propuesta **cruda**: `agent_action.py:355` persiste el `extracted_data` del request sin mutarlo (`compute_context_updates` construye dicts nuevos, nunca toca el input).

### 2. n8n reenvía el output del LLM textual, sin filtrar

`[n8n: cafe_arenillo_v2 → "Validate and Prepare Action"]`: el nodo hace
`extracted_data: (parsed.extracted_data && typeof … === 'object' && !Array.isArray(…)) ? parsed.extracted_data : {}` — reenvío verbatim, sin whitelist de keys. El `safeFallback` ("Disculpa, no entendí bien…") **no** disparó: el `response_text` entregado fue la corrección real a 340g, así que el JSON del modelo se parseó bien y el `{}` salió del propio modelo.

### 3. El backend no rechazó nada

El `agent_turn` de 19:58:17.345 en audit_log: `{"side_effects": []}` — ni `context_updated` ni `warning:*`. Todo camino de rechazo de gate emite warning observable (`agent_action.py:221-240`). Cero warnings + cero merge = input vacío, no filtrado silencioso.

**Hipótesis A muerta.** (Único microhueco no determinable: si el LLM hubiera emitido `extracted_data` como *array*, n8n lo coerce a `{}` en silencio — indistinguible en datos sin la respuesta cruda de OpenAI, que no se loguea. Aun ese caso sería output malformado del LLM: familia B, no descarte del backend. Candidato a log en n8n cuando se haga P5.)

## Por qué el LLM no extrajo (sub-diagnóstico de B)

- **El directive de ese turno pedía solo `product_id`, y en voz baja.** El `strategy_snapshot` literal de la conversación: `current_checkpoint: "product_matched"`, `missing_fields: ["product_id"]`. El motor es puro y determinista (`goal_strategy.py`); con contexto vacío el directive era `SALES PROGRESS 0% / NEXT INFO NEEDED: product_id / HINT (low priority…): help the customer choose a product…` + "Do NOT mention or push…".
- **El directive jamás pide quantity/grind/roast**: son `ORDER_FIELDS`, no son `required_fields` de ningún checkpoint — el motor no los conoce.
- **La única instrucción de extracción es genérica y enterrada**: una línea al fondo del system prompt (`[n8n: "Build LLM Prompt"]`: lista de keys válidas — que SÍ incluye `quantity` y `grind_preference` — más "Only extract data the customer explicitly stated"). El modelo la ignoró en fase conversacional.
- **Patrón en los 12 turnos**: el LLM extrajo casi exclusivamente lo que él mismo acababa de preguntar (nombre tras pedir nombre, teléfono tras pedir teléfono…). Lo ofrecido espontáneamente fuera de fase ("molido" a las 19:58, el producto mismo) no se capturó hasta que el guion llegó ahí — `product_id` recién en el último turno (20:04:36).

## C descartada estructuralmente

No existe reconciliación pedido↔catálogo en el backend: `compute_context_updates` no tiene gate para `product_id` (pasa sin validar contra `products`) y nada matchea cantidad/presentación. La corrección a 340g ocurrió en el **mismo turno** (19:58:17), dentro del `response_text`, autoría del LLM leyendo el catálogo del `business_context` (único producto: 340g, $40.000). No hubo intento de captura que fallara. Si el "500" inexistente *influyó* en la cautela del modelo, no es determinable — el mecanismo que C postula no existe en el sistema.

## P2 descartado como causa

Commit `0a0c2de` ("P2: persist quantity/grind/roast…") es del **2026-06-14**, un mes antes de la venta (último merge previo a la venta: 2026-07-05, PR #50; CI/CD despliega en push a main). Prueba en datos, misma conversación: `quantity=2` mergeó a las 20:03:05 y `grind_preference` a las 20:04:06 (`context_updated` en audit_log). Cuando el LLM propuso, el backend persistió a la primera, siempre.

## Hallazgo incidental (sin fix en esta rama)

`conversations.message_count` doble-cuenta los inbound y no cuenta los outbound: la conversación de la venta registra `28` con 26 filas reales (14 in + 12 out; 14×2=28). Origen probable: `ingest.py:204-212` (UPDATE SQL + mutación del atributo ORM del mismo contador). Cosmético — nada lo consume para lógica — pero registrado.

## Dónde vive el fix

En la orientación del LLM — el directive (`goal_strategy.py`), la parte que el modelo sí obedece — no en el backend ni en reconciliación de catálogo. Implementación: `docs/briefs/impl-brief-order-fields-directive.md`. Limitación conocida más amplia (el LLM solo captura lo que pregunta) registrada en la nota final de ese brief; no se ataca aquí.
