# ROADMAP — histórico

> Solo se agrega texto, nunca se edita. Es texto sacado **tal cual** de `docs/ROADMAP.md` cuando
> un frente, una deuda o un paso se cerró. Lo vivo está en el ROADMAP; esto es la evidencia.

## Traslado del 2026-09-22

### Encabezado — historial de actualizaciones

> Anterior: 2026-09-07 (P31 reabierto: la fase 4 de P29 no cubre al cliente que
> responde horas después a una conversación cerrada — evidencia en la entrada de P31).
> Anterior: 2026-09-06 (arreglo de la clave de OpenAI de n8n tras dos días y medio de
> silencio; saludo repetido corregido; P35 registrado a partir de la conversación de prueba).
> Anterior: 2026-09-05 (P29 acotado a echoes: ADR-013 aceptado, migración 015 y
> el backend de las fases 1–3; P31 cerrado como decisión — ver
> `docs/briefs/brief-impl-P29-echoes-operador.md`).
> Anterior: 2026-09-04 (ADR-010 desplegado, migraciones 013 y 014 aplicadas).
> Anterior: 2026-09-01 (auditoría del ROADMAP contra el sistema vivo — ver
> `docs/postmortems/auditoria-2026-09-01-roadmap-y-planeacion.md`).
> Anterior: 2026-08-29 (comprobante ciego y dirección perdida del miércoles 08-26 — ver
> `docs/postmortems/diagnostico-2026-08-29-comprobante-ciego-y-direccion-perdida.md`).
> Anterior: 2026-08-19 (análisis de la venta real BSUID — ver
> `docs/postmortems/analisis-2026-08-19-venta-bsuid-colision-operador.md`)

### Encabezado — actualización del 2026-09-22

> Última actualización: 2026-09-22 (sincronización con la verificación read-only del 2026-09-12:
> deuda #7 resuelta y P4 cerrado — 4 resúmenes persistidos —; la fase 4 de P29 está viva desde
> el 09-07 y falta la 5, porque aún no ha llegado ningún echo; deuda #20 registrada).

### P14 · sección completa

### P14 · Mensajes con LID/privacidad se pierden en silencio  ← CERRADO, verificado e2e
**Qué**: WhatsApp desplegó privacidad de número. Para clientes con privacidad activada,
Meta omite `from` y `wa_id` y manda solo el **BSUID** (Business-Scoped User ID,
`CO.…`). El workflow validaba `from` con `typeValidation: strict` → rama false → Stop,
marcado "success" en 15 ms. **Drop 100% silencioso**: sin error, sin alerta, sin registro.
**Evidencia**: **2026-08-04** (no 08-01 — `timestamp 1785873069` = 14:51:09 COT), clienta
real escribió "Hola" y se perdió. Ejecución n8n 9459. Único mensaje real de cliente ese
día. Crecerá conforme más usuarios activen privacidad.
**Por qué es #1**: pierde clientes reales ANTES de que entren al flujo. Peor que un bug
dentro del flujo, porque ni te enteras de que existieron.

**Brief guía**: `docs/briefs/impl-brief-P14-lid-bsuid-final.md` (4 fases).

**Lo que el diagnóstico del payload real cambió** (exec 9459 vs 9461):
- Los campos BSUID **ya llegan**: el webhook es pass-through verbatim de Meta y
  `from_user_id`/`user_id` están presentes en TODOS los mensajes. No hay que cambiar el
  formato de webhook ni pedirle nada a Chakra para RECIBIR. El problema era de parsing.
- La única diferencia entre un payload normal y uno de LID: faltan `wa_id` y `from`.
- **La causa primaria no era el `If`**: el `Set` whitelist `map_webhook_data_arenillo` del
  workflow `master` copia 6 campos y DESTRUYE `from_user_id`/`user_id` antes de que el
  sub-workflow los vea. Arreglar solo el `If` no habría servido.

**Estado por fase**:
- [x] **Fase 1 · schema** — migración `012_add_bsuid_identity.sql`, aplicada en prod
      2026-08-17. Columna `bsuid VARCHAR(140)` propia, índice único `(client_id, bsuid)`,
      `phone_number` nullable. Deliberadamente NO dropea `uq_client_user_phone` (el upsert
      vivo lo referenciaba por nombre) → migración retrocompatible, sin ventana de rotura.
- [x] **Fase 2 · backend** — PR #60, desplegado (revisión nueva del Container App). Resolución
      BSUID-first con cascada `bsuid` → teléfono → insert. El paso por teléfono reusa al
      cliente pre-P14 sin escribirle el bsuid (la fusión es del ADR posterior). 174 tests.
- [x] **Fases 3 y 4 · n8n recibir y responder** — aplicadas 2026-08-18. Resultaron ser
      **11 nodos, no 3**: la identidad se copia A MANO en cada nodo Code de la cadena
      (`Normalize Inbound` → `Build LLM Prompt` → `Validate and Prepare Action` →
      `Process Backend Response` → envío), así que `bsuid` hubo que agregarlo en cada
      salto o llegaba `undefined`. Además de los 4 de recepción (incluido
      `POST Ingest Message`, que arma el JSON del body y el brief no menciona) y los 2 de
      envío (`Send WhatsApp via Chakra` y `Send Product Image`, vía `recipient`), hubo que
      tocar `Notify Owner WhatsApp` y `Build Operator Notice`: mostraban "null" /
      "desconocido" al operador, rompiendo el handoff de ADR-009 justo para estos clientes.
      Se hicieron juntas a propósito: la Fase 3 sola dejaba un estado PEOR que el bug
      original (el mensaje entra, gasta LLM y falla al enviar; hoy al menos se descarta
      gratis).

**Decidido y cerrado, no reabrir**:
- Sin telemetría-de-derrota. La versión previa del brief planeaba una alerta a Telegram
  para "no pude atender"; se descartó al confirmar Chakra: el sistema recibe y responde,
  no se construye el parche.
- El BSUID es **scoped por WABA** (confirmado por Chakra): el mismo cliente físico tiene
  un BSUID distinto por tenant. La unicidad es `(client_id, bsuid)`, **nunca** global.
- ~~Bomba latente: `phone` VARCHAR corto~~ → resuelta por la 012 con columna propia.
  Nota de nomenclatura: la columna es `phone_number`; `phone` es otra cosa (el slot del
  DAG en `lead_qualified`, dentro de `extracted_context`). No confundirlos en migraciones.

**Fuera de P14 → su propio ADR** (identidad primaria por BSUID): fusión phone↔BSUID con
write-back, `user_id_update` (el BSUID cambia si el cliente cambia de teléfono),
recolección del teléfono en el DAG como dato de envío, Contact Book. Hoy 33 de 33
`client_users` son pre-P14 (0 con bsuid) y se resuelven por el fallback.

**Aprovechar la consulta a Chakra** — la de LID ya está respondida; quedan: (a) si expone
descarga/transcripción de medios — decide P16 y P25; (b) si el cambio de facturación de
mensajes de servicio del 2026-10-01 les aplica y cómo lo repercuten — ver nota en P27.
**Verificación e2e (2026-08-18)**: se envió al webhook de producción un payload sin
`from` ni `wa_id` —misma estructura que la exec 9459 que se perdió— usando la identidad
LID del dueño en vez de la de la clienta real, para no mandarle un WhatsApp dos semanas
después. Resultado: exec 10373/10374, 12.4 s (antes: 15 ms hasta `Stop`). `Normalize
Inbound` extrajo `bsuid` con `phone_number: null`; el backend creó el `client_user` y
devolvió `should_respond: true`; el envío devolvió un `whatsappMessageId` cuyo wamid
decodifica a `CO.XXXXXXXXXXXXXXXX`, o sea **la respuesta salió contra el BSUID, sin
teléfono**. Fila en prod: `bsuid=CO.XXXXXXXXXXXXXXXX, phone_number=NULL`.

**Validación con cliente real (2026-08-19)**: 24 h después del e2e sintético, una clienta
real con privacidad activada (`bsuid CO.…`, `phone_number NULL`) atravesó el flujo
completo — 27 inbound, 23 outbound, todos contra el BSUID — y llegó hasta el punto de pago.
P14 es el mecanismo que hizo EXISTIR esa venta (antes era la exec 9459: drop de 15 ms).
Postmortem: `analisis-2026-08-19-venta-bsuid-colision-operador.md`.

**Deuda que deja abierta**: que la identidad se copie a mano en 4 nodos Code es
fragilidad estructural — cualquier campo de identidad nuevo va a tener este mismo
problema. Merece frente propio.

Rollback: `n8n_workflow/{master,cafe_arenillo_v2}.pre-p14-bsuid.json` (n8n NO tiene
historial de versiones: el archivo es el único respaldo).

### CERRADO Y VERIFICADO · texto completo

## ✅ CERRADO Y VERIFICADO

- **P11 · Venta duplicada** (2 caminos marcaban pago; se desactivó el camino LLM→payment en 3
  superficies: backend, prompt DB migración 011, n8n). Verificado en prod 2026-08-01:
  una venta, autor operador, cero fantasmas del LLM. Fix con verificación por mutación.
- **P8 · Circuit breaker** (3 outbounds idénticos consecutivos → human_handoff).
  **Nota de calibración (2026-08-23, dato — no hay acción abierta)**: el umbral de 3 se
  calibró cuando TODO inbound generaba turno, incluidos los ilegibles. Desde el guard de
  contenido ilegible, un medio ya no produce outbound, así que deja de aportar su voto a la
  cuenta de idénticos consecutivos: **el breaker es hoy menos sensible que cuando se
  calibró**. Observado el 2026-08-23 19:37 UTC — dos outbounds idénticos seguidos
  («¿Cuántas bolsas…?», execs 10880 y 10882) separados por una imagen ciega que antes habría
  respondido y ahora calla; con 2 de 3, el breaker no disparó. Revisar el umbral es parte
  del alcance de P16 (ver P16), no de una acción propia.
- **P3 · Gate de payment_confirmation permeable** (recálculo tras el gate de user_confirmation).
  **Superseded by ADR-009**: hoy el `payment_confirmation` del LLM se descarta siempre
  (`OPERATOR_ONLY_FIELDS`), así que el escenario del gate ya no puede existir. Los tests se
  conservan como regresión.
- **P2 · ORDER_FIELDS** (quantity/grind/roast se persisten; registro de compra con quantity/total).
- **P4 · Observabilidad de compaction** (dejó de fallar en silencio).
  **Causa raíz encontrada el 2026-09-01, 80 días después**: la clave de OpenAI del backend es
  inválida. `AuthenticationError: 401 invalid_api_key` sobre el secreto de OpenAI en Key Vault,
  capturado justamente por el ERROR con traza que P4 instaló — log de consola del backend del
  **2026-08-30T21:53:34.869Z**, `conversation_summary.py:220`. Era la **candidata 2** del
  `diagnostico-2026-06-14-P4-compaction.md`. n8n llama a OpenAI con otra credencial, y por eso el bot
  conversa mientras la memoria muere en silencio. **El fix es rotar el secreto, no código.** Hasta
  que se rote, la deuda #7 sigue abierta: 0 de 39 `client_users` con `last_conversation_summary`.
  **Resuelta y verificada el 2026-09-12**: 4 `client_users` con resumen persistido.
- **ADR-008 · Multiidioma + teléfono** (detección de idioma en backend; validación E.164-laxa).
- **ADR-009 · Lazo de handoff** (endpoint confirm-payment + auth escopada + Telegram +
  corte de respuesta n8n + registro de venta + cierre a closed). Probado e2e.
- **P12 · Slot perdido / captura de ORDER_FIELDS** en el directive (oportunista, no bloqueante).
  **Nota de alcance (2026-08-26, dato — la familia sigue viva fuera de lo que P12 cubrió)**: P12
  arregló la captura oportunista de los `ORDER_FIELDS` (`quantity`, `grind_preference`) en fase
  pre-producto, pero el mismo descarte ocurre con campos del **DAG** ofrecidos fuera de orden. Caso
  real: una clienta escribió su dirección a las 21:55:51 y el LLM devolvió
  `extracted_data = {"grind_preference": "grano"}` — **`shipping_address` nunca se extrajo**, así que
  no llegó a ningún gate y no está en `extracted_context`. El bot le pidió primero la ciudad y tiró el
  dato; cuando el flujo llegue a la dirección volverá a pedírsela, justo lo que el prompt prohíbe
  ("nunca se lo vuelvas a pedir"). Ver P30 y
  `docs/postmortems/diagnostico-2026-08-29-comprobante-ciego-y-direccion-perdida.md`.
- **P18 · Diagnóstico de datos legacy** — el diagnóstico se hizo el 08-19, y **la limpieza que quedó
  anotada como pendiente ya estaba hecha**: el `audit_log` registra `profile_corrected` (`operator`,
  2026-07-31 20:31:19 UTC, `purchase_count_before: 2 → after: 1`, PR #57). El `purchase_count: 2` que
  hoy tiene ese `client_user` son **dos compras distintas y legítimas** (07-20 y 08-01), no el
  duplicado. Las
  4 conversaciones con `payment_confirmation` están las 4 en `closed`. Cerrado sin trabajo pendiente
  (verificado 2026-09-01).
- **P19 · Mensaje engañoso de Telegram** — **cerrado sin implementar**: dependía de que existieran
  filas legadas (pago en contexto con la conversación aún en `human_handoff`) y no existe ninguna. Las
  2 conversaciones en `human_handoff` no tienen pago en contexto (verificado 2026-09-01).
- **Infra · minReplicas 0→1** (eliminó cold starts que perdían mensajes).
- **P1 · Drift de docs** (CLAUDE.md, n8n CLAUDE.md sincronizados con la realidad).

### Orden de cierre · punto 1 completo

1. ~~**Rotar el secreto de OpenAI** en Key Vault~~ ✅ **hecho el 2026-09-04**, y verificado contra
   la API con la petición exacta de la compaction (`json_schema` estricto, HTTP 200). Ojo con el
   detalle que casi lo deja a medias: la clave nueva se había creado bajo **otro nombre de secreto**,
   y el backend solo lee el suyo; ya está copiada al que corresponde. **Falta que una revisión nueva
   la tome** — el secreto se lee una sola vez, al arrancar —, cosa que hace el despliegue del
   punto 2. **Hecho el 2026-09-04**: la revisión `--0000058` arrancó con
   `OpenAI key: loaded from Key Vault`. Falta verificar que la compaction corra de verdad en la
   próxima conversación de un cliente recurrente — hasta leerlo, la deuda #7 no se cierra.
   ✅ **Leído el 2026-09-12**: `SELECT count(*) FROM client_users WHERE profile ? 'last_conversation_summary'`
   → 4 (sesión read-only). Deuda #7 resuelta, P4 cerrado.

### Deudas resueltas · filas completas (#7, #10, #13)

| # | Item | Severidad | Bloquea cliente que paga? |
|---|------|-----------|---------------------------|
| 7 | Memoria entre conversaciones rota EN PROD: la lazy compaction nunca ha persistido un resumen. **Medido 2026-08-19: 0 de 35 client_users con `last_conversation_summary`; 27 profiles vacíos** — es total, no intermitente. Re-saludo re-confirmado ese día (cliente del 06-12 saludado como desconocido). **CAUSA RAÍZ ENCONTRADA 2026-09-01: la clave de OpenAI del backend es inválida.** `AuthenticationError: 401 invalid_api_key` sobre el secreto de OpenAI en Key Vault, en el ERROR con traza que P4 instaló — log de consola del backend del 2026-08-30T21:53:34.869Z. Era la candidata 2 del postmortem P4. n8n usa otra credencial y por eso el bot conversa mientras la memoria muere. **El fix es rotar el secreto, no código**. **RESUELTA el 2026-09-04**: la clave nueva se había creado bajo otro nombre de secreto y el backend solo lee el suyo; ya está copiada al que corresponde y verificada contra la API con la petición exacta de la compaction (`json_schema` estricto → HTTP 200), lo que descarta las candidatas 1 y 3. La revisión `--0000058` (2026-09-04 02:47 UTC) ya la cargó: el log de arranque dice `OpenAI key: loaded from Key Vault`. **VERIFICADA EN PROD el 2026-09-12**: 4 `client_users` con resumen persistido (`SELECT count(*) FROM client_users WHERE profile ? 'last_conversation_summary'`, sesión read-only; el resumen vive en `profile` JSONB, no es columna). Funciona hoy, 4 de 35+. `INV-CONV-007` → `holds`, como foto: nada en CI lo re-verifica si la clave vuelve a morir | Alta | Sí (la "memoria del vendedor" es promesa central) |
| 10 | ~~Corte n8n~~ **resuelto** (ADR-009 §3: n8n corta pre-LLM por estado y pre-envío por `approved`+estado; el loop escalado ahora notifica por Telegram). Queda: loop de texto VARIABLE no cubierto (trigger solo texto idéntico exacto) — ver `docs/registros/registro-P8-limitaciones.md`, hoy frente **P10**; e2e del corte pendiente (primer handoff real post-fix). ⚠️ *Nota histórica: antes del 2026-06-14, `#10` designaba el "reset por idle 30 min" que este archivo documentaba pero que nunca existió en código (cerrado por P1). Los documentos de junio usan ese sentido viejo.* | Media (era Alta) | Menor |
| 13 | ~~El early-return del debounce coalescido devolvía un dict de 2 claves → **500 en CADA coalescencia**~~ **RESUELTA** (PR #63, desplegada 2026-08-23): `build_suppressed_response()` construye la respuesta completa para los tres caminos de supresión (`debounce`, `duplicate`, `unreadable_content`) y `reason` es campo del schema. Verificado en prod 2026-08-26 (execs 11144/11146/11150). Nota: el 500 nunca mató la ejecución porque `POST Ingest Message` lleva `continueOnFail: true`. **La race de fondo sigue abierta: es P7.** | ~~Alta~~ | ~~Sí~~ |
