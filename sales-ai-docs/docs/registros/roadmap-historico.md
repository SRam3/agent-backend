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

## Traslado del 2026-09-22 (2) — análisis read-only de producción

### Encabezado — actualización anterior

> Última actualización: 2026-09-22 (lo cerrado se movió tal cual a
> `docs/registros/roadmap-historico.md`; aquí queda una línea por cerrado).

### P29 · sección completa

### P29 · Presencia de operador: el bot no sabe cuándo un humano está atendiendo (registrado 2026-08-19, **ABIERTO y acotado a echoes el 2026-09-05**)
**Qué**: cuando el operador escribe manualmente por WhatsApp, el sistema queda ciego y el
bot sigue activo: los mensajes del humano no se persisten (no pasan por el webhook), el LLM
razona sobre un diálogo al que le falta la mitad, y no existe ninguna forma de callar al
bot (el único freno automático es el circuit breaker).
**Evidencia**: 2026-08-19 — colisión real de "dos Sebastian": la clienta respondía al
operador y el bot interpretaba esas respuestas como propias; el bot inventó la "llave 1234"
ENTRE la promesa del operador ("ya te comparto la llave") y la llave real. Testimonio del
operador: "no entré a confirmar rápidamente, me quedé atendiendo porque vi el bot muy
perdido" — cuando el bot falla, el humano va al chat, no a Telegram; el lazo de ADR-009
asume lo contrario.
**Segunda ocurrencia real (2026-08-26), con el mecanismo ya identificado**: cerrar la venta
**reinicia** al bot en vez de silenciarlo. El operador pulsó el botón a las 17:38:38 (`sale_closed`);
el cliente escribió un mensaje corto a las 17:41:12 y, como la conversación estaba `closed`, el
ingest **creó otra desde cero** (v1, sin historial) y el bot saludó de nuevo, usando el nombre de
pila y ofreciendo ayuda — a un cliente que acababa de comprar y
mientras el operador atendía a mano. Idéntico al 2026-08-19 con otra clienta: once días, dos ventas,
mismo comportamiento. El guard de contenido ilegible no aplica aquí ni podría (el disparador es texto
legible). Detalle en `docs/postmortems/diagnostico-2026-08-29-comprobante-ciego-y-direccion-perdida.md`.
**Toca la consecuencia aceptada de ADR-009** ("el bot acompaña en active hasta el botón") y
es insumo directo de la decisión P23 (estados de espera explícitos). Deuda observable: #14.

**Decidido y en curso (ADR-013, 2026-09-05)**. La pregunta "¿Chakra webhookea los echoes?"
tiene respuesta: **sí**. El payload crudo está en el diagnóstico del 2026-08-22 §1.1
(`exec 10678`), con `changes[0].field = "smb_message_echoes"` y el mensaje en
`message_echoes[]`. Muere en dos nodos que solo saben leer `messages[]`: el `Set` whitelist
`map_webhook_data_arenillo` produce `null`, y `If Message Exists` manda la ejecución a `Stop`
marcada `success` en ~130 ms. La identidad sobrevive intacta: `contacts[0].user_id` está en
**247 de 247** payloads y el whitelist ya lo copia desde P14.

Las otras dos opciones se descartaron y quedan registradas en ADR-013: el botón "tomo la
conversación" y el endpoint de pausa manual dependen de que el operador avise, y el testimonio
del 08-19 dice justo lo contrario.

**Qué hace el alcance acotado**: el sistema **ve** al operador (sus mensajes se persisten con
`author='operator'` y entran al historial que el LLM lee) y se **calla** mientras el humano
atiende (supresión con vencimiento de 30 min, refrescada por cada echo, sin cambio de estado).

**Qué NO hace, y hay que decirlo así**:
- **No mata el turno que ya está en vuelo.** Si el cliente escribe, n8n llama al LLM y
  mientras tanto el operador escribe, ese turno aprueba y envía igual. Es exactamente el caso
  "llave 1234" del 08-19, y queda **parcialmente descubierto**. Es **P34**.
- **No reactiva automáticamente** más allá del vencimiento de la ventana.
- **No construye vista de operador** — sigue siendo deuda #4.
- **No es una capa de post-venta** — eso depende de P24.

**Fases**: 0 ADR-013 ✅ · 1 migración `015` (`author` + backfill) · 2 endpoint
`POST /api/v1/ingest/operator-echo` · 3 pausa en el ingest + evento de auditoría + autoría en
`recent_messages` · 4 sesión n8n única con P5 y P9, re-export antes y después · 5 verificación
en prod con un echo real. **Las fases 2 y 3 se despliegan sin la 4 sin riesgo de regresión**:
el endpoint queda vivo sin tráfico y la pausa nunca dispara porque no hay filas con
`author='operator'`.

**Dos hallazgos del análisis que el brief no traía**, ambos porque el código asumía que todo
outbound es del bot, y ambos ya corregidos en la fase 3: el circuit breaker de P8 se diluía si
un mensaje del operador caía entre dos respuestas idénticas, y el prompt de la compaction
habría resumido al perfil del cliente, como dicho por el bot, lo que prometió el humano.

Brief: `docs/briefs/brief-impl-P29-echoes-operador.md`. Decisión: `ADR-013`.
Riesgo: [ADR] + [DB] + [B] + [N8N].

### Orden de cierre · revisión del 2026-09-01 y lista anterior

**Revisado el 2026-09-01** contra el sistema vivo (auditoría §10). Qué cambió respecto del orden
anterior, y por qué: sube al punto 1 rotar la clave de OpenAI, porque cierra la deuda #7 sin escribir
código; **P5 deja de ser higiene intercalada** y sube, porque es la única forma de enterarse de que el
sistema falló; **P16 (bytes) y P22 salen de los primeros ocho**, porque son lo interesante y no lo que
desbloquea ventas; y entran P31, P32 y P33, que son baratos y atacan daño ya observado en clientes
reales.

1. ✅ **Rotar el secreto de OpenAI** — hecho el 2026-09-04 y verificado el 2026-09-12 (histórico).
2. **P15 vía ADR-010, en cuatro pasos y en este orden**: ~~aplicar el DDL~~ ✅ (2026-09-04
   02:09:28 UTC) → ~~mergear y desplegar~~ ✅ (PR #68, revisión `--0000058`, 02:47 UTC) →
   ~~aplicar datos y prompt (`014`)~~ ✅ (02:52 UTC) → **verificar en una conversación real**,
   que es lo único que queda. El envío a Manizales quedó confirmado por el negocio. Al mergear: ADR-010
   pasa a `Accepted`, entra al índice de `docs/decisions/README.md`, y se resuelve ahí la reserva
   del número 010 para P10. El despliegue **también recoge la clave de OpenAI nueva** (punto 1),
   porque el backend solo lee el secreto al arrancar.
3. **P31 reabierto** (2026-09-07). El cierre del 09-05 daba por hecho que P29 lo cubría entero y
   no es así: la pausa expira —correctamente— cuando el cliente responde horas después, y la
   conversación `closed` bifurca el diálogo igual. El rechazo de la ventana temporal sigue en pie;
   lo que vuelve es el hueco de la conversación cerrada. **No se despacha solo**: va con el punto 4.
4. **P29 fase 4 + P31, un solo cierre** (decidido el 2026-09-07). Primero la sesión de n8n, que
   hace que el sistema vea al operador; después el hueco post-venta, que es backend y probablemente
   ADR. En ese orden porque el segundo se evalúa mejor con echoes ya entrando.
   P29 acotado a echoes — en curso. El `master` deja pasar `message_echoes[]`, el backend los
   persiste como outbound del operador, y una regla determinista pausa al bot tras un echo. Es la
   mitad cara de P29 y la que evita que el bot contradiga al humano delante del cliente. **Tercera
   ocurrencia real el 2026-08-30**, sobre una conversación que sigue `active`. **Fases 0–3 hechas**
   (ADR-013, migración `015`, endpoint y pausa). ✅ **Fase 4 desplegada el 2026-09-07 13:02 UTC.**
   **Falta la fase 5**, la verificación en prod: al 2026-09-12, cero filas con `author='operator'`
   porque de 101 ejecuciones posteriores al despliegue ninguna trae `message_echoes`. La rama del
   Switch no ha corrido, no está rota.
5. **P5 + P9 + ~~el switch de echoes de P29~~** (✅ el switch se desplegó solo el 2026-09-07) en un solo toque del workflow vivo, con **re-export de
   los tres workflows antes y después**: el respaldo del repo dejó de ser el vivo el 08-29. Se junta
   con la fase 4 de P29 a propósito: `map_webhook_data_arenillo` es el nodo que causó el drop
   silencioso de P14 y no se toca dos veces.
6. **P32** (placeholder de medios en el historial) — backend puro, cierra "pidió el comprobante que
   ya tenía" sin abrir la descarga de bytes. **Sube**, porque el punto que ocupaba este lugar se
   fundió con el 4.
7. **P17** y decisión de **P20**. Sesión corta, entre fixes.
8. **Medios de pago gobernados por el backend**, como sección nueva de ADR-010 en lugar de abrir P28.
