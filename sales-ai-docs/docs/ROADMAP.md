# ROADMAP — Sales AI Agent

> Estado de todos los frentes abiertos. Fuente de verdad del "qué sigue". Se actualiza
> conforme se cierra cada frente. Vive en el repo (versionado) a propósito: no crear una
> segunda fuente de verdad fuera de git.
>
> **Objetivo actual**: endurecer el flujo de venta para que clientes reales lo usen con
> confianza. Todo se prioriza contra ese objetivo.
>
> **Disciplina**: cerrar punto a punto. No se abre el siguiente frente hasta cerrar el
> actual (diagnóstico → decisión → fix → verificación). Cada fix, su test; baby-steps.
>
> **Registrar no es abrir.** Varios frentes de este archivo (P22–P27) existen solo para no
> perder la idea. Tener un número P no autoriza a tocarlos: la autorización la da la
> sección "Orden sugerido de cierre". Esto aplica en particular a lo que vino de
> `docs/north-star.md`, que sigue siendo contexto de dirección de solo lectura.
>
> Última actualización: 2026-08-19 (análisis de la venta real BSUID — ver
> `docs/postmortems/analisis-2026-08-19-venta-bsuid-colision-operador.md`)

---

## Notación

Tres ejes distintos. **Nunca son intercambiables**:

| Eje | Qué identifica | Dónde vive |
|---|---|---|
| `P<n>` | **Frente de trabajo** — algo que alguien va a hacer | este archivo (registro canónico al final) |
| `DEUDA #<n>` | **Problema observado** — algo que está mal hoy | tabla "Deuda técnica visible" en `CLAUDE.md` |
| `ADR-<nnn>` | **Decisión** tomada y argumentada | `docs/decisions/` |

Un frente P puede remediar una deuda, y puede exigir un ADR previo. Se escribe
**`P7 (remedia deuda #2)`**, nunca `deuda #2/P7` con slash: el slash sugiere que son
el mismo objeto y fue justo lo que hizo perder la pista de qué estaba hecho y qué no.

**Reglas de numeración**:

- Los números **no se reciclan**. Un frente cerrado conserva su P para siempre; una deuda
  retirada conserva su `#` y nadie más lo usa. Reutilizar un número rompe todo documento
  anterior que lo mencione — ya pasó dos veces (ver "Notas históricas").
- **Sin zero-padding**: `P10`, no `P010`. El orden alfabético de `ls` se rompe; se acepta
  a cambio de que el ID en prosa y en nombre de archivo sea idéntico.
- **`P` mayúscula** en prosa y en nombres de archivo. Las ramas git van en minúscula
  (`feat/p14-lid-privacidad`) como excepción deliberada: es convención git y no genera
  ambigüedad.
- Frentes nuevos toman el siguiente número libre — hoy, **P28**.
- **Un frente sin número P no existe.** `purchase_intents` estuvo citado como pendiente en
  cuatro ADRs y en `CLAUDE.md` durante meses sin número propio, y por eso nunca entró en
  ninguna priorización. Hoy es P24. Si algo aparece dos veces en prosa, dale un número.

---

## Leyenda

- 🔴 Bloquea el objetivo (cliente real afectado) — prioridad alta
- 🟡 Deuda/mejora real, no bloquea hoy — prioridad media
- 🟢 Higiene / bajo riesgo — se intercala entre fixes
- 🔵 Diseño en espera — no implementar aún, esperar evidencia/decisión
- ✅ Cerrado y verificado

Riesgo de implementación: [B]ackend acotado · [DB] migración · [N8N] workflow vivo ·
[ADR] requiere decisión escrita antes de tocar código.

---

## 🔴 ABIERTOS — bloquean "clientes reales con confianza"

### P14 · Mensajes con LID/privacidad se pierden en silencio  ← CERRADO, verificado e2e
**Qué**: WhatsApp desplegó privacidad de número. Para clientes con privacidad activada,
Meta omite `from` y `wa_id` y manda solo el **BSUID** (Business-Scoped User ID,
`CO.1034…`). El workflow validaba `from` con `typeValidation: strict` → rama false → Stop,
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
real con privacidad activada (`bsuid CO.…5687`, `phone_number NULL`) atravesó el flujo
completo — 27 inbound, 23 outbound, todos contra el BSUID — y llegó hasta el punto de pago.
P14 es el mecanismo que hizo EXISTIR esa venta (antes era la exec 9459: drop de 15 ms).
Postmortem: `analisis-2026-08-19-venta-bsuid-colision-operador.md`.

**Deuda que deja abierta**: que la identidad se copie a mano en 4 nodos Code es
fragilidad estructural — cualquier campo de identidad nuevo va a tener este mismo
problema. Merece frente propio.

Rollback: `n8n_workflow/{master,cafe_arenillo_v2}.pre-p14-bsuid.json` (n8n NO tiene
historial de versiones: el archivo es el único respaldo).

### P15 · user_confirmation se fija por interpretación del LLM (hermano del bug de payment)
**Qué**: el LLM marca `user_confirmation: true` interpretando un mensaje del cliente, y un
mensaje cruzado en vuelo lo dispara sin que haya confirmación real.
**Evidencia**: 2026-08-01, "envíame una foto del producto" llegó 0.8 s antes de que el bot
terminara "¿todo bien?", el LLM lo leyó como "sí" y marcó user_confirmation → convocó al
operador con un mensaje que no confirmaba nada. Daño nulo hoy (confirmaste de verdad 6 s
después), pero la puerta está abierta.
**Por qué importa**: es la MISMA clase del bug de payment que acabamos de cerrar (el LLM
afirmando un hecho que no le consta), sobre otro checkpoint que dispara una acción real
(convocar operador). Incoherente dejarlo abierto justo tras cerrar su gemelo.
**Patrón sistémico a vigilar**: cualquier checkpoint que el LLM marca por interpretación
es vulnerable. Confirmados: payment (✅ resuelto), user_confirmation (P15). Los checkpoints
de *datos* (dirección, teléfono) proveen datos, no afirman hechos → probablemente no
afectados. **Decisión tomada**: resolver P15 acotado primero; si su solución se parece a la
de payment, ENTONCES evaluar extraer un mecanismo general. No generalizar por elegancia
antes de tener 2 casos resueltos.
**Tercer canal a vigilar (ver P25)**: una transcripción de audio es texto de usuario con
más ruido. Cuando exista, entra por esta misma superficie.
**Evidencia nueva que lo AGRAVA (2026-08-19)**: exec 10602 — la clienta mandó "Barrio E. C."
(un fragmento de dirección) y el LLM extrajo `user_confirmation: true` ANTES de que el bot
enviara siquiera el resumen del pedido. El gate lo aceptó (los 4 slots estaban), disparó
`checkpoint_completed:user_confirmed` y convocó al operador por Telegram sobre una
confirmación que no existió. Quedó persistido en prod. Ya no es "la puerta está abierta":
entró.
**Alcance añadido (H6 del postmortem)**: el gate exige `full_name+phone+shipping_address+
shipping_city` pero NO `product_id` — `user_confirmed` se completó con `product_matched`
incompleto (la clienta nunca nombró el producto: "este café" + imagen ciega). Consecuencia
verificada en código: `confirm-payment` registraría la venta sin precio/total
(`_fetch_product_price(None) → None`). Decidir dentro de P15 si el gate exige el DAG
upstream completo.
**Empezar por**: diagnóstico read-only (cómo se fija hoy user_confirmation, por qué el
cruce lo disparó, si la solución se parece a la de payment).
Riesgo: [B] probable, a confirmar tras diagnóstico.

### P16 · Medios entrantes llegan con content vacío (sistema ciego a imagen y audio)
**Qué**: los mensajes con medio se guardan con `content` vacío — no hay manejo de medios.
El sistema no puede razonar sobre nada que no sea texto.
**Evidencia**: 2026-08-01, el comprobante de pago (imagen) entró como mensaje vacío; el
bot "vio" un mensaje en blanco y repitió su despedida (causó el mensaje duplicado). La
foto de producto pedida tampoco se envió.
**Por qué importa**: el comprobante de pago —el artefacto MÁS importante de la venta— es
una imagen, y el sistema es ciego a él. Hoy lo salva que el operador lo ve en WhatsApp.
Es brecha de CAPACIDAD transversal (entrante: comprobante; saliente: foto producto), no un
bug puntual.
**El frente es el PIPELINE, no el tipo de medio.** Imagen y nota de voz comparten el 90%
del camino: contrato de ingest con `media_type`, descarga desde Chakra (URL autenticada,
con expiración), decisión de persistencia, y qué llega al pipeline conversacional. Lo que
se hace con los bytes al final difiere (visión vs. ASR) y se decide aparte — ver P25.
**Alcance ampliado (2026-08-19)**: no son solo medios. Un evento **`edit`** de WhatsApp
(clienta corrigió un typo) entró como inbound vacío con wamid nuevo → el LLM vio un mensaje
en blanco y **se re-presentó desde cero en mitad de la venta** ("Hola, soy Sebastian…"; la
clienta respondió "🤔"). Medido en DB: **50 inbound con content vacío = 15,6% del total**
(35 `unsupported`, 12 `image`, 2 `audio`, 1 `edit`). El pipeline necesita un allowlist de
`message_type` además de la descarga de bytes. Y la imagen ciega de hoy no era un
comprobante: era la identificación del producto — raíz de que `product_id` nunca se
resolviera (ver P15/H6).
**Alcance**: grande. Probablemente [ADR] para decidir hasta dónde (¿solo registrar que
llegó media?, ¿pasarla al LLM?, ¿persistir el comprobante?, ¿cuánto se retiene?). Decidir
por separado.
Riesgo: [ADR] + [B] + [N8N].

### P28 · El NLG afirma datos operativos falsos (registrado 2026-08-19, NO abierto)
**Qué**: no existe ningún gobierno sobre el texto SALIENTE del LLM cuando afirma datos
operativos del negocio (medios de pago, llaves de transferencia, puntos/condiciones de
entrega). ADR-002 gobierna slots entrantes; el `response_text` viaja sin validación.
**Evidencia**: 2026-08-19, exec 10632 — la clienta, con la plata en la mano ("Ya te
transfiero los 87"), pidió "Compárteme llave por favor" (la llave de transferencia). El bot
respondió **"La llave para recoger el café es 1234"**: interpretó "llave" como llave física
e **inventó el valor**. Los medios de pago reales estaban en el prompt y nunca los
compartió. Sin la intervención manual del operador (que mandó la llave real 60 s después),
la clienta transfería a ciegas o abandonaba.
**Clase ya vista**: "Sí, somos nosotros" a ciegas (postmortem 2026-07-15 §3). Hoy escaló al
punto exacto donde cambia dinero de manos.
**Deslinde con P21**: P21 reestructura el prompt para que el LLM OBEDEZCA reglas; P28
decide qué datos operativos NO se le confían al NLG en absoluto (inyección determinista,
plantilla, o detección de peticiones de pago en el backend). Pueden converger; se decide al
abrir.
**No abrir sin decidir el orden** — la mitigación barata (medios de pago al directive) está
anotada en P21 como adelanto quirúrgico.
Riesgo: [B] + posible [N8N]; la versión mínima es prompt/directive ([DB] migración).

### P29 · Presencia de operador: el bot no sabe cuándo un humano está atendiendo (registrado 2026-08-19, NO abierto)
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
**Toca la consecuencia aceptada de ADR-009** ("el bot acompaña en active hasta el botón") y
es insumo directo de la decisión P23 (estados de espera explícitos). Opciones a evaluar al
abrir: endpoint operador → pausa (`active → human_handoff` manual), detección de mensajes
del operador vía Chakra (¿webhookea echoes?), o botón "tomo la conversación" en el aviso
Telegram. Deuda observable: #14.
Riesgo: [ADR] (toca ADR-009/P23) + [B] + [N8N].

---

## 🟡 ABIERTOS — deuda real, no bloquea hoy

### P22 · Motor ejecutable sin LLM (remedia deuda #1)
**Qué**: correr el DAG completo (ingest → estrategia → validación → side effects) con
stubs deterministas para clasificación de intención y extracción de campos
(substring/regex/`key=value`), sin red, sin API key, sin costo. Absorbe el catálogo de
**escenarios de error COMO DATOS** (`happy_path`, `missing_fields`, `low_confidence`,
`producto_inválido`, `gate_rechaza_slot`, `send_failure`): forzar una rama de error es
pasar un dato, no escribir un mock ni una rama especial.
**Origen**: `docs/north-star.md` §2 y §3, fusionados en un solo frente. Ese archivo los
presenta como dirección futura; en realidad son **deuda #1 con otro nombre** (cero tests de
integración). Se les asigna número P sin que eso implique implementarlos ya.
**Por qué importa más de lo que sugiere su color**: hoy CADA fix se verifica a mano contra
producción después del hecho (P11, P12, ADR-009 se comprobaron mirando la DB). P14, P15 y
P16 tocan ingest, gates y medios a la vez. Sin esto, el costo de verificación crece más
rápido que el de implementación, y el conejillo de indias es un cliente real.
**Momento**: después de P15, antes de P21. No antes — P14 pierde clientes hoy.
Riesgo: [B], aditivo. No toca hot path ni schema.

### P24 · `purchase_intents` — estado de venta que sobrevive a la conversación
**Qué**: el carrito vive en `conversations.extracted_context` y muere con la ventana de
24h. El `profile` (ADR-005) sabe QUIÉN es el cliente, no QUE estaba comprando 3 bolsas. Si
alguien abandona a mitad de la venta y vuelve tres días después, el sistema lo saluda como
si nada hubiera pasado.
**Origen**: citado como pendiente en ADR-004 §51, ADR-005 §57, ADR-008 §17, ADR-009 §11 y
`CLAUDE.md:300` — **sin número P en ninguno**. Por eso nunca entró en una priorización.
Se le asigna número aquí (ver la regla añadida en "Notación").
**Mitigación actual, frágil**: `pending_intent` dentro de `last_conversation_summary`,
que depende de que la lazy-compaction viva — deuda #7, rota en producción durante meses.
**Habilitador de producto, no solo deuda**: sin esta tabla no existe el concepto "carrito
abandonado", y por tanto P27 (campañas) no tiene sobre qué disparar. Es precondición dura.
**Evidencia nueva (2026-08-19)**: pedido real de 4 bolsas (~$160.000, conv `1deb5fda`,
"Lodge P. V.") murió a mitad de flujo tras una pregunta de permiso del bot. Hoy ese carrito
no existe para ninguna pieza del sistema: 41 conversaciones `active` históricas y ninguna
señal de "abandonada". Es el mejor caso concreto de este frente hasta la fecha.
**No es resucitar `leads`/`orders`** (ADR-004 sigue vigente): entidad nueva, propósito
acotado, con `client_id` como toda tabla del sistema.
Riesgo: [ADR] + [DB] + [B].

### P26 · Consentimiento, canal autorizado y opt-out (base legal de todo outbound)
**Qué**: registro por `client_user` de opt-in comercial explícito, canal autorizado,
timestamp, texto exacto con el que se pidió, y estado de opt-out. Chequeo obligatorio
previo a CUALQUIER mensaje business-initiated. Mecanismo de baja en cada plantilla.
**Marco legal colombiano (no opcional)**: Ley 2300/2023 + Registro de Números Excluidos
(RNE) + Ley 1581/2012. Desde 2024 el RNE aplica también a mensajería por aplicaciones, no
solo SMS. Implica: consulta al RNE antes de cada campaña; ventana horaria L-V 07:00–19:00
y Sáb 08:00–15:00, prohibido domingos y festivos; mecanismo ágil de cancelación. Un cron
que dispara a las 22:00 de un domingo es una infracción, no un bug. Sanciona la SIC.
**Multi-tenant**: el `client` (Café Arenillo) es responsable del tratamiento; nosotros
encargados. Requiere cláusula contractual además de código, y el sistema tiene que poder
*probar* el consentimiento, no solo tenerlo.
**Lo único de este bloque que conviene adelantar**: el opt-in es barato de capturar hoy e
**imposible de capturar retroactivamente**. Cada día sin capturarlo es contactabilidad
futura perdida sobre la base instalada. La captura mínima (una columna + una frase en el
flujo) puede intercalarse como 🟢 sin abrir P26 completo; el motor de campañas espera.
Riesgo: [ADR] + [DB] + [B].

### P7 · Debounce: rediseño (remedia deuda #2)
**Qué**: `asyncio.sleep(5)` DENTRO de la transacción ocupa el pool y suma 5 s a cada
`/ingest`. Además su ventana temporal se ANCLA al timestamp de WhatsApp, no al reloj del
ingest → la ventana efectiva se desplaza con la latencia de entrega (los medios llegan
más lento). Fue la causa del mensaje duplicado del 2026-08-01.
**Requisitos que absorbió este ADR**: (a) sacar la espera de la transacción; (b) el delay
humano opcional (ver P21); (c) robustez ante latencia variable de WhatsApp.
**Bug puntual que NO espera a este ADR (deuda #13, 2026-08-19)**: el early-return del
camino coalescido (`services/ingest.py:228`) devuelve `{'should_respond': False, 'reason':
'debounce'}`, que no valida contra `IngestMessageResponse` → **500 en CADA coalescencia**
(4 veces en la venta real de hoy; n8n traga el error en silencio — deuda #12). El path de
`DuplicateMessageError` sí construye la respuesta dummy completa; a este se le olvidó.
El fix es devolver un modelo válido — un cambio acotado, con su test, independiente del
rediseño. La race de fondo (dos outbounds idénticos consecutivos 19:41:26/19:41:33 — a UNO
del circuit breaker) sigue siendo de este ADR.
**Decidir P23 ANTES de escribir este ADR**: si adoptamos resume por checkpoint, el modelo
temporal de referencia cambia y este rediseño se rehace.
Riesgo: [ADR] + [B] — toca hot path, el north-star lo reescribiría. No tocar sin ADR.

### P6 · Idempotencia outbound (remedia deuda #11)
**Qué**: el sistema no garantiza que un mismo mensaje no se envíe dos veces (saludos
duplicados, imagen duplicada). La 007 dropeó `idempotency_key` y no se repuso.
**Evidencia**: el mensaje duplicado del 2026-08-01 también toca esto.
**Se vuelve crítico con P27**: un mensaje duplicado dentro de la conversación es molesto;
una campaña duplicada es dinero real, quality rating y una queja regulatoria.
Riesgo: [ADR] + [DB] + [B] — toca schema + hot path.

### P5 · Manejo de errores 409/5xx en n8n (remedia deuda #12)
**Qué**: n8n no maneja el 409 (strategy_version stale) ni 5xx del backend. Un 409 hoy =
drop silencioso del turno. Relacionado con deuda #3 (fallas silenciosas).
Riesgo: [N8N].

---

## 🟢 ABIERTOS — higiene, intercalar entre fixes

### P17 · Barrido de código muerto tras el fix de venta duplicada
**Qué**: al desactivar el camino LLM→payment pudo quedar código sin uso más allá de lo ya
borrado (`_PAYMENT_CONFIRMATION_REQUIRES`, gate de pago). Barrer qué quedó colgando.
Riesgo: [B], bajo. Hacer entre fixes.

### P18 · Diagnóstico de datos legacy (pendiente desde el fix de venta duplicada)
**Qué**: SELECT read-only: cuántas conversaciones tienen `payment_confirmation` en
contexto / estado viejo, cuáles reales vs. prueba. Decide si limpiar es "borrar 1 fila" o
"reconciliar N ventas reales mal registradas". NO asumir que es solo la fila de prueba.
**Diagnóstico HECHO (2026-08-19, SELECT contra prod)**: solo 2 conversaciones con
`payment_confirmation`, ambas `closed` y legítimas — el e2e `9635…` (07-20) y la venta real
`7be24ff4` (08-01). No hay filas en estado inconsistente; el caso "legado en human_handoff"
de la nota de ADR-009/P19 ya no existe en datos. Queda solo la limpieza puntual del
`purchase_count: 2` del e2e (pendiente de P11).
Riesgo: read-only ✓ hecho; queda un UPDATE puntual aprobado por humano.

### P19 · Mensaje engañoso de Telegram (caso legado)
**Qué**: el callback dice "ya estaba confirmada y cerrada" cuando en el caso legado la
conversación no cerró. Cosmético. Anotado en notas as-built de ADR-009.
Riesgo: [N8N], menor. Resolver junto con P18 si hay filas legacy.

### P9 · Microfixes n8n
**Qué**: `latency_ms` real en "Validate and Prepare Action" (hoy 0 hardcodeado); evaluar
subir el `slice(-10)` a los 20 mensajes que el backend ya manda (hoy descarta la mitad del
historial disponible).
**Se despacha junto con P5** para tocar el workflow vivo una sola vez, con export antes y
después al repo.
Riesgo: [N8N], bajo.

### P20 · Documentación fuera de git
**Qué**: `CLAUDE.md` y la carpeta `n8n_workflow/` están gitignored. Conocimiento
operacional valioso sin versionar, acumulándose. Decidir conscientemente: ¿versionar
(sacar de .gitignore) o aceptar que es efímero y mover lo durable a docs/?
**Avance parcial (2026-08-08)**: el registro canónico de frentes P se movió de `CLAUDE.md`
a este archivo justo por esta razón — la lista maestra no puede vivir fuera de git. Lo que
queda en `CLAUDE.md` es su espejo operativo. El resto de la decisión sigue abierto.
Riesgo: decisión, no código.

---

## 🔵 DISEÑO EN ESPERA — no implementar aún

### P21 · Rediseño del prompt/directive (flujo secuencial + tono)
**Qué**: cuando el `profile` ya tiene datos, el flujo re-pregunta campo por campo en vez
de confirmar en bloque ("¿tu dirección?" → "¿tu teléfono?" en turnos separados, aunque el
bot ya los tenga). El sistema no distingue "no tengo el dato, lo pido" de "lo tengo, lo
confirmo". Relacionado: tono robótico (6 migraciones sin resolverlo por prompt) y deuda #8.
**Insight de diseño**: reglas enterradas en el prompt de ~5.900 tokens se ignoran; lo que
el LLM obedece es el DIRECTIVE (corto, al frente). El rediseño debe mover reglas del muro
al directive, y colapsar checkpoints de confirmación cuando ya hay datos.
**Su propio proyecto**. Empieza por diagnóstico (qué se ignora, qué va al directive, qué se
degradó en 9 migraciones). Casos de uso: la conversación del 2026-08-01 y, sobre todo, la
venta del 2026-08-19: los medios de pago estaban en el prompt con instrucción explícita
("cliente confirma → compartes medios de pago") y el LLM **no los compartió en 23 turnos**,
ni ante la petición directa de la clienta; la dirección ofrecida espontáneamente se ignoró
por perseguir el slot de turno; y la pregunta-permiso ("¿Te gustaría que lo dejemos en…?"
sobre algo que la clienta acababa de pedir) precedió al abandono del pedido del Lodge.
**Adelanto quirúrgico permitido sin abrir P21**: mover los medios de pago al directive
cuando `user_confirmed` esté cerca es una regla con dinero en tránsito, análoga a la
captura de opt-in de P26 — barata hoy, cara de esperar. Decidirlo explícitamente.
**Frente hermano, deliberadamente separado**: P13 (conocimiento curado de café). P21 es
ESTRUCTURA del prompt (qué va al directive, cuándo confirmar en bloque); P13 es CONTENIDO
de dominio (qué sabe el bot sobre café). Se tocan en el mismo archivo de prompt y conviene
coordinarlos, pero son decisiones distintas y no se cierran juntos.
**Hacer después de P22**: rediseñar el prompt sin poder ejercer el flujo completo sin LLM
es volver a iterar a ciegas, que es exactamente lo que produjo 6 migraciones de tono sin
resultado.
Riesgo: [ADR] o [DB] migración de prompt. NO mezclar con fixes de comportamiento.

### P13 · Conocimiento curado de café en el prompt
**Qué**: el bot no tiene conocimiento de dominio curado sobre café (origen, tueste, molienda,
maridaje). Responde con lo que el modelo base sabe, sin criterio del negocio.
**Origen**: pendiente sin cerrar del stand público 2026-06-16 — ver
`docs/postmortems/postmortem-2026-06-16-stand-publico.md`. Usuarios reales preguntaron cosas
que el bot contestó de forma genérica o incorrecta.
**Bloqueado por una decisión de producto, no técnica**: hay que decidir qué debe saber el bot
y con qué voz, y eso lo define el negocio, no el código.
**Frente hermano**: P21 (rediseño del prompt/directive) — ver la nota de separación allí.
Riesgo: [DB] migración de prompt cuando se decida el contenido.

### P23 · Resume asíncrono por checkpoint
**Qué**: `human_handoff` deja de ser casi-terminal y pasa a ser un estado de espera
explícito con reanudación por checkpoint en Postgres. El resultado externo entra por un
endpoint que escribe el estado y **retorna sin llamar al LLM**; el siguiente mensaje del
usuario reanuda de forma transparente.
**Origen**: `docs/north-star.md` §1 (patrón `WAITING_FOR_BACKGROUND_CHECK` del accelerator
de Databricks). Registrado aquí para no perderlo; el archivo north-star sigue siendo
contexto de dirección, no plan.
**Por qué**: hoy mitigamos REACTIVAMENTE la ventana entre Call-1 y Call-2 con
`strategy_version` (409 → hoy drop silencioso, ver P5). El patrón de checkpoint es
estructuralmente inmune: no hay ventana sostenida por una llamada en vuelo. WhatsApp ya es
turn-based y desconectado; el modelo "pausar y reanudar en el siguiente mensaje" le queda
natural. `strategy_version` se conserva como red de seguridad, no como mecanismo principal.
**Precondición de P7, no sucesor**: si el debounce se rediseña sobre el modelo temporal
actual y después adoptamos esto, se rehace. Decidir P23 (sí/no) ANTES de escribir el ADR
de P7. Es una decisión, y puede resolverse en una sesión de razonamiento sin código.
**NO implica migrar a LangGraph** (north-star §"Lo que NO copiamos"): se roba el vocabulario
de estado explícito, no el runtime.
Riesgo: [ADR] + [B] + posible [DB]. NO implementar aún.

### P25 · Transcripción de notas de voz (bloqueado por P16)
**Qué**: nota de voz entrante → ASR → texto que entra al pipeline como `content` normal,
para que el bot pueda atender a quien prefiere hablar antes que escribir (mayoría del uso
real de WhatsApp en LATAM).
**Bloqueado por P16**: P16 decide si el medio LLEGA al backend (contrato de ingest,
descarga desde Chakra, persistencia, retención). P25 decide qué se hace con los bytes una
vez llegan. Sin P16 no hay audio que transcribir. Misma separación que P21/P13:
infraestructura vs. capacidad.
**Reglas no negociables** (mismo principio que ADR-001/002 y P11/P15):
- El transcript es texto de usuario NO confiable. Entra sujeto a `OPERATOR_ONLY_FIELDS`
  igual que cualquier mensaje. Una transcripción **nunca** marca `payment_confirmed` ni
  `user_confirmation`. Es un canal más ruidoso sobre la misma superficie ya vulnerada dos
  veces.
- Persistir el transcript en `messages` marcado como derivado (p. ej.
  `content_source='asr'`), no confundible con lo que el cliente efectivamente escribió.
**Agrava P7**: descarga del medio + ASR añade 2–5 s de latencia variable. El debounce ya
está anclado al timestamp de WhatsApp y ya se rompe con medios lentos; el audio convierte
un problema intermitente en sistemático. El ADR de P7 debe contemplarlo.
**Decisiones abiertas**: ¿se guarda el audio original o solo el transcript? (la voz es PII
más sensible que el texto). ¿ASR propio o el que exponga Chakra? **Default propuesto**:
transcript sí, audio original no; retención del transcript igual a la de `messages`.
Riesgo: [B] + costo por minuto de audio + decisión de retención (PII).

### P27 · Motor de campañas outbound (remarketing a N días)
**Qué**: cliente que preguntó por el café y no cerró → mensaje de re-enganche a los N días.
**Bloqueado por P24 y P26**: sin P24 no existe el dato "carrito abandonado" (el carrito
muere con la conversación de 24h); sin P26 no hay a quién es legal escribirle.
**Alcance real** (no es un cron): catálogo de plantillas aprobadas por tenant, scheduler,
ventana horaria legal en zona horaria del DESTINATARIO, idempotencia de envío (una campaña
nunca dos veces al mismo contacto — hermano de P6), supresión por opt-out y RNE, tope de
frecuencia por contacto, y atribución (¿la campaña cerró la venta o no?). Sin atribución no
sabemos si funciona y el gasto es fe.
**Costo**: categoría *marketing* sin escapatoria (Meta clasifica por intención promocional;
lenguaje de re-enganche cae en marketing aunque el formato parezca utility). ~USD 0.02 por
mensaje entregado a Colombia, **sin descuento por volumen** en esta categoría — a
diferencia de utility, que en Colombia está cerca de USD 0.001. Mil mensajes ≈ 20 USD más
el markup de Chakra. **El dinero no es el riesgo.**
**El riesgo real es destino compartido en el número**: se paga por mensaje entregado que el
usuario luego marque como spam, y eso golpea el quality rating del **mismo número que
atiende la venta**, con tope de volumen futuro. Una campaña mal segmentada degrada el canal
de venta principal. Si en el futuro varios tenants comparten número, un tenant quema a
todos: **decidir la topología de números antes de la primera campaña, no después.**
**Alerta de economía a verificar con Chakra (ver P14)**: varios BSP reportan que desde el
2026-10-01 los mensajes free-form de servicio pasan a ser facturables al rate de utility,
incluso dentro de la ventana de 24h. La documentación pública de Meta consultada el
2026-08-17 (actualizada 2026-03-30) todavía los lista como gratuitos, así que **no está
confirmado** — pero si aplica, cambia el costo unitario de CADA conversación del bot, no
solo el de las campañas. Preguntar junto con lo de LID.
**PRIMERA VERSIÓN SIN MOTOR (recomendada)**: reusar el lazo de operador de ADR-009. Reporte
diario a Telegram con los intents abandonados; el operador escribe a mano. Cero
infraestructura, cero exposición regulatoria automatizada, y valida si el remarketing
convierte ANTES de construir el motor. Si no convierte a mano, tampoco convierte
automatizado y nos ahorramos el frente completo.
Riesgo: [ADR] + [DB] + [B] + [N8N] + exposición regulatoria.

### P10 · Detección de conversaciones no-humanas + estancamiento del DAG
**Qué**: bot-a-bot accidental (caso LATAM) y abuso deliberado (scrapers que queman tokens).
Señales: velocidad de respuesta inhumana (la más barata/potente), turnos sin progreso del
DAG, repetición semántica, volumen anómalo. Modelo de SCORE que suma señales → escala a
handoff (reusa el mecanismo existente). El LLM nunca decide "es un bot"; el backend sí.
**Absorbe el detector de loop de texto VARIABLE** — el que P8 no cubre, porque su trigger es
comparación exacta de texto. Ese detector se registró en su momento como "candidato P9"
(número ya ocupado por los microfixes n8n) y también como el remanente de `deuda #10`: un
solo frente con dos identificadores, ninguno válido. Aquí queda unificado como P10, porque
"turnos sin progreso del DAG" y "repetición semántica" ya eran dos de sus señales. Detalle
del caso no cubierto: `docs/registros/registro-P8-limitaciones.md`.
**Estado**: diseñado, no implementar aún. Excepción posible: la señal de velocidad si la
amenaza se materializa. P8 (circuit breaker por 3 idénticos) ya cubre el caso trivial.
**ADR-010 pendiente de escribir**: este roadmap lo daba por "diseñado", pero no existe
ningún `ADR-010-*.md` en `docs/decisions/` (el índice llega hasta 009). El diseño vive en
esta entrada; falta convertirlo en decisión escrita antes de implementar.
Riesgo: [ADR] **por escribir**, [B] cuando se implemente.

---

## ✅ CERRADO Y VERIFICADO

- **P11 · Venta duplicada** (2 caminos marcaban pago; se desactivó el camino LLM→payment en 3
  superficies: backend, prompt DB migración 011, n8n). Verificado en prod 2026-08-01:
  una venta, autor operador, cero fantasmas del LLM. Fix con verificación por mutación.
- **P8 · Circuit breaker** (3 outbounds idénticos consecutivos → human_handoff).
- **P3 · Gate de payment_confirmation permeable** (recálculo tras el gate de user_confirmation).
  **Superseded by ADR-009**: hoy el `payment_confirmation` del LLM se descarta siempre
  (`OPERATOR_ONLY_FIELDS`), así que el escenario del gate ya no puede existir. Los tests se
  conservan como regresión.
- **P2 · ORDER_FIELDS** (quantity/grind/roast se persisten; registro de compra con quantity/total).
- **P4 · Observabilidad de compaction** (dejó de fallar en silencio; causa raíz aún pendiente
  de leer del log de prod — sub-abierto menor).
- **ADR-008 · Multiidioma + teléfono** (detección de idioma en backend; validación E.164-laxa).
- **ADR-009 · Lazo de handoff** (endpoint confirm-payment + auth escopada + Telegram +
  corte de respuesta n8n + registro de venta + cierre a closed). Probado e2e.
- **P12 · Slot perdido / captura de ORDER_FIELDS** en el directive (oportunista, no bloqueante).
- **Infra · minReplicas 0→1** (eliminó cold starts que perdían mensajes).
- **P1 · Drift de docs** (CLAUDE.md, n8n CLAUDE.md sincronizados con la realidad).

---

## Orden sugerido de cierre (revisable)

Criterio único: **un sistema de ventas que funcione**. Primero deja de perder clientes,
después deja de mentir sobre el estado de la venta, después baja el costo de verificar, y
solo entonces agrega capacidad nueva. Nada de P22–P27 se abre antes del punto 5.

1. ~~**P14** (LID/privacidad)~~ — ✅ CERRADO, verificado e2e (08-18) y con cliente real
   (08-19). Quedan las preguntas a Chakra anotadas en la entrada (medios, facturación
   2026-10-01) — hacerlas junto con P16.
2. **Fix puntual deuda #13** (contrato de respuesta del debounce) — un return + un test.
   NO es abrir P7: es dejar de responder 500 en cada ráfaga de mensajes. Hoy ocurre en
   toda conversación de cliente rápido (4 veces en la venta del 08-19).
3. **P15** (user_confirmation) — hermano del bug cerrado; diagnóstico primero. Su evidencia
   ya no es hipotética (aviso falso al operador el 08-19). Incluye decidir si el gate exige
   `product_id` (H6 del postmortem).
4. **Decidir P28 y P29** (una sesión de razonamiento, sin código): P28 porque el punto de
   pago quedó demostradamente sin gobierno (la "llave 1234"); P29 porque el operador ya
   está interviniendo en vivo y el bot no se entera. Si no se abren ya, al menos decidir la
   mitigación quirúrgica de P21 (medios de pago al directive) y dejarla hecha.
5. Decidir si **P16** (medios ciegos) entra ya. Dato nuevo: 15,6% del inbound histórico es
   invisible, y hoy costó la identificación del producto de una venta real, no solo el
   comprobante.
6. Intercalar **P17** (código muerto), ~~P18~~ (diagnóstico ✅ hecho 08-19; queda el UPDATE
   puntual del e2e), **P19**, y **P5+P9** juntos en un solo toque del workflow — cortos,
   bajo riesgo.
   *Intercalable aquí también*: la captura mínima de opt-in comercial (ver P26). Barata
   hoy, imposible retroactivamente. No es abrir P26; es no perder la base instalada.
7. **P22** (motor sin LLM) — el primer frente "nuevo" que se abre. Va aquí y no después
   porque a partir de este punto todo lo que sigue (P21, P24, P7) toca el hot path, y
   verificar a mano contra producción deja de ser aceptable.
8. **P24** (`purchase_intents`) — cierra el agujero de la venta que se pierde entre
   conversaciones. Es el bug de producto más visible que queda tras endurecer el flujo, y
   además desbloquea P27. Evidencia fresca: el carrito de 4 bolsas del Lodge (08-19).
9. **P21** (rediseño prompt) como proyecto propio, con P22 ya disponible. **P13** cuando
   el negocio decida el contenido.
10. **P23** como DECISIÓN (una sesión de razonamiento, sin código; incorporar la evidencia
    de P29) → luego **P7** (debounce) y **P6** (idempotencia), cada uno con su ADR.
11. **P26** completo y **P27** (campañas) — empezando por la versión manual vía operador.
    No construir motor hasta que el remarketing manual demuestre que convierte.
12. **P10** (bots) cuando la amenaza se materialice, o antes si aparece abuso real.

**P25** (audio) no tiene posición fija: entra cuando P16 cierre y el negocio confirme que
los clientes reales mandan notas de voz. Si el dato existe en `messages`, mirarlo antes de
decidir.

---

## Registro canónico de frentes P

**Esta tabla es la fuente de verdad de qué número está tomado.** Antes de asignar un P nuevo,
mirar aquí. La tabla de `CLAUDE.md` es un espejo operativo, no la autoridad.

| P | Frente | Estado | Remedia |
|---|---|---|---|
| P1 | Sincronizar documentación con la realidad | ✅ | — |
| P2 | Persistir `quantity`/`grind`/`roast` (ORDER_FIELDS) | ✅ | — |
| P3 | Cerrar gate permeable de `payment_confirmation` | ✅ *Superseded by ADR-009* | — |
| P4 | Resucitar la lazy-compaction + hacerla ruidosa | 🟡 causa raíz pendiente | deuda #3, #7 |
| P5 | Manejo de errores 409/5xx en n8n | ⬜ | deuda #12 |
| P6 | Idempotencia outbound (texto e imagen) | ⬜ [ADR] | deuda #11 |
| P7 | Debounce: race + conexión ocupada | ⬜ [ADR] | deuda #2 |
| P8 | Circuit breaker para loops conversacionales | ✅ | — |
| P9 | Microfixes n8n (`latency_ms`, `slice(-10)`) | ⬜ | — |
| P10 | Detección de conversaciones no-humanas + estancamiento del DAG | 🔵 [ADR-010 por escribir] | deuda #10 (remanente) |
| P11 | Fix venta duplicada — operador única autoridad del pago | ✅ | — |
| P12 | Captura oportunista de ORDER_FIELDS en el directive | ✅ | — |
| P13 | Conocimiento curado de café en el prompt | 🔵 decisión de producto | — |
| P14 | Mensajes con LID/privacidad se pierden en silencio | ✅ verificado e2e + cliente real 2026-08-19 | deuda #3 (parcial) |
| P15 | `user_confirmation` por interpretación del LLM | 🔴 | — |
| P16 | Medios entrantes con content vacío (imagen y audio) | 🔴 [ADR] | — |
| P17 | Barrido de código muerto post-P11 | 🟢 | — |
| P18 | Diagnóstico de datos legacy | 🟢 | — |
| P19 | Mensaje engañoso de Telegram (caso legado) | 🟢 | — |
| P20 | Documentación fuera de git | 🟢 | — |
| P21 | Rediseño del prompt/directive (flujo + tono) | 🔵 | deuda #8 |
| P22 | Motor ejecutable sin LLM (stubs + escenarios como datos) | 🟡 | deuda #1 |
| P23 | Resume asíncrono por checkpoint | 🔵 [ADR] | — |
| P24 | `purchase_intents` — venta que sobrevive la conversación | 🟡 [ADR][DB] | deuda #7 (parcial) |
| P25 | Transcripción de notas de voz | 🔵 bloqueado por P16 | — |
| P26 | Consentimiento, canal autorizado y opt-out | 🟡 [ADR][DB] | — |
| P27 | Motor de campañas outbound (remarketing) | 🔵 bloqueado por P24, P26 | — |
| P28 | Gobierno de datos operativos en el NLG (llave/medios de pago inventados) | 🔴 registrado, no abierto | — |
| P29 | Presencia de operador — el bot no se calla cuando el humano atiende | 🔴 registrado, no abierto | deuda #14 |

**Siguiente número libre: P30.**

---

## Notas históricas de numeración

Dos números se reutilizaron antes de que existiera esta regla. **No se renumeran** — los
documentos que los mencionan son registros inmutables y reescribirlos falsearía el archivo.
Se anotan aquí para que quien lea un documento viejo sepa interpretarlo.

**`P4` en abril de 2026 ≠ P4 (compaction).** La migración
`migrations/versions/006_p4_reduce_robotic_tone.sql`, la rama `prompt/p4-reduce-robotic-tone`
y el commit `prompt(006): reduce robotic tone — P4 surgical edits` (2026-04-20) usan una
numeración anterior, sin relación con el P4 de este registro. La migración está aplicada en
producción; renombrarla sería riesgo sin beneficio.

**`DEUDA #10` cambió de significado el 2026-06-14.** Antes designaba el "reset por idle
30 min" que `CLAUDE.md` documentaba pero que nunca existió en código; eso se cerró con P1.
Después de esa fecha, `#10` designa el corte de n8n post-handoff (hoy resuelto por ADR-009,
con el loop de texto variable como remanente, absorbido por P10). Los documentos de junio
—`brief-audit-2026-06-14-estado-y-plan.md` y `auditoria-2026-06-14-estado-y-plan.md`— usan
el sentido viejo.

**Dónde nacieron P1–P9.** El plan original está en
`docs/postmortems/auditoria-2026-06-14-estado-y-plan.md` §8. Ese documento es el registro
histórico de cómo se priorizó en junio; **ya no es la fuente definicional**. Tenerla enterrada
en un postmortem inmutable fue justamente lo que permitió que P9 se asignara dos veces.

**`purchase_intents` vivió sin número P hasta el 2026-08-17.** Fue citado como pendiente en
ADR-004 §51, ADR-005 §57, ADR-008 §17, ADR-009 §11 y `CLAUDE.md:300`, y en ADRs viejos se
le reservó informalmente el número "ADR-008" —número que después tomó otra decisión (ver la
nota de numeración en `ADR-008-idioma-y-telefono-e164.md`). Un frente citado cinco veces y
priorizado cero veces: el modo de fallo que la notación existe para evitar. Hoy es **P24**,
y de ahí sale la regla añadida en "Notación": si algo aparece dos veces en prosa, dale un
número.

**P22, P23, P25, P26 y P27 se registraron el 2026-08-17 sin abrirse.** P22 y P23 vienen de
`docs/north-star.md` (§2+§3 y §1 respectivamente); P25 y P27 vienen de ideas de producto
(audio, remarketing) traídas ese día; P26 apareció al analizar qué hacía falta para que P27
fuera legal en Colombia. Ninguno se implementa: se registraron para que el trabajo en curso
(P14) no compita con ideas nuevas por espacio en la cabeza. `north-star.md` conserva su
regla de solo-lectura; tener número P no lo convierte en plan.

**P28 y P29 se registraron el 2026-08-19 sin abrirse.** Ambos salen del postmortem de la
venta real BSUID (`analisis-2026-08-19-venta-bsuid-colision-operador.md`): P28 de la
alucinación "llave 1234" en el punto de pago; P29 de la colisión bot/operador durante la
intervención manual. El mismo análisis agregó las deudas #13 y #14 a `CLAUDE.md`, cerró el
diagnóstico de P18 y amplió el alcance documentado de P15 (gate sin `product_id`) y P16
(allowlist de `message_type`; 15,6% del inbound invisible).
