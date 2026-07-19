# Postmortem de un éxito — Primera venta cerrada

**Fecha del análisis**: 2026-07-19 · **Venta**: 2026-07-15 · **Brief**: `docs/briefs/analisis-primera-venta-brief.md`

**Convención de evidencia**: toda afirmación con timestamp UTC y/o message_id corto está confirmada con SELECT contra la Postgres viva (read-only). PII enmascarada en todo el documento. `archivo:línea` = inferido del repo.

**Dato de partida confirmado por el humano** (no re-verificado, es premisa): los datos que el bot recolectó estaban completos y correctos; el operador no ajustó nada al tomar el handoff.

---

## 0. Identificación (y una corrección a la premisa del brief)

**La conversación de la venta es `31e4…09c8`, iniciada 2026-07-15 19:57:07 UTC**, cliente `b949…705a` (nombre "A. E. C.", tel `***5481`), 26 mensajes, 7 minutos 29 segundos. Es la única candidata: es la única conversación reciente con `user_confirmation=true` y datos completos en `extracted_context`, y su profile sincronizado coincide con lo que el operador usó.

**Pero la premisa del brief no se cumple en datos: esta conversación NUNCA pasó a `human_handoff`.** Su estado sigue siendo `active`. El DAG se detuvo en `user_confirmed`; `payment_confirmation` jamás se propuso ni se persistió, y por tanto el auto-escalate jamás disparó. La única conversación en `human_handoff` de toda la base (`d634…b5ab`, 2026-07-18) no es una venta: es un **loop bot-a-bot** — un bot de notificaciones de vuelos de LATAM escribió al número ("…tu vuelo n° 4012 de LATAM…", 12:49:35) y nuestro bot quedó saludando en bucle hasta que el circuit breaker P8 disparó (`loop_detected`, 12:51:12).

Esto reencuadra todo: la venta se cerró, pero **el sistema nunca se enteró de que cerró**. Lo que sigue analiza ambas cosas.

## 1. ¿Cuántas sesiones? — Una sola, y eso es suerte estructural

Una única fila en `conversations` para este `client_user`; `first_contact_at` = 19:57 del mismo día — clienta nueva, primera vez que escribe. Todo ocurrió en **una sesión continua de 7m29s** (primer inbound 19:57:07 → últimas instrucciones de pago 20:04:36). La lazy-compaction rota (deuda #7) **nunca se ejerció**: no hubo segunda conversación, no hubo seed desde profile, no hubo re-saludo posible. La venta esquivó por completo el problema no resuelto más grande del sistema. Eso no es diseño funcionando — es que la venta fue tan rápida que el diseño roto no alcanzó a estorbar.

## 2. Turnos y fricción — 12 turnos de bot, 2 desperdiciados

14 inbound (13 texto + 1 imagen) / 12 outbound. La fricción concreta, con evidencia:

- **Slot perdido y re-preguntado**: la clienta dijo "molido" en su primer mensaje de producto (19:58:04, "Me interesa la de 500 molido honey rojo"). El LLM nunca lo propuso como `grind_preference` hasta que lo re-preguntó a las 20:03:32 — cinco minutos y medio después — con una frase además incoherente: *"¿cómo prefieres el molido: ¿en grano o molido?"*.
- **Doble confirmación**: por culpa de lo anterior, el resumen del pedido se envió DOS veces (20:03:05 y 20:04:06, texto casi idéntico) y la clienta tuvo que decir que sí dos veces ("Si" 20:03:22, "Si gracias" 20:04:25). El primer "Si" no registró nada: los `extracted_data` del outbound de 20:03:32 muestran que el LLM **no propuso** `user_confirmation` (no fue un gate — el gate la habría aceptado, los 4 slots ya estaban). `user_confirmation` y `product_id` solo aparecen juntos en el último turno (20:04:36).
- Costo total: ~2 turnos y ~1 minuto. Esta clienta los toleró.

## 3. ¿Llegó fácil? — Sí: el bot no vendió, despachó

Su segundo mensaje fue "Estoy interesada en un café como lo puedo comprar?" (19:57:23). Cero venta consultiva: el bot respondió precio y especificaciones y recolectó datos. Dos momentos flojos que una clienta motivada dejó pasar:

- **Confirmación de identidad a ciegas**: envió una imagen (19:58:51, `message_type=image`, content vacío — el LLM nunca la vio) y preguntó "Ustedes no son estos mismos?" (19:59:01). El bot respondió *"Sí, somos nosotros"* (19:59:16) sin poder verificarlo. Salió bien — probablemente era su publicidad — pero es un patrón de afirmación inverificable que con una clienta desconfiada, o si la imagen fuera de OTRO negocio, destruye la confianza.
- **"¿es el honey rojo?"** (19:59:47): el bot respondió "proceso honey" sin confirmar ni negar el "rojo". Respuesta parcial; ella no insistió.
- A favor: cuando pidió "la de 500", el bot **no inventó** una presentación de 500g — corrigió a 340g (19:58:17), el único producto real del catálogo. Anti-alucinación funcionando de verdad.

## 4. Tono — la regla anti-chatbot se violó tres veces y no importó (esta vez)

Los primeros 4 turnos del bot incluyen tres preguntas de permiso: *"¿Te gustaría saber qué presentaciones…?"* (19:57:38), *"¿Te gustaría que te comparta más detalles…?"* (19:58:17), *"¿Te gustaría que comenzáramos con eso?"* (19:59:16). La clienta las atravesó sin reaccionar — respondía con especificaciones de compra, no con "sí me gustaría". A partir de la fase de datos el tono mejoró notablemente: preguntas directas de un dato por turno ("¿Me compartes tu nombre completo?" → teléfono → ciudad → dirección), y un resumen de pedido con aritmética correcta (2×40.000 + ~7.000 = ~87.000 ✓). Conclusión honesta: el tono robótico de apertura **no impidió esta venta porque la clienta ya venía decidida** — no hay evidencia de que sea inocuo con un cliente frío, y la fase donde el bot suena mejor (recolección) es justo la fase donde el cliente ya se comprometió.

## 5. Gates y checkpoints — se ejercieron poco, y limpio

- **Cero rechazos**: los 12 `agent_turn` del audit_log solo registran `context_updated`; ningún `warning:*`. El teléfono `***5481` (10 dígitos) pasó el gate E.164-laxo sin fricción — y de hecho coincide con el número de WhatsApp de la clienta (verificación que el sistema no hace, pero los datos eran consistentes).
- **P3 no se ejerció** (no hubo rechazo previo que requiriera recálculo). El gate de `user_confirmation` aceptó a la primera propuesta real porque los 4 slots llevaban ya dos turnos completos.
- **El DAG nunca completó**: tras "te comparto los medios de pago… me envías el comprobante" (20:04:36), **cero ingests llegaron al backend** (último `message_ingest`: 20:04:33). El comprobante — si existió — nunca tocó el sistema. No es determinable con datos del backend si la clienta no lo envió por WhatsApp o si se perdió aguas arriba (Chakra/n8n). El pago, es decir el cierre real, ocurrió **fuera de la vista del sistema**.
- **El corte post-handoff NO queda acreditado por esta venta.** El bot no volvió a responder porque no llegó ningún mensaje más — no porque un corte actuara (nunca hubo handoff que cortar). Peor: en la única conversación con handoff real (el loop LATAM), hay **7 outbounds persistidos DESPUÉS de la transición a `human_handoff`** (12:51:12), y el breaker re-disparó dos veces más (12:51:55, 13:30:48). Primera evidencia en prod de que la deuda #10 es real: n8n sigue enviando pese al handoff. La confirmación del humano de que "el bot no volvió a responder" es cierta para la venta, pero por ausencia de estímulo, no por diseño.

### Hallazgos incidentales

Salieron de la evidencia, no eran objetivo del brief:

- (a) El saludo inicial del loop LATAM se persistió **duplicado exacto** a las 12:49:46 — deuda #11 viva.
- (b) `message_count=28` con 26 filas reales — el contador cuenta cada inbound dos veces (14×2) y ningún outbound; bug cosmético de `ingest.py`.
- (c) Los `strategy_*` de la conversación de venta quedaron un turno desactualizados (`current_checkpoint=product_matched`, 40%) porque se computan en el ingest y el merge final llegó después del último ingest.
- (d) **El profile no registró la compra**: sin `purchases`, sin `purchase_count`, `lifecycle_stage='engaged'` — el registro de compra solo se escribe con `payment_confirmed` (`agent_action.py:442-453`), que nunca llegó. La "memoria del vendedor" no sabe que su primera clienta ya compró: si A. vuelve a escribir, el sistema la tratará como interesada, no como compradora.

## Veredicto: ¿reproducible o afortunada?

**Diseño funcionando** (se repetirá porque es el sistema):

- Recolección incremental slot-a-slot con cadencia de un dato por turno, y merge determinista limpio (6 `context_updated` progresivos en audit_log).
- Sync en vivo de stable facts al profile — nombre, teléfono, ciudad y dirección quedaron en `client_users.profile` correctos, que es exactamente lo que el operador usó sin ajustar nada.
- Resumen de confirmación con datos completos y aritmética correcta.
- Fidelidad al catálogo: corrigió 500g→340g en lugar de alucinar una presentación.
- Idempotencia inbound (13 wamids únicos, cero duplicados de entrada).

**Condiciones favorables** (quizás no se repitan):

- Clienta decidida que llegó queriendo comprar — el bot nunca tuvo que vender.
- Una sola sesión de 7.5 minutos — esquivó la compaction rota (deuda #7) y la ventana de 24h.
- Toleró tres preguntas de permiso, una re-pregunta de dato ya dado y doble confirmación.
- Creyó una confirmación de identidad hecha a ciegas sobre una imagen que el bot no vio.
- **El cierre real (pago → entrega al operador) ocurrió enteramente fuera del sistema.** El backend jamás habría escalado solo: dependió de que el operador estuviera mirando WhatsApp. Ese último tramo del DAG sigue sin ejercerse jamás en prod.

**Fricciones que no rompieron ESTA venta pero romperían la próxima:**

1. El último tramo no tiene camino probado: comprobante → `payment_confirmed` → handoff → notificación. Hoy la venta muere en "te comparto los medios de pago" si nadie está mirando.
2. Slot perdido → doble confirmación: un cliente impaciente abandona en el segundo "¿Todo bien con esos datos?".
3. Preguntas de permiso en la apertura: con un cliente frío (el que hay que ganar), es la fase más débil del bot.
4. Afirmaciones inverificables ante imágenes que el LLM no ve.
5. El sistema no registra la venta consumada: la próxima conversación de una clienta que YA compró empezará desde cero — y si además cruza la ventana de 24h, con la compaction rota, ni siquiera habrá contexto de la sesión anterior.

Veredicto en una frase: **el núcleo de recolección+validación es reproducible y funcionó tal como se diseñó; el cierre fue afortunado** — dependió de una clienta decidida, una sesión única, y un operador atento que cubrió a mano el tramo del sistema que nunca ha corrido solo.
