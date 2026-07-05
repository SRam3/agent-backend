# Auditoría v2 — estado actual + revisión viva + plan de trabajo

**Fecha**: 2026-06-14
**Brief**: `docs/audit-brief.md`
**Modo de ejecución**: READ-ONLY estricto. Sesión Postgres con `default_transaction_read_only = on` (verificado con `SHOW` como primer comando); contra n8n solo `GET /api/v1/workflows[/{id}]` y `GET /api/v1/executions`; contra Azure solo lecturas (`az keyvault secret show`, `az containerapp show/logs`). Cero escrituras, cero archivos del repo modificados durante la auditoría, cero fixes ejecutados.

**Convención de evidencia**: `archivo:línea` = inferido del repo; `[DB]` = confirmado con SELECT contra la Postgres viva; `[n8n]` = confirmado leyendo la definición viva del workflow. PII enmascarada.

---

## 0. Drift (repo ↔ sistema vivo)

**Schema vivo vs migraciones: SIN drift.** Las 6 tablas vivas (`clients`, `client_users`, `products`, `conversations`, `messages`, `audit_log`) coinciden columna a columna con el estado acumulado 001→009 `[DB: information_schema.columns + pg_constraint]`. Los CHECKs vivos son exactamente los esperados: `ck_conversation_state` con 3 estados (007:40-41), `ck_client_users_lifecycle_stage` (008:26-27), `ck_message_direction` (001:245). No sobrevive ninguna tabla dropeada (`leads`, `orders`, `order_line_items` ausentes) y no hay tablas sin documentar.

**No existe tabla de versiones de migración.** `[DB: \dt + information_schema.tables → ninguna `alembic_version`/`schema_migrations`]`. Cuál migración fue la última aplicada **no es determinable desde la DB** — solo por inferencia (el schema coincide con 009, y el md5 del prompt lo confirma, abajo).

**Prompt vivo = migración 009, byte a byte.** `[DB: md5(system_prompt_template) = 027023ba3b9583585c1e96d3b48f825d, len 17340]` — idéntico al md5 calculado del texto de `009_humanize_prompt_after_apr30_review.sql:51-291`. Cero drift en el prompt.

**`business_rules` vivo = 003+005.** Las 10 claves vivas (`shipping_rules`, `payment_methods`, `discount_rules`, `notification_phone`, etc.) coinciden con 003:44-71 + 005:26-61 `[DB: jsonb_object_keys]`.

**Drift que SÍ existe — todo en docs y exports, no en el sistema:**

1. **`CLAUDE.md` (backend) dice "7 tablas activas"** — son 6 (el propio doc lista 6; la DB tiene 6). Error de conteo.
2. **`CLAUDE.md` describe "reset extracted_context si idle 30+ min" en el paso 3 del ingest** — ese código NO existe (`ingest.py:131-177` solo aplica ventana de 24h; ningún reset por idle). Reconfirma el hallazgo previo: DEUDA #10 describe código ficticio.
3. **`CLAUDE.md` dice "1 transacción" en ingest** — falso: `session.commit()` intermedio en `ingest.py:215` + `asyncio.sleep(5)` en :216 = mínimo 2 transacciones, y el advisory lock se suelta y re-adquiere (`ingest.py:235-238`). Reconfirmado.
4. **`n8n_workflow/CLAUDE.md` está gravemente desactualizado**: documenta un state machine de 7 estados (`idle/qualifying/selling/ordering/...`), `available_actions`, `proposed_action`, y campos de respuesta (`has_full_name`, `media_url`) que ya no existen — el código real tiene 3 estados (`state_machine.py:13-17`) y el response real es otro (`api/v1/ingest.py:35-47`).
5. **`master.json` del repo apunta al workflow legacy**: el nodo `cafe_arenillo_workflow` referencia `RnoPSNFG2frdVVll` (master.json:200), pero el master VIVO apunta a `xKtfVQsyYWkwQta9` (cafe_arenillo_v2) `[n8n: GET /workflows/xUhGo…]`. El export está obsoleto.
6. **`cafe_arenillo_v2.json` del repo no es el workflow** — es un resumen-puntero de 29 líneas. La definición real de 17 nodos solo vive en n8n. Además la URL de Chakra difiere: el repo documenta `…/665932016609209/messages` (cafe_arenillo_v2.json:15) y el vivo usa `…/12280…74/messages` en sus 3 nodos de envío `[n8n]` — el ID de teléfono de WhatsApp cambió y el repo no se enteró.

---

## 1–5. Re-verificación contra el sistema vivo

**El workflow v2 (backend-governed) es el que corre: CONFIRMADO leyendo el n8n vivo, no asumido.** `master` (`xUhGo…`) y `cafe_arenillo_v2` (`xKtfVQ…`) están `active: true`; el legacy `cafe_arenillo` (`RnoPSN…`) está `active: false` `[n8n: GET /workflows]`. El v2 llama a `POST …/api/v1/ingest/message` y `POST …/api/v1/agent/action` (nodos "POST Ingest Message" y "POST Agent Action", host `ca-backend.****.azurecontainerapps.io`) `[n8n]`.

**Modelo real: `gpt-4o-mini`. Discrepancia resuelta.** El nodo "Build LLM Prompt" usa `config.ai_model` que viene del backend, que lo lee de `clients.ai_model = 'gpt-4o-mini'` `[DB]`. El literal `'gpt-4.1-mini'` que confundía en el primer reporte es solo el **fallback** si el backend no enviara el campo (`[n8n: Build LLM Prompt]`: `const model = config.ai_model || 'gpt-4.1-mini'`). Y los 85 mensajes outbound registran `ai_model_used = 'gpt-4o-mini'`, sin excepción `[DB: SELECT DISTINCT ai_model_used]`.

**Pérdida de contexto entre conversaciones: CONFIRMADA en datos.** El único usuario recurrente (`15d89…`) tiene 2 conversaciones (2026-05-03 y 2026-06-11) y su `profile` está **vacío** — sin `last_conversation_summary`, sin nada `[DB]`. La lazy-compaction (`ingest.py:146-167`) corrió (la conversación nueva se creó) pero no persistió resumen. Detalle en sección 6.

---

## 6. Base de datos viva (solo SELECT)

### Inventario y salud
1 client, 8 client_users, 1 product, 7 conversations, 174 messages, 170 audit_log `[DB]`. Volumen pequeño: esto sigue siendo un piloto con tráfico real esporádico.

### Estados: el state machine nunca se ha movido
`SELECT state, COUNT(*) FROM conversations GROUP BY state` → **`active`: 7. Punto.** Ni una sola conversación ha llegado a `human_handoff` ni a `closed` en toda la historia. El audit_log lo corrobora: solo existen eventos `message_ingest` (85) y `agent_turn` (85) — **cero `auto_escalated`, cero `state_transition`** `[DB]`. La conversación más avanzada (48 msgs, 2026-05-01) se quedó en checkpoint `payment_confirmed` al 80% y nunca escaló. ¿Por qué? El LLM **nunca, en 85 turnos, propuso `payment_confirmation`** (`COUNT WHERE extracted_data ? 'payment_confirmation'` = 0) `[DB]` — ningún cliente envió comprobante. Consecuencia: el camino "auto-escalate → Notify Owner" (`agent_action.py:160-196` + nodo n8n "IF Escalated") **jamás se ha ejecutado en producción**. Es código sin estrenar, y el handoff humano (DEUDA #4) es doblemente teórico: no hay vista de operador Y nunca se ha generado un handoff.

### Shape real del JSONB — los "no determinable" cerrados

**`conversations.extracted_context`** (7 filas): las únicas claves que existen son `product_id` (3), `full_name` (2), `shipping_city` (2), `phone` (2), `shipping_address` (2), `user_confirmation` (1) `[DB: jsonb_object_keys]`. **`quantity` y `grind_preference`: 0 apariciones en la historia.**

Y aquí está la prueba dura del bug del filtro: el LLM **sí los extrajo** — `messages.extracted_data` contiene `quantity` en 12 mensajes y `grind_preference` en 8 `[DB]` — pero `STRATEGY_FIELDS` (`agent_action.py:35-39`) los descarta antes del merge. CONFIRMADO punta a punta: el prompt los exige (009:218-219), el LLM los entrega, el backend los tira, y el bloque "ESTADO DEL PEDIDO" del siguiente turno los vuelve a listar como faltantes (`prompt_context.py:111-121` los incluye en `_ORDER_FIELDS`). El bot le re-pregunta al cliente lo que el cliente ya dijo, estructuralmente.

**`client_users.profile`** (8 filas): solo aparecen `full_name`, `first_name`, `city`, `phone`, `shipping_address` (en 2 usuarios) `[DB]`. **Nunca han existido** `purchases`, `purchase_count`, `last_conversation_summary`, `language`, `communication_style`, `preferences`, `email`. El "contrato" del COMMENT de 008:50-64 está incumplido al 100% en datos. El array `purchases` está vacío en toda la DB — consistente con que `payment_confirmation` nunca se persistió (la única vía que lo escribe es `_merge_profile` con `payment_just_confirmed`, `agent_action.py:298-305`). Nota adicional de código: cuando algún día se escriba, el registro irá **incompleto** — solo `{date, product_id}`, sin `quantity` ni `total` (`agent_action.py:301-304`), porque `quantity` nunca llega al contexto. El bug del filtro y el perfil incompleto son el mismo bug.

**Lazy compaction: muerta en producción, con el caso de prueba perfecto.** Usuario `15d89…`: conversación del 3-may (4 msgs) + conversación del 11-jun 00:37 — gap de 39 días, fuera de la ventana de 24h, `needs_summary()` = true garantizado (profile vacío). Si la compaction funcionara, hoy ese perfil tendría `last_conversation_summary`. Está vacío `[DB]`. El backend sí carga la key al boot (`main.py:42-68`; log de 11-jun 01:49: `"OpenAI key: loaded from Key Vault (openai-key)"` `[az logs]`), así que la causa exacta del fallo a las 00:37 **no es determinable** con la retención de logs actual — pero el diseño garantiza el silencio: `summarize_conversation` traga cualquier excepción y devuelve `None` con un `logger.warning` (`conversation_summary.py:210-215`), y nadie lee esos warnings (DEUDA #3). El bug de re-saludo (DEUDA #7) queda confirmado en datos con causa raíz en la compaction silenciosamente rota.

### Idempotencia outbound: cero, con duplicados reales

- **85 de 85 mensajes outbound tienen `chakra_message_id` NULL** `[DB]`. Desde que 007:56 dropeó `idempotency_key`, la única defensa de idempotencia de `messages` (`chakra_message_id UNIQUE`) aplica solo a inbound. Outbound: nada.
- **Duplicado real #1** (conv `7def8…`, 3-may): saludo idéntico (md5 `daf85…`, "Hola, soy Sebastian de Café Arenillo…") enviado 2 veces con 8.9s de diferencia. Anatomía `[DB]`: inbound 20:49:16 → saludo 20:49:41; inbound 20:49:42 → **mismo saludo** 20:49:49. El segundo turno tenía el primer saludo en su historial y aun así re-saludó — violación de la regla de saludo de 009 + cero dedup del lado del sistema.
- **Duplicado real #2** (misma conv): dos inbounds a 4s (20:52:02 y 20:52:06) y **ambos** recibieron respuesta (20:52:09 y 20:52:13). El debounce no suprimió el primero: la ventana de `sleep(5)` + el check `created_at > msg_timestamp` (`ingest.py:218-227`) pierde la carrera si el segundo ingest no ha commiteado cuando el primero despierta. Race condition del debounce, evidenciada en datos.
- **El episodio 19x** (conv `889167…`, 25-abr): el mensaje "Lo siento, pero aquí solo hablamos de café…" aparece **19 veces en 29 minutos**. NO es duplicación técnica — son 35 inbound / 35 outbound apareados `[DB]`: un usuario fuera-de-contexto y el bot respondiendo idéntico 19 veces, 70 mensajes, 0% de progreso, sin ningún circuit breaker. Un loop conversacional que quema tokens y degrada la marca, distinto del bug de idempotencia pero igual de real.
- **La imagen duplicada NO deja huella en la DB**: `message_type` solo registra `text` (174/174) `[DB]`. El envío de imagen ocurre exclusivamente en n8n ("Send Product Image") y **no se persiste como mensaje**. El sistema es ciego a su propio side-effect — esta es la condición estructural que permite el duplicado (sección 7).

### Integridad multi-tenant
Limpia: 0 mismatches `messages.client_id ↔ conversations.client_id`, 0 mismatches `conversations.client_id ↔ client_users.client_id` `[DB]`. Sin huérfanos (FKs lo garantizan). `lifecycle_stage`: 7 `new`, 1 `engaged`, 0 `customer` — coherente con cero ventas completadas.

### Detalle menor con evidencia
`ai_latency_ms = 0` en 85/85 outbound `[DB]`; el nodo "Validate and Prepare Action" lo hardcodea (`latency_ms: 0` `[n8n]`). Los tokens sí llegan (0 filas sin tokens).

---

## 7. Flujo n8n vivo

**Cadena activa** `[n8n]`: `master` (Webhook → Switch por `phone_number_id` → `cafe_arenillo_v2`) → v2: `If Message Exists → Normalize Inbound → POST Ingest Message → IF Should Respond → Build LLM Prompt → Call OpenAI → Validate and Prepare Action → POST Agent Action → Process Backend Response → IF Approved to Send → Send WhatsApp via Chakra → IF Has Image → Send Product Image → IF Escalated → Notify Owner WhatsApp`.

**Ensamble del prompt real** (nodo "Build LLM Prompt", `[n8n]`), en este orden exacto:
1. `client_config.system_prompt_template` — el texto de 009 (17.340 chars).
2. `business_context` — generado por `format_business_context` (`prompt_context.py:11-106`): catálogo (1 producto, Café Arenillo $40.000 COP, con `image_url` y la regla "Send the product photo ONCE per conversation"), shipping rules de 005, medios de pago de 003, descuentos.
3. `conversation_summary` — `format_conversation_summary` (`prompt_context.py:228-283`): bloque `=== CLIENTE ===` + `=== ESTADO DEL PEDIDO ===` con ✓/✗. (Aquí es donde `quantity`/`grind_preference` salen eternamente como ✗.)
4. `strategy_directive` — `directive.to_prompt()` (`goal_strategy.py:50-80`): barra de progreso + NEXT INFO NEEDED.
5. Bloque INTERNAL: `STATE: active` + instrucción de JSON con claves válidas — **incluye `quantity`, `grind_preference`, `roast_preference`, `send_image_url`** — más "If the customer asks for a photo or image, set send_image_url…".
6. User message: los últimos **10** mensajes en formato `Customer:/Agent:` (el backend manda 20, `ingest.py:314-329`; n8n usa `slice(-10)` — la mitad del historial disponible se descarta).

Llamada con `response_format: {type: 'json_object'}`, temperatura 0.30 de DB. JSON mode simple, no `json_schema` estricto — el shape de `extracted_data` sigue sin garantía (DEUDA #8), solo hay validación superficial en "Validate and Prepare Action" (parse + fallback seguro + whitelist de transiciones).

**Camino de la imagen — causa raíz cerrada.** Hay **un solo camino** de envío de imagen por turno (texto → `IF Has Image` → imagen; ramas en serie, no paralelas) y **ningún nodo tiene `retryOnFail`** `[n8n]`. O sea: el duplicado NO viene de reintentos de n8n ni de ramas dobles. Viene de la combinación: (a) `send_image_url` lo decide el LLM en `extracted_data`, leído del `action_body` **local** de n8n ("Process Backend Response" lo parsea de `prev.action_body`, no de la respuesta del backend) — el backend ni lo valida ni lo gobierna (no está en `STRATEGY_FIELDS` ni hay gate); (b) el envío no se persiste como mensaje (sección 6), así que en el turno siguiente ni el backend ni el historial le dicen al LLM "ya la mandaste" de forma confiable; la única defensa es la instrucción del prompt (009:151-156) dependiendo de que el LLM recuerde por el texto de la conversación. Un side-effect sin estado materializado y sin idempotencia — exactamente la clase de problema que el primer reporte predijo desde código, ahora con la mecánica completa confirmada.

**Manejo del 409: NO existe.** El nodo "POST Agent Action" no tiene `onError`, `retryOnFail` ni rama de error `[n8n]`. Un 409 de `/agent/action` (ADR-003) lanza excepción → la ejecución muere → no se reintenta el ingest, no se alerta, el cliente no recibe respuesta. **La protección de contexto viejo de ADR-003 es, en la práctica, un drop silencioso del turno.** El else-branch de "Process Backend Response" (`should_send: false`) solo cubre un 200 malformado, nunca se alcanza en un 409 porque el nodo HTTP revienta antes.

**Fallas 5xx / backend caído: igual de silencioso.** Ningún nodo HTTP del workflow tiene retry ni error handling; no hay error-workflow global configurado (`settings` del workflow sin `errorWorkflow`) `[n8n]`. DEUDA #3 confirmada en el sistema vivo. Las ejecuciones retenidas (solo 5, todas `success`) no permiten cuantificar la tasa histórica de fallas — **no determinable** con la retención actual, otro síntoma de la misma deuda.

**Detalle**: "Notify Owner WhatsApp" hardcodea el número del dueño (`****8477`, coincide con `business_rules.notification_phone`) en el body del nodo — config duplicada n8n/DB.

---

## 8. Plan de trabajo priorizado

Plan de **remediación** (cero features del north-star; el norte se usa solo como lente de coherencia). Recordatorio de lente: el norte dice "estado materializado en Postgres como única verdad, uniones endurecidas, motor testeable sin LLM". Ningún fix de abajo construye eso; varios lo *acercan* y ninguno debería *estorbarlo*.

### Ítems

**P1 — 🟢 Sincronizar documentación con la realidad** (4 archivos)
- Qué: `CLAUDE.md` backend (6 tablas, no 7; eliminar el "reset idle 30 min"; "≥2 transacciones + sleep en el ingest"); reescribir o retirar `n8n_workflow/CLAUDE.md` (describe un API de 7 estados que no existe — es activamente peligroso para cualquier sesión futura que construya flujos n8n); re-exportar `master.json` vivo (hoy apunta al workflow legacy); reemplazar `cafe_arenillo_v2.json` por el export real de 17 nodos (incluida la URL de Chakra vigente).
- Evidencia: sección 0, ítems 1–6.
- Norte: **alineado** (los docs son el contrato; el norte exige entender el estado actual). ADR: no.

**P2 — 🟡 Persistir `quantity`/`grind_preference`/`roast_preference`** (el dato perdido)
- Qué: incluirlos en la persistencia de `extracted_context` — idealmente como un set separado de "order fields" no-DAG junto a `STRATEGY_FIELDS` (`agent_action.py:35-39`) — y completar `_merge_profile` para que `purchases` lleve `quantity`/`total` como promete el COMMENT de 008 (`agent_action.py:301-304`). Con tests de servicio.
- Evidencia: 12/8 mensajes con el dato en `extracted_data` y 0 en `extracted_context` `[DB]`; 009:218-219 exige extraerlos; `prompt_context.py:111-121` los re-pide eternamente.
- Norte: **alineado** — es literalmente "materializar el estado en Postgres". Es además prerequisito de que un futuro `pending_intent`/resume tenga datos completos (`ingest.py:393-395` ya intenta rehidratar `quantity`). ADR: no (no toca schema; JSONB es flexible y el COMMENT de 008 ya lo contempla).

**P3 — 🟡 Cerrar el gate permeable de `payment_confirmation`**
- Qué: recalcular `merged` después de eliminar un `user_confirmation` rechazado (`agent_action.py:118-131`), y añadir el `side_effect` de rechazo que hoy falta en la rama de payment (la de user_confirmation sí lo agrega, :125).
- Evidencia: código; en datos nunca se disparó (payment jamás propuesto `[DB]`), así que es preventivo — pero protege el paso más sensible del flujo (dinero).
- Norte: **alineado** (gates deterministas más estrictos = backend gobierna). ADR: no. Test obligatorio: turno con `user_confirmation` rechazado + `payment_confirmation` simultáneo.

**P4 — 🟢/🟡 Resucitar la lazy-compaction + hacerla ruidosa**
- Qué: (1) diagnosticar el fallo real — reproducir `summarize_conversation` contra la conversación del 3-may en entorno controlado, revisar Log Analytics si hay retención; (2) como mínimo, subir el `logger.warning` de `conversation_summary.py:210-215` a algo observable (error + contador), porque hoy la memoria del negocio muere en silencio.
- Evidencia: usuario recurrente con profile vacío tras gap de 39 días `[DB]`; boot sí carga la key `[az logs]`; causa exacta no determinable por retención.
- Norte: **alineado** (el perfil persistente es la memoria que el resume del norte necesita). ADR: no. Nota: si el diagnóstico revela config de infra, es 🟢; si revela bug de código, 🟡.

**P5 — 🟡 Manejo de errores en n8n: 409, 5xx y alerta** (cambio en sistema vivo — sesión dedicada)
- Qué: `onError`/ramas de error en "POST Ingest Message", "POST Agent Action" y los 3 nodos de Chakra; en 409: no reenviar, registrar y notificar (reutilizar el patrón "Notify Owner"); error-workflow global de n8n para lo demás. Decidir explícitamente la política de 409 (¿avisar al cliente? ¿re-ingest manual?) — documentarla aunque sea en el propio nodo.
- Evidencia: ningún nodo con retry/onError `[n8n]`; ADR-003 hoy = drop silencioso.
- Norte: **neutro-alineado** — endurece la unión Call1↔Call2 sin comprometerse con el rediseño de resume; `strategy_version` sigue siendo la red de seguridad que el norte dice conservar. ADR: no, pero exige export del workflow al repo antes y después (cierra también parte de P1).

**P6 — 🔴 Idempotencia outbound (texto E imagen) — requiere ADR previo**
- Qué: el problema general, no el parche de la imagen: (a) todo outbound se persiste con clave de idempotencia (p.ej. derivada de `conversation_id + strategy_version + tipo`), (b) el envío de imagen se persiste como mensaje (`message_type='image'`) — hoy es invisible, (c) `send_image_url` se gobierna en el backend (gate: solo si la conversación no tiene imagen previa), no en la disciplina del LLM, (d) n8n envía lo que el backend aprueba, no lo que el LLM dijo en el `action_body` local.
- Evidencia: 85/85 outbound sin `chakra_message_id` `[DB]`; saludo duplicado 8.9s `[DB]`; `message_type` solo `text` `[DB]`; "Process Backend Response" lee `send_image_url` del action_body local `[n8n]`; 007:56 dropeó `idempotency_key` sin reemplazo para outbound.
- Norte: **fuertemente alineado** — es el caso concreto de "estado materializado como única verdad" (idea #1). Hacerlo como parche solo-imagen estaría **en tensión** con el norte; hacerlo como idempotencia outbound general es caminar hacia él. **ADR: SÍ, antes de tocar nada** (toca schema —columna/clave en `messages`— y el hot path).

**P7 — 🔴 Debounce: race + conexión ocupada — requiere ADR previo**
- Qué: el `sleep(5)` dentro del request ocupa el pool (DEUDA #2) y el check post-sleep pierde la carrera con inbounds no commiteados (`ingest.py:214-232`). El fix real (mover el debounce fuera de la transacción / supersession por `last_message_at` bajo lock) toca el hot path completo del ingest.
- Evidencia: doble respuesta a inbounds separados 4s `[DB: conv 7def8…, 20:52]`.
- Norte: **en tensión si se sobre-diseña** — el resume asíncrono del norte reordenaría esta zona entera; el ADR debe elegir el fix mínimo que no construya infraestructura que luego se deshaga. ADR: sí.

**P8 — 🟡 Circuit breaker para loops conversacionales**
- Qué: regla determinista en el backend (p.ej. N outbounds idénticos consecutivos → escalar a `human_handoff` o frenar respuesta), nada de LLM.
- Evidencia: 19 respuestas idénticas en 29 min, 70 msgs, 0% progreso `[DB: conv 889167…]`.
- Norte: **neutro** (cuando existan "escenarios como datos", este será uno; no construir ese catálogo ahora). ADR: no, pero la transición automática extra debe registrarse en audit_log como las demás.

**P9 — 🟢 Microfixes n8n**: `latency_ms` real en "Validate and Prepare Action" (hoy 0 hardcodeado, evidencia `[DB+n8n]`); evaluar subir `slice(-10)` a los 20 mensajes que el backend ya manda. Norte: neutro. Va en el mismo cambio que P5 para tocar n8n una sola vez.

*(Observación sin tarea: `human_handoff` jamás alcanzado y `lifecycle_stage` sin `customer` no son bugs — son la consecuencia de que ninguna venta cerró aún. El plan no los "arregla"; los fixes P2/P6 son lo que destraba ese embudo.)*

### Secuencia recomendada de sesiones de Code

1. **Sesión 1 — 🟢 cero riesgo de prod**: P1 completo (docs + exports). Commit por archivo, sin tocar DB ni n8n vivo. Si se quiere trazabilidad de migraciones (la tabla `schema_migrations`), en esta sesión solo se *escribe* la migración y su mini-ADR; no se aplica.
2. **Sesión 2 — 🟡 backend con tests**: P2, luego P3, luego P8 (un commit + test cada uno, en ese orden: P2 desbloquea que el resumen del pedido sea completable). P4 como diagnóstico al final (lectura + decisión).
3. **Sesión 3 — 🟡 n8n vivo, con cuidado**: P5 + P9 en una sola intervención sobre el workflow, con export antes/después al repo y prueba controlada de un turno real.
4. **Bloqueado tras ADR — 🔴**: ADR de idempotencia outbound (P6, el mínimo exigido por el brief) y ADR/decisión del debounce (P7). Solo después de aprobados, una sesión de implementación por cada uno.

Una cosa a la vez, commit y verificación entre cada una, y "entender y decidir antes de tocar" intacto.
