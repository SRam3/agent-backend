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
> Última actualización: 2026-09-22, tarde (análisis read-only de producción: P29 cerrado,
> evidencia de P15 y P31, medios de pago sube al primer lugar, deudas #21–#23).
> Historial de actualizaciones anteriores: `docs/registros/roadmap-historico.md`.

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
- Frentes nuevos toman el siguiente número libre — hoy, **P34**.
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
**Estado (2026-09-01)**: el diagnóstico está hecho y la decisión escrita. `ADR-010` vive en la rama
`feat/adr-010-backend-gobierna-resumen` con `services/order_summary.py`, la migración `013` y sus
tests: el backend renderiza el resumen, calcula el total y solo acepta la confirmación si se cumplen
cuatro condiciones deterministas. **Estado del despliegue (2026-09-04)**: la migración se partió en dos (P33) y **el DDL ya está
aplicado en prod** — `013_add_order_summary_state.sql`, 02:09:28 UTC, `conversations` de 16 a 18
columnas, aditivo y nullable, con el código viejo corriendo sin novedad. El envío a Manizales a
$5.000 **queda confirmado por el negocio** (2026-09-03): no era un error de la migración, es una
bajada deliberada sobre la tarifa de abril. **Secuencia completa el 2026-09-04**: 013 (DDL)
02:09:28 UTC → merge y despliegue (PR #68, revisión `--0000058`) 02:47 → 014 (datos y prompt)
02:52, con el `system_prompt_template` pasando de 17.261 a 15.952 caracteres. ADR-010 pasó a
`Accepted`. **Verificado en prod (2026-09-22)**: 6 de 6 resúmenes con el total correcto; la
confirmación solo se aceptó después del resumen, y la compuerta bloqueó 2 confirmaciones prematuras
(09-11 y 09-13). **Falta solo la §6** (una corrección invalida y re-resume): nunca se disparó,
porque depende de que el LLM emita la corrección (P35; caso del 09-06). Regla de la auditoría §9: un frente que termina en "verificar X" no se cierra hasta
leer X.
Riesgo: [B] + [DB].

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
**Alcance REDUCIDO (2026-08-23), no cerrado.** La mitad barata está hecha en
`feat/allowlist-contenido-ilegible`: un mensaje sin contenido legible se persiste pero **no
genera turno** (`reason: "unreadable_content"`), así que el 15,6% de inbound ilegible dejó de
producir respuestas a ciegas. La regla mira el CONTENIDO, no el `message_type` — enumerar
tipos era un bug nuevo por cada tipo que Meta invente, así que el "allowlist de
`message_type`" que pedía el párrafo anterior quedó descartado como diseño.
**Lo que sigue abierto y sigue siendo el frente**: descarga de los bytes desde Chakra,
persistencia, y qué llega al pipeline conversacional. Dato duro nuevo para ese diseño
(diagnóstico 2026-08-22): la URL firmada de `lookaside.fbsbx.com` vive **301–302 s** desde el
`timestamp` del mensaje (medido en 8 payloads, `ext − timestamp`), sin `oe`/`oh` — la firma va
en `hash`. Cualquier diseño que guarde la URL en vez del binario guarda un enlace muerto.
También pendiente: el `caption` de imagen, que hoy no se extrae y por eso una imagen con
caption cae en "sin contenido" como todas.
**El frente no es solo "responder a ciegas": es CONTRADECIR (2026-08-26).** Con el guard puesto,
el bot ya no contesta a un medio ilegible — pero sigue sin verlo, así que opera sobre un diálogo
al que le falta lo esencial. Caso real: un cliente envió el comprobante de pago a las 17:37:27 y
**64 segundos después el bot le pidió el comprobante** ("Cuando realices el pago, no olvides
enviarme el comprobante"), disparado por un mensaje de agradecimiento posterior del propio cliente. El guard
funcionó (exec 11150: `reason: unreadable_content`, 177 ms, sin salida) y aun así el cliente vio
al bot desconocer su pago. **Silenciar el turno no evita este daño; solo bajar los bytes lo evita.**
Detalle en `docs/postmortems/diagnostico-2026-08-29-comprobante-ciego-y-direccion-perdida.md`.
**Re-medición (2026-09-01): el alcance estaba sobredimensionado.** Hoy son 58 de 359 inbound sin
contenido legible (16,2 %), pero **39 de esos 58 no son medios** (35 `unsupported`, 2 `reaction`, 1
`revoke`, 1 `edit`) y ya los resuelve el guard. Los medios son 19 mensajes, **5,3 %**, y **7 de las 14
imágenes pertenecen a un solo episodio bot-a-bot** (el del 08-15). El material real de
clientes en cinco meses es del orden de una docena de mensajes, sobre un catálogo de UN producto, y
ninguna de las imágenes retenidas trae `caption`.
**Y obliga a tocar el `master`, no solo `cafe_arenillo_v2`**: su `Set` whitelist
`map_webhook_data_arenillo` copia diez campos y **ninguno es el objeto `image`/`audio`**, así que la
URL de descarga nunca llega al sub-workflow. Ese nodo fue la causa primaria del drop de P14.
**La mitad útil de este frente se separó a P32** (placeholder de medios en el historial, 100 %
backend): evita que el bot contradiga al cliente sin abrir la descarga. Lo que queda aquí es bajar los
bytes, y es la mitad cara.
**Dentro del alcance al abrir: revisar el umbral del circuit breaker.** Cerrar P16 hace que
una imagen legible vuelva a generar turno, así que **restaura implícitamente la sensibilidad
del breaker** que el guard de contenido ilegible redujo (ver la nota de calibración en P8).
El umbral de 3 vuelve a contar como cuando se calibró; hay que decidir a conciencia si sigue
siendo el correcto en vez de que el cambio ocurra de rebote.
**Alcance**: grande. Probablemente [ADR] para decidir hasta dónde (¿solo registrar que
llegó media?, ¿pasarla al LLM?, ¿persistir el comprobante?, ¿cuánto se retiene?). Decidir
por separado.
Riesgo: [ADR] + [B] + [N8N].

### P5 · Alerta de fallo silencioso en n8n (remedia deuda #12)
**Qué**: cuando el backend falla, nadie se entera. Ni el operador, ni un log, ni Chakra.
**Corrección de mecanismo (2026-09-01)**: la versión anterior de esta entrada decía que un 409 «mata
la ejecución». **No es así.** `POST Agent Action` lleva `continueOnFail: true` igual que el ingest, de
modo que un 409 o un 5xx se convierten en un item con `error`, caen al `else` de `Process Backend
Response` como `suppressed_reason: "backend_error"`, y la ejecución termina marcada **`success`**. La
rama de error ya existe; lo que no existe es el aviso.
**Evidencia**: **cero** 409 en las 268 ejecuciones retenidas del pipeline. Lo que sí ocurrió son 5
respuestas 500 del ingest el 08-19 (execs 10452, 10566, 10600, 10612, 10650), todas dentro de
ejecuciones `success`: el modo de falla es real aunque el mecanismo estuviera mal descrito.
**Por qué sube a 🔴**: es la única forma de enterarse de que el sistema falló, y es **precondición de
P16** — bajar bytes introduce una descarga que puede fallar dentro de una ventana de 301 s y hoy no
hay dónde avisarlo.
**Alcance acotado, sin retry**: un IF sobre `backend_error` y sobre el item de error del ingest, un
aviso a Telegram reusando el canal de ADR-009, y `suppressed_reason` en el texto del aviso.
Reintentar un turno después de 5 s de debounce es otra decisión y no entra aquí.
**Trampa medida**: las únicas ejecuciones en error de la instancia son de un workflow ajeno
(`Predicción horaria → Telegram`, 7 fallos diarios desde al menos el 08-19). Una alerta basada en
`status=error` nace ahogada en ruido que no es del producto.
**Lado backend verificado (2026-09-22)**: una petición stale responde `409 stale_context`, con
rollback y sin escribir nada. Hay test y mutación (`INV-CONV-003`). Lo que falta es solo n8n.
**Se despacha junto con P9** para tocar el workflow vivo una sola vez, con export antes y después.
Riesgo: [N8N].

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

### P30 · Sin validación de dirección de envío (registrado 2026-08-26, NO abierto)
**Qué**: `services/validation.py` son 32 líneas con **una sola función**, `is_plausible_phone`
(ADR-008). No existe ninguna validación de `shipping_address`. En `agent_action.py` el campo aparece
solo en `STRATEGY_FIELDS` y en `_USER_CONFIRMATION_REQUIRES`: el gate exige que **esté presente**,
nunca que sea válido.
**Evidencia**: 2026-08-26 21:55:51, una clienta envió `<dirección>` — sin separadores, indespachable.
En ese caso concreto no llegó a persistirse porque el LLM ni siquiera la extrajo (ese es el problema
hermano, ver P12), pero de haberlo hecho se habría guardado tal cual y habría contado para
`user_confirmation`: la venta habría quedado lista para cerrar con una dirección a la que nadie puede
despachar.
**Por qué importa**: la dirección es el único dato del DAG cuyo error no se detecta hasta el despacho,
cuando ya se cobró. El teléfono tiene gate desde ADR-008; la dirección no tiene nada.
**Alcance a decidir al abrir**: qué significa "válida" para una dirección colombiana (¿forma?,
¿normalización?, ¿geocoding contra un proveedor?, ¿solo pedir confirmación al cliente?). Ojo con
repetir el error del gate de teléfono: valida FORMA, no veracidad, y eso se decidió a conciencia.
Probablemente basta empezar por normalizar y devolver el resumen para confirmación explícita.
Riesgo: [B] acotado si es solo forma; [ADR] si entra un proveedor externo.

### P31 · Silencio post-venta: el bot contesta al cliente que acaba de comprar (registrado 2026-09-01, cerrado el 09-05, 🔴 **REABIERTO el 2026-09-07**)
**Qué**: cerrar la venta deja la conversación en `closed`, y el siguiente mensaje del cliente abre una
conversación NUEVA en `active` que el bot contesta — justo mientras el operador atiende a mano. No es
que el bot se re-presente (el seed desde `profile` funciona y usa el nombre): es que **no sabe que
acaba de haber una venta** y no existe forma de callarlo.
**Evidencia (2026-08-26)**: `sale_closed` a las 17:38:38 UTC; el operador escribe por WhatsApp entre
17:38:09 y 17:40:01 (4 echoes, execs 11153, 11165, 11170, 11174); el cliente escribe un mensaje corto
a las 17:41:12 y el bot le contesta a las 17:41:22 desde una conversación nueva, en `v1`, sin
historial.
**Corrección al diagnóstico del 08-29**: registró dos ocurrencias, pero **solo una sigue siendo
alcanzable**. La del 08-19 la disparó un audio con `content` vacío, y hoy el guard de
contenido ilegible la suprime — verificado con el `revoke` del 08-29 (exec 11435: `should_respond:
false`, `reason: unreadable_content`, 0,12 s, sin llamada al LLM). La del 08-26 la disparó texto
legible: el guard no aplica ni podría.
**Por qué frente propio y no parte de P29**: es la mitad barata y de mayor confianza. Una ventana
temporal en el ingest ("si la última conversación de este cliente cerró hace menos de N, persistir y
suprimir") es backend puro, no toca n8n, y ataca el momento exacto en que el humano está escribiendo.
El mecanismo general de presencia de operador sigue siendo P29.
**Toca una consecuencia aceptada de ADR-009 §4** ("`closed` cierra la VENTA, no la RELACIÓN; el
siguiente mensaje abre una conversación nueva en `active`"). La decisión de cerrar sigue siendo
correcta; el supuesto de que ese mensaje siguiente es una próxima venta no lo es.

---

**Cerrado como decisión el 2026-09-05 y REABIERTO el 2026-09-07.** El cierre se apoyaba en que
P29 lo cubría entero. La evidencia del 09-07 dice que no, así que el frente vuelve. Conserva su
número porque es el mismo problema: la regla del repo prohíbe **reciclar** un número para otro
tema, no reabrir uno para el suyo.

**Por qué se rechaza la ventana temporal**: es un **proxy** de "hay un humano atendiendo", y el
proxy falla en los tres casos que importan.

1. El operador coordina el envío y escribe al cliente **60 minutos después** del cierre: la
   ventana ya expiró y la colisión vuelve.
2. El cliente agradece **tres días después**: fuera de cualquier ventana.
3. El operador se fue: la ventana calla al bot cuando **no hay nadie**, y el cliente queda sin
   respuesta y sin aviso.

El echo del operador no tiene los problemas 1 y 3 porque **se refresca solo**: cada mensaje del
operador extiende la pausa, sea a los 2 minutos o a las 3 horas. Eso sigue siendo cierto y sigue
siendo la razón de no construir una ventana temporal.

**Lo que el cierre del 09-05 dio por cubierto y NO lo está: el caso 2.** Que la señal se refresque
sola solo ayuda **mientras el operador siga escribiendo**. Un cliente que responde horas después,
cuando ya no hay nadie atendiendo, cae fuera de la pausa — y debe caer fuera, porque a esa hora
callar al bot sería dejarlo sin respuesta, que es justo el defecto 3 de la ventana temporal.

### Reapertura — la evidencia del 2026-09-07

Conversación `b313a570`, cerrada a las 22:34:53 del 09-06 con la venta registrada.

| Hora (UTC) | Qué pasó |
|---|---|
| 09-06 23:13:26–31 | El operador escribe al cliente. **Tres echoes descartados** por n8n en ~107 ms cada uno, marcados `success` (exec 12125/12127/12129) |
| 09-07 02:20:18 | El cliente responde «De una .gracias» — a un mensaje que el sistema nunca vio |
| 09-07 02:20:23 | La conversación estaba `closed`, así que el ingest **abre una nueva** (`b2bd2c3c`, `v1`) |
| 09-07 02:20:33 | El bot responde «¡Claro! ¿Qué cantidad de bolsas quieres esta vez?» |

**Dos cosas que este caso enseña y que el cierre no anticipó:**

1. **La pausa habría expirado, y con razón.** Tres horas y siete minutos entre el mensaje del
   operador y la respuesta del cliente, contra una ventana de 30 minutos. Subir la ventana no es
   la respuesta: sería reinventar el proxy que este frente rechazó.
2. **La conversación `closed` bifurca el diálogo.** El ingest exige `state != 'closed'`, así que
   la respuesta del cliente abre una conversación nueva, sembrada con sus datos pero sin pedido, y
   el DAG apunta a armar uno. De ahí sale la pregunta por la cantidad.

**Lo que sí aportará P29 aquí, y es parcial**: con la fase 4 hecha, el echo quedaría persistido en
la conversación cerrada, y la lazy compaction —que **sí corrió**, a las 02:20:23— lo habría
recogido en el resumen. El bot habría sabido que el operador confirmó el envío. No se calla, pero
deja de responder a ciegas.

**Y hay un agravante que no depende del operador**: la compaction produjo un resumen correcto que
decía que el cliente ya había comprado, pagado los $55.000 y que se coordinaría el envío. El bot
tenía esa memoria delante y aun así leyó «De una, gracias» como una compra nueva. El directive
pesó más que el resumen.

**Alcance al reabrir**: que la respuesta de un cliente a una conversación cerrada no arranque de
cero pidiendo un pedido nuevo. Toca la consecuencia aceptada de ADR-009 §4, así que probablemente
necesita ADR. **Medido 2026-09-22**: 6 de 10 ventas terminan con el cliente escribiendo en menos
de 48 h a una conversación nueva sin historial. El 2026-09-03 el bot le reabrió a un cliente un
pedido completo 24 min después de su compra y le pidió confirmarlo. Ya no espera a P29: los
echoes entran desde el 2026-09-15.
Riesgo: [ADR] + [B].

### P32 · Placeholder de medios en el historial (mitad útil de P16, registrado 2026-09-01)
**Qué**: `recent_messages` entrega `content: ""` cuando lo que llegó fue una imagen o un audio
(`ingest.py:419-434`), así que el LLM razona sobre un diálogo al que le falta el artefacto. Con el
guard, el bot ya no responde AL medio; sigue **contradiciendo** al cliente que lo mandó.
**Evidencia (2026-08-26)**: el cliente envía el comprobante de pago a las 17:37:27 y **64 segundos
después el bot le pide el comprobante** ("Cuando realices el pago, no olvides enviarme el
comprobante"), disparado por un mensaje de agradecimiento posterior del propio cliente.
**El fix no requiere bajar bytes**: un placeholder por tipo en el historial ("[el cliente envió una
imagen]", "[el cliente envió una nota de voz]") más una línea de directive cuando el medio llega
después de `user_confirmed`. Es 100 % backend, no toca el `master`, no toca la ventana de 301 s, y de
paso permite decir "no puedo escuchar notas de voz, ¿me lo escribes?" en vez de callar.
**Se separa de P16 a propósito**: aquella queda como la descarga de bytes, cara y con material real
del 5,3 % del inbound; esta es barata y ataca el daño que el guard no evita.
Riesgo: [B] acotado.

### P33 · Orden entre migración y despliegue (registrado 2026-09-01)
**Qué**: las migraciones se aplican a mano y el CI despliega solo al mergear a `main` cuando cambia
`sales_agent_api/**`. Nadie declara cuál de las dos cosas va primero, y para la 013 la respuesta es
**distinta según la sección**.
**Evidencia**: el ORM de la rama de ADR-010 declara `order_summary_fingerprint` y
`order_summary_sent_at` (`models/core.py:193-194`); producción **no tiene esas columnas** (verificado
2026-09-01). Si esa rama se mergea antes de aplicar la 013, cada `select(Conversation)` falla: el 100 %
de los turnos devuelve 500, `POST Ingest Message` lo traga por `continueOnFail`, y **todas las
ejecuciones se marcan `success`**. Es la deuda #12 amplificando un fallo total.
**Las tres secciones de la 013 tienen restricciones opuestas**: el DDL debe ir **antes** del
despliegue; la cirugía del `system_prompt_template` debe ir **después**, porque si el prompt deja de
enseñar a redactar el resumen y el código todavía no lo renderiza, nadie lo manda. El brief de ADR-010
dice "aplicar 013 y desplegar", en ese orden y sin distinguir secciones.
**Precedente que sí lo resolvió**: la 012 se diseñó retrocompatible a propósito y lo dejó escrito en su
encabezado.
**Alcance**: separar la 013 en dos migraciones (DDL en una, datos y prompt en la siguiente, numeración
secuencial como siempre) y escribir la regla en un ADR corto — cada migración declara si va antes o
después del despliegue, y por qué. El CI no cambia.
**✅ HECHO (2026-09-04)**: la regla es `ADR-012`; la 013 se partió en
`013_add_order_summary_state.sql` (DDL, ANTES) y `014_shipping_rules_and_summary_prompt.sql`
(datos y prompt, DESPUÉS), con el SQL movido sin tocar un byte y la suite en verde; el DDL quedó
aplicado en prod a las 02:09:28 UTC. **Ya no bloquea el merge de ADR-010.** Queda como frente
abierto solo para el resto de migraciones: el campo `-- Orden:` se exige de la próxima en adelante.
Riesgo: [ADR] + [DB].

---

## 🟡 ABIERTOS — deuda real, no bloquea hoy


### P34 · El echo mata el turno en vuelo (registrado 2026-09-05, NO abierto)

**Qué**: P29 fase 3 calla al bot **desde el turno siguiente**. Si el cliente escribe, el ingest
corre, n8n llama al LLM y **mientras tanto** el operador escribe, el `/agent/action` de ese turno
aprueba y envía igual. Es el resto de la deuda #14.

**Evidencia**: es exactamente el caso "llave 1234" del 2026-08-19, entre las 19:42 y las 19:45.
P29 lo deja **parcialmente descubierto**, y el ROADMAP lo dice así a propósito en vez de vender
P29 como resuelto.

**La solución existe y es elegante**: que el echo **incremente `strategy_version`**. ADR-003 creó
ese mecanismo justamente para invalidar contexto viejo, y un echo cambia el contexto de forma
material; el `/agent/action` en vuelo devolvería **409 stale** y n8n suprimiría el envío.

**Por qué no se hace ya, y es deliberado**: nunca ha ocurrido un 409 en la vida del sistema (cero
en 268 ejecuciones retenidas). Esto sería el primer productor real de 409, y hoy un 409 cae en el
`else` de `Process Backend Response` como `suppressed_reason: "backend_error"` —indistinguible de
un backend caído, y sin alerta—. Crear el primer 409 del sistema en el mismo PR que crea la
ingesta de echoes es lo contrario de baby-steps.

**Precondición: P5 vivo**, y un `suppressed_reason` propio para este caso.
Riesgo: [B] + [N8N].

### P35 · Medir cuándo el LLM narra un cambio sin emitirlo (registrado 2026-09-06, NO abierto)

**Qué**: ADR-010 §6 invalida la confirmación y re-resume cuando el pedido cambia, pero **la
detección del cambio sigue siendo del LLM**. El backend gobierna el resumen y el juicio de la
confirmación; no gobierna la extracción. Si el modelo describe una corrección en prosa y deja
`extracted_data` vacío, la §6 no tiene nada que invalidar y la venta se cierra sobre datos viejos,
en silencio.

**Evidencia (2026-09-06)**: conversación de prueba, conv `b313a570`. El cliente corrige la
dirección; el bot responde «apunto la dirección como Carrera 84F #3C-39, **Unidad** Campestre al
Parque»; el `extracted_data` de ese turno es `{}`; `extracted_context.shipping_address` sigue
diciendo «**Unidsd** Campestre al Parque», el fingerprint no cambia, `user_confirmation` sigue en
`true` y la venta cierra así. Ni un `side_effect`, ni un warning, ni una fila distinta. Es la nota
as-built de ADR-010 del mismo día.

**Este frente es INSTRUMENTACIÓN, no un cambio de formato.** El reflejo —pasar el chat call a
`json_schema` estricto, que es la deuda #8— puede ser la respuesta, pero hoy se apoyaría en **un
solo caso observado**, en una prueba, con el operador haciendo de cliente. Ese cambio toca el nodo
`Build LLM Prompt` del workflow vivo y el contrato de todas las respuestas del modelo. No se hace
sobre una anécdota.

**Qué medir, en orden de baratura**:
1. **La tasa de extracción vacía.** `messages.extracted_data` ya guarda lo que el modelo propuso
   en cada turno — con eso se puede medir hoy, sobre el histórico, sin desplegar nada. Empezar por
   ahí: cuántos turnos devuelven `{}`, y cuántos de esos caen **después** de
   `order_summary_sent_at`, que es la ventana donde una corrección no reportada hace daño real.
2. **Un evento propio cuando el turno no mergea nada habiendo resumen vigente.** Barato y
   determinista. Genera ruido en turnos benignos («ok», «gracias»), así que es señal para
   diagnóstico, no para alertar.
3. **Sólo entonces**, decidir con la cifra en la mano: si el esquema estricto se justifica, si
   basta una línea de directive cuando hay resumen vigente, o si el volumen no lo amerita.

**Por qué no cerrar el hueco «detectando la corrección» en el backend**: para saber que el cliente
corrigió un dato hay que leer lenguaje natural, y eso es el LLM. No hay versión determinista de esa
detección. Lo que sí es determinista es **notar que el modelo no reportó nada**, y eso es lo que se
instrumenta.

**Relación con P30**: son hermanos, no el mismo. P30 es que la dirección no se valida cuando llega;
P35 es que la corrección de una dirección puede no llegar nunca.
Riesgo: [B] acotado mientras sea medición. [N8N] el día que se toque el formato de salida.
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
**Evidencia nueva (2026-08-19)**: un pedido real de 4 bolsas (~$160.000) murió a mitad de flujo
tras una pregunta de permiso del bot. Hoy ese carrito
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
**✅ Deuda #13 CERRADA (2026-08-23)**, rama `feat/allowlist-contenido-ilegible`:
`build_suppressed_response()` construye la respuesta completa para los tres caminos de
supresión (`debounce`, `duplicate`, `unreadable_content`), y `reason` pasó a ser campo del
schema. Detalle que el diagnóstico del 2026-08-22 aclaró: el 500 no mataba la ejecución
porque `POST Ingest Message` lleva `continueOnFail: true` — el AxiosError se convertía en un
item sin `should_respond` y caía a la rama false de `IF Should Respond`. **La race de fondo
sigue abierta y sigue siendo de este ADR.**
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

---

## 🟢 ABIERTOS — higiene, intercalar entre fixes

### P17 · Barrido de código muerto tras el fix de venta duplicada
**Qué**: al desactivar el camino LLM→payment pudo quedar código sin uso más allá de lo ya
borrado (`_PAYMENT_CONFIRMATION_REQUIRES`, gate de pago). Barrer qué quedó colgando.
Riesgo: [B], bajo. Hacer entre fixes.

### P9 · Microfixes n8n
**Qué**: `latency_ms` real en "Validate and Prepare Action" (hoy 0 hardcodeado); evaluar
subir el `slice(-10)` a los 20 mensajes que el backend ya manda (hoy descarta la mitad del
historial disponible).
**Se despacha junto con P5** para tocar el workflow vivo una sola vez, con export antes y
después al repo.
**Dato nuevo (2026-09-01)**: el export del repo **ya no es el workflow vivo**. `cafe_arenillo_v2`
tiene `updatedAt: 2026-08-29T23:38:06Z` y `n8n_workflow/cafe_arenillo_v2.json` es del 08-18. El diff
funcional es nulo (dos nodos Telegram perdieron `resource`/`operation`, que n8n rellena por default),
pero el archivo que este roadmap declara «único respaldo, n8n no tiene historial de versiones» dejó de
ser byte-idéntico y no consta quién lo editó. **Re-exportar los tres workflows antes de tocar nada.**
Riesgo: [N8N], bajo.

### P20 · Documentación fuera de git
**Qué**: `CLAUDE.md` y la carpeta `n8n_workflow/` están gitignored. Conocimiento
operacional valioso sin versionar, acumulándose. Decidir conscientemente: ¿versionar
(sacar de .gitignore) o aceptar que es efímero y mover lo durable a docs/?
**Avance parcial (2026-08-08)**: el registro canónico de frentes P se movió de `CLAUDE.md`
a este archivo justo por esta razón — la lista maestra no puede vivir fuera de git. Lo que
queda en `CLAUDE.md` es su espejo operativo. El resto de la decisión sigue abierto.
**Avance parcial (2026-09-22)**: la tabla de deudas también se movió aquí (§ "Registro
canónico de deudas"); en `CLAUDE.md` quedó solo un puntero, sin espejo.
**Precisión (2026-09-22)**: `n8n_workflow/` no está gitignored: está fuera de todo repositorio, así
que sus exports y respaldos no tienen historial.
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
**Estado**: diseñado, no implementar aún. **La amenaza ya se materializó dos veces, y el breaker
bastó las dos** (verificado 2026-09-01): el bot de vuelos del 07-18 y un bot de soporte de
telecomunicaciones el 08-15 (28 inbound en 4 minutos, 16 outbound, breaker a las 14:06:28 UTC). Dato que encoge el frente todavía más: **23 de esos 28 inbound eran ilegibles**
(16 `unsupported`, 7 `image`), así que con el guard de contenido ilegible —vivo desde el 08-23— ese
episodio produciría 5 turnos en vez de 28. El guard desactivó la mayor parte del único caso observado
de P10 sin proponérselo.
**Su ADR sigue por escribir, y ya NO es el 010.** El número 010 lo tomó el ADR del resumen del pedido
(`ADR-010-backend-gobierna-resumen.md`, frente P15, escrito el 2026-08-23 en la rama
`feat/adr-010-backend-gobierna-resumen`, pendiente de merge). Se aplica el precedente de ADR-008: el
ADR que efectivamente se escribe toma el número. P10 tomará el siguiente libre cuando se escriba; el
diseño vive por ahora en esta entrada.
Riesgo: [ADR] **por escribir**, [B] cuando se implemente.

---

## ✅ CERRADO Y VERIFICADO

Una línea por frente. El texto completo, con evidencia, está en `docs/registros/roadmap-historico.md`.

- **P1** · drift de docs sincronizado.
- **P2** · `ORDER_FIELDS` persisten; registro de compra con quantity/total.
- **P3** · gate de pago permeable. Superseded by ADR-009.
- **P4** · compaction observable; causa raíz = clave de OpenAI. Verificado en prod el 2026-09-12 (4 resúmenes).
- **P8** · circuit breaker. Ojo: desde el guard de contenido ilegible es menos sensible (nota en el histórico, afecta a P16).
- **P11** · venta duplicada. Verificado en prod el 2026-08-01.
- **P12** · captura oportunista de `ORDER_FIELDS`. El mismo descarte con campos del DAG sigue vivo: ver P30.
- **P14** · LID/privacidad (BSUID). Verificado con clienta real el 2026-08-19.
- **P18** · datos legacy: la limpieza ya estaba hecha (verificado 2026-09-01).
- **P19** · cerrado sin implementar: no existen filas legadas (verificado 2026-09-01).
- **P29** · presencia de operador, acotado a echoes. Primer echo real y primera pausa en prod el 2026-09-15. El turno en vuelo sigue abierto como P34.
- **ADR-008** · multiidioma + teléfono E.164-laxo.
- **ADR-009** · lazo de handoff, probado e2e.
- **Infra** · `minReplicas` 0→1.

---

## Orden sugerido de cierre (revisable)

Criterio único: **un sistema de ventas que funcione**. Primero deja de perder clientes, después deja
de mentir sobre el estado de la venta, después baja el costo de verificar, y solo entonces agrega
capacidad nueva.

**Revisado el 2026-09-22** con el análisis read-only de producción. Medios de pago sube al primer
lugar: falla en 5 de 6 ventas y usa el patrón ya probado de ADR-010. P29 salió de la lista
(cerrado). La lista anterior y la revisión del 2026-09-01 están en el histórico.

1. **Medios de pago gobernados por el backend**, como sección nueva de ADR-010 en lugar de abrir
   P28. En 5 de 6 ventas desde el 2026-09-06 el bot pidió el comprobante antes de dar los medios de
   pago, y en 4 nunca los dio. El backend ya conoce el momento exacto: la transición `user_confirmed`.
2. **P31**: 6 de 10 ventas terminan en una conversación nueva sin historial. El rechazo de la
   ventana temporal sigue en pie; lo que falta es el hueco de la conversación cerrada.
   Probablemente ADR.
3. **P15**: solo falta observar la §6 en producción. Depende de P35.
4. **P5 + P9** en un solo toque del workflow vivo, con **re-export de los tres workflows antes y
   después**.
5. **P32** (placeholder de medios en el historial): backend puro, cierra "pidió el comprobante que
   ya tenía" sin abrir la descarga de bytes.
6. **P17** y decisión de **P20**. Sesión corta, entre fixes.

Después de que P5 exista: **P34** (el echo mata el turno en vuelo). No antes — sería el primer
productor real de 409 del sistema, y hoy un 409 es indistinguible de un backend caído.

Después, sin fecha ni compromiso: **P21** con la compaction ya viva y el prompt recortado por la
migración de ADR-010; decisión de **P24** con datos reales de `pending_intent`, que existe desde P2 y
nunca ha corrido; **P22** re-alcanzado a tests de integración contra Postgres en CI; **P16 bytes**
solo si el negocio exige el comprobante dentro del sistema; **P26** y **P27** empezando por la versión
manual vía operador.

### Frentes que NO se abren, y qué se hace con ellos

Auditoría 2026-09-01 §11. El sesgo que más ha costado es acumular diagnóstico más rápido de lo que se
cierra trabajo; esta tabla es el contrapeso.

| Frente | Qué se hace | Razón |
|---|---|---|
| **P28** | **fusionar** en ADR-010 | es la misma decisión sobre otro dato operativo; el título del ADR ya dice "y los datos operacionales" |
| **P30** | **fusionar** en ADR-010 | el caso del 08-26 fue de extracción (familia P12); el resumen con confirmación explícita es la validación que el negocio necesita hoy |
| **P23** | **decidir "no por ahora"** y cerrar como decisión | cero 409 en 268 ejecuciones: el problema que resuelve no ha producido un fallo observado. Desbloquea el ADR de P7 sin rediseñar nada |
| **P7** | **posponer**; extraer solo el microfix del mismo segundo | sin carreras desde el fix del 08-23; el caso texto→imagen ya lo mató el lookahead por contenido |
| **P6** | **posponer** hasta que exista outbound business-initiated | lo urgente de su alcance (wamid de salida) entra con P5 |
| **P22** | **re-alcanzar** a tests de integración contra Postgres | los 254 tests existentes ya son puros; los fallos que dolieron vivieron en las costuras con Postgres y n8n, que un motor con stubs no ejercita |
| **P25** | **posponer** | 5 audios en toda la historia, dos de ellos de prueba; P32 da la salida amable |
| **P26** (captura mínima de opt-in) | **no intercalar** | capturar consentimiento por extracción del LLM es el bug de P15 otra vez, sobre un dato con consecuencia regulatoria |
| **P16** (bajar bytes) | **posponer**; su mitad útil es P32 | 5,3 % del inbound es media real, sobre un catálogo de un producto, y obliga a tocar el `master` |
| **P10** | **aparcar** | la amenaza ocurrió dos veces y el breaker bastó; el guard desactivó 23 de los 28 inbound del caso del 08-15 |
| **P13**, **P27** | sin cambio | decisión de negocio, y versión manual antes que motor |

---

## Registro canónico de frentes P

**Esta tabla es la fuente de verdad de qué número está tomado.** Antes de asignar un P nuevo,
mirar aquí. La tabla de `CLAUDE.md` es un espejo operativo, no la autoridad.

| P | Frente | Estado | Remedia |
|---|---|---|---|
| P1 | Sincronizar documentación con la realidad | ✅ | — |
| P2 | Persistir `quantity`/`grind`/`roast` (ORDER_FIELDS) | ✅ | — |
| P3 | Cerrar gate permeable de `payment_confirmation` | ✅ *Superseded by ADR-009* | — |
| P4 | Resucitar la lazy-compaction + hacerla ruidosa | ✅ secreto rotado el 2026-09-04 y cargado por la revisión `--0000058`; **verificado en prod el 2026-09-12**: 4 resúmenes persistidos | deuda #3, #7 |
| P5 | Alerta de fallo silencioso en n8n (409/5xx/500) | 🔴 mecanismo corregido 2026-09-01; **precondición de P34** | deuda #12 |
| P6 | Idempotencia outbound (texto e imagen) | ⬜ [ADR] | deuda #11 |
| P7 | Debounce: race + conexión ocupada | ⬜ [ADR] | deuda #2 |
| P8 | Circuit breaker para loops conversacionales | ✅ | — |
| P9 | Microfixes n8n (`latency_ms`, `slice(-10)`) | ⬜ se despacha con P5. `ai_latency_ms` = 0 en el 100% de los turnos (2026-09-22) | — |
| P10 | Detección de conversaciones no-humanas + estancamiento del DAG | 🔵 [ADR por escribir, ya no el 010] | deuda #10 (remanente) |
| P11 | Fix venta duplicada — operador única autoridad del pago | ✅ | — |
| P12 | Captura oportunista de ORDER_FIELDS en el directive | ✅ | — |
| P13 | Conocimiento curado de café en el prompt | 🔵 decisión de producto | — |
| P14 | Mensajes con LID/privacidad se pierden en silencio | ✅ verificado e2e + cliente real 2026-08-19 | deuda #3 (parcial) |
| P15 | `user_confirmation` por interpretación del LLM | 🟡 verificado en prod 2 de 3 (2026-09-22); falta la §6, depende de P35 | — |
| P16 | Medios entrantes con content vacío (imagen y audio) | 🟡 [ADR] alcance re-medido 2026-09-01: la media real es 5,3 % del inbound; queda la descarga de bytes, que toca el `master` | deuda #13 ✅ |
| P17 | Barrido de código muerto post-P11 | 🟢 | — |
| P18 | Diagnóstico de datos legacy | ✅ | — |
| P19 | Mensaje engañoso de Telegram (caso legado) | ✅ cerrado sin implementar | — |
| P20 | Documentación fuera de git | 🟢 | — |
| P21 | Rediseño del prompt/directive (flujo + tono) | 🔵 | deuda #8 |
| P22 | Motor ejecutable sin LLM (stubs + escenarios como datos) | 🟡 | deuda #1 |
| P23 | Resume asíncrono por checkpoint | 🔵 [ADR] | — |
| P24 | `purchase_intents` — venta que sobrevive la conversación | 🟡 [ADR][DB] | deuda #7 (parcial) |
| P25 | Transcripción de notas de voz | 🔵 bloqueado por P16 | — |
| P26 | Consentimiento, canal autorizado y opt-out | 🟡 [ADR][DB] | — |
| P27 | Motor de campañas outbound (remarketing) | 🔵 bloqueado por P24, P26 | — |
| P28 | Gobierno de datos operativos en el NLG (llave/medios de pago inventados) | 🔴 registrado, no abierto | — |
| P29 | Presencia de operador — el bot no se calla cuando el humano atiende | ✅ cerrado 2026-09-22: primer echo real y primera pausa en prod el 2026-09-15. El turno en vuelo es P34 | deuda #14 (mitad de presencia) |
| P30 | Sin validación de dirección de envío | 🟡 registrado — se propone fusionar en ADR-010 | — |
| P31 | Silencio post-venta (el bot contesta a quien acaba de comprar) | 🔴 **REABIERTO 2026-09-07**. Se cerró el 09-05 dando por hecho que P29 lo cubría entero; la evidencia del 09-07 (conv `b313a570` → `b2bd2c3c`) muestra que no cubre al cliente que responde horas después a una conversación cerrada. El rechazo de la ventana temporal sigue en pie. Medido 2026-09-22: 6 de 10 ventas | deuda #14 (parcial) |
| P32 | Placeholder de medios en el historial (mitad útil de P16) | 🔴 registrado 2026-09-01 | — |
| P33 | Orden entre migración y despliegue | 🟡 ADR-012 `Accepted` y ejercido con éxito 2026-09-04 (013 partida, 014 después); queda exigir `-- Orden:` en las próximas — la 015 ya lo trae | — |
| P34 | El echo mata el turno en vuelo (bump de `strategy_version` → 409 stale) | 🔴 registrado 2026-09-05, no abierto; bloqueado por P5 | deuda #14 (resto) |
| P35 | Medir cuándo el LLM narra un cambio del pedido sin emitirlo (precondición silenciosa de ADR-010 §6) | 🔴 registrado 2026-09-06, no abierto — **instrumentación primero**, no cambio de formato | deuda #8 (parcial) |

**Siguiente número libre: P36.**

---

## Registro canónico de deudas

**Esta tabla es la fuente de verdad de las deudas.** Vivía en `CLAUDE.md`, que está gitignored,
mientras el modelo de sistema versionado la citaba con `refs: [deuda#N]`; se movió aquí el
2026-09-22 por la misma razón que el registro de P (ver P20). `tools/check_invariants.py`
falla si un YAML cita una deuda que no está en esta tabla.

> **Un número de deuda retirado nunca se reutiliza.** Una deuda resuelta conserva su `#`
> tachado; el siguiente item toma un número nuevo. Reutilizar un número rompe todo documento
> anterior que lo mencione — ya pasó con `#10` (ver "Notas históricas de numeración").

| # | Item | Severidad | Bloquea cliente que paga? |
|---|------|-----------|---------------------------|
| 1 | Sin tests de integración (cero contra Postgres) | Alta | Sí |
| 2 | Debounce con `asyncio.sleep(5)` durante transacción ocupa pool + race confirmada en datos (dos inbounds a 4s, ambos respondidos — auditoría 2026-06-14). Fix = P7, requiere ADR previo | Alta | A medio plazo |
| 3 | Sin telemetría (no hay alertas de fallas silenciosas n8n→backend); sin Log Analytics en el environment (solo stream en vivo). Parcial: fallos de compaction ahora a ERROR + contador `get_summary_failure_count()` (P4) | Alta | Sí |
| 4 | Sin vista de operador para human_handoff. Mitigado: canal Telegram vivo (aviso de venta lista + loop, botón confirm-payment — ADR-009); la vista completa (inbox/dashboard/config) sigue pendiente | Alta (era Crítica) | Sí |
| 5 | Multi-tenant defendido por aplicación, no por RLS | Media | Al 3er cliente |
| 6 | Auth: un solo token compartido sin rotación ni scopes | Media | Al escalar callers |
| 7 | ~~Memoria entre conversaciones rota en prod~~ **RESUELTA**: causa = clave de OpenAI del backend; verificada el 2026-09-12 (4 resúmenes). Texto completo en el histórico | Alta | — |
| 8 | LLM puede inventar formatos en extracted_data: el chat call de n8n usa `json_object` simple, sin `json_schema` estricto (la compaction sí lo usa). Mitigado parcialmente por gates deterministas (phone E.164-laxo — ADR-008) | Media | Sí, si no se ataja |
| 9 | Sin pruebas de carga — desconocemos throughput máximo | Alta | Sí (no se puede ofrecer SLA) |
| 10 | ~~Corte n8n~~ **resuelto** (ADR-009 §3). Remanente = loop de texto variable, hoy P10. Antes del 2026-06-14 `#10` significaba otra cosa (ver notas de numeración). Texto completo en el histórico | Media | Menor |
| 11 | **Deja de ser 100 % con P29**: los mensajes del operador son las primeras filas outbound del sistema con `chakra_message_id` real. El outbound del BOT sigue sin él, así que la deuda no se cierra. Texto original: Idempotencia outbound CERO: **314/314** outbound sin `chakra_message_id` (medido 2026-09-01; era 284/284 el 08-19 y 85/85 en junio); el envío de imagen no se persiste como mensaje (sistema ciego a su side-effect); duplicados reales de saludo (8.9s) e imagen. Fix = P6, requiere ADR previo (toca schema de `messages`) | Alta | Sí (duplicados visibles al cliente) |
| 12 | n8n sin alerta de fallo. **Mecanismo corregido 2026-09-01**: un 409 NO mata la ejecución — `POST Agent Action` lleva `continueOnFail: true`, así que el 409 o el 5xx se vuelven un item con `error`, caen al `else` de `Process Backend Response` como `suppressed_reason: backend_error`, y la ejecución termina marcada `success`. La rama de error existe; falta el aviso. Cero 409 en 268 ejecuciones retenidas. **2026-08-19: 4 respuestas 500 del ingest tragadas en silencio en una sola venta** (rama false de `IF Should Respond` → Stop, "success"). Fix = P5 (+P9 microfixes: `latency_ms` hardcodeado en 0, `slice(-10)` descarta la mitad del historial) | Alta | Sí (turnos perdidos sin alerta) |
| 13 | ~~500 en cada coalescencia del debounce~~ **RESUELTA** (PR #63, desplegada 2026-08-23). Texto completo en el histórico | — | — |
| 14 | **REMEDIADA EN SU MITAD DE PRESENCIA (P29, 2026-09-05)**: los echoes se persisten con `author='operator'`, entran a `recent_messages` con su marca, y una pausa determinista de 30 min calla al bot mientras el humano atiende. **Lo que sigue abierto es el turno en vuelo**: si el cliente escribe, n8n llama al LLM y mientras tanto el operador escribe, ese turno envía igual — el caso "llave 1234" del 08-19 queda parcialmente descubierto, y es **P34** (bloqueado por P5). Texto original: El sistema es ciego al operador: los mensajes que el humano manda desde la app de WhatsApp no pasan por el webhook (no se persisten, el LLM no los ve) y el bot sigue activo mientras el humano atiende — colisión "dos Sebastian" real el 2026-08-19 (el bot inventó una llave de pago entre la promesa y la llave real del operador). No existe forma de callar al bot salvo el circuit breaker. Fix = P29, registrado sin abrir | Alta | Sí (contradice al humano frente al cliente) |
| 15 | El export de n8n del repo dejó de ser el workflow vivo: `cafe_arenillo_v2` tiene `updatedAt 2026-08-29T23:38:06Z` y `n8n_workflow/cafe_arenillo_v2.json` es del 08-18. Diff funcional nulo (dos nodos Telegram perdieron `resource`/`operation`), pero el archivo que el ROADMAP declara "único respaldo" ya no es byte-idéntico y no consta quién lo editó. Re-exportar antes de tocar n8n | Media | No, pero anula el rollback |
| 16 | La instancia de n8n es compartida: `Predicción horaria → Telegram` falla 7 veces al día desde al menos el 08-19 y son **todas** las ejecuciones en error de la instancia; `Liquidación de señales` tiene tantas ejecuciones como el pipeline de ventas. Cualquier alerta por `status=error` nace ahogada, y la réplica única comparte CPU con automatizaciones ajenas | Media | Al alertar (P5) |
| 17 | `max_natural` está activo y enrutado por el `master`, con un whitelist de 4 campos y **ninguno de identidad P14**: un cliente suyo con privacidad de número se descarta en silencio, la exec 9459 otra vez. Y es un **AI Agent con tools**, justo lo que ADR-002 descartó y lo que `n8n_workflow/CLAUDE.md` prohíbe. **Confirmado por el dueño el 2026-09-03: es un tenant REAL pero todavía NO está en operación, hoy es una prueba.** El riesgo no es de hoy, es del día que entre: su primer cliente con privacidad de número se perdería en silencio. Antes de ponerlo en operación hay que llevarle P14 y decidir qué hacer con su arquitectura de AI Agent | Media | No hoy; sí al ponerlo en operación |
| 18 | El outbound se persiste ANTES de enviarse (`agent_action.py` escribe el mensaje y devuelve `approved`; recién después n8n llama a Chakra, sin `continueOnFail`). Si el envío falla, la DB afirma un envío que no ocurrió, el historial se contamina y el circuit breaker cuenta fantasmas. Distinto de #11: no es idempotencia, es veracidad del registro | Media | Sí, en silencio |
| 19 | Los turnos suprimidos no dejan rastro en `audit_log`: el guard y el debounce retornan antes del `AuditLog` del ingest. Hoy hay 36 inbound sin evento `message_ingest`. **El camino nuevo de P29 SÍ deja rastro** (`turn_suppressed` con `reason: operator_active`), a diferencia de los tres anteriores; la deuda no se cierra hasta que los otros tres hagan lo mismo. No se puede distinguir un turno suprimido por el guard de uno por debounce ni de uno perdido por un 500 | Media | No, pero ciega el diagnóstico |
| 20 | El echo del operador pisa `client_users.display_name`. `ingest_operator_echo` resuelve al cliente con el mismo `_resolve_client_user` que el ingest normal: en el camino con bsuid lo sobrescribe cuando el valor es verdadero (`ingest.py:720-721`), y en los dos `ON CONFLICT DO UPDATE` lo mete sin guard vía `set_on_match` (`ingest.py:700`), así que ahí también un `None` pisa un nombre bueno. `Normalize Echo` deriva el nombre de `profile.name \|\| profile.username` y el echo no trae `name`, así que gana un username; ese campo llega al prompt (`prompt_context.py:311`). Ningún test lo cubre: los de echo usan el mismo nombre de punta a punta. Hallado el 2026-09-12, **registrado, no arreglado**; es el hueco declarado de `INV-OP-004`. **Observada en prod** en 2 de 2 clientes con echo (2026-09-15, 2026-09-23) | Media | Sí (el bot llama al cliente por otro nombre) |
| 21 | `messages.author` solo se escribe para el operador: las filas de cliente y bot nacen NULL y el código las deduce de `direction` (`author_of()`). El comportamiento es correcto; un conteo en SQL sobre `author` no lo es (161 filas NULL entre el 2026-09-06 y el 2026-09-22) | Baja | No |
| 22 | Cinco claves de `business_rules` que ningún código lee: `auto_escalate_after_minutes` (30), `require_address_for_order`, `notification_phone`, `agent_persona` y `shipping_cities`. Prometen un comportamiento que no existe, la misma clase que el «reset por idle» de junio. `shipping_cities` además contradice a `shipping_rules.cities` | Media | No |
| 23 | n8n corre `n8nio/n8n:latest` sin versión fija (revisión `ca-r8fm-n8n--0000010`). Un reinicio puede subir de versión mayor y romper los workflows sin aviso | Media | Sí (todo el flujo pasa por n8n) |

**Siguiente número libre: deuda #24.**

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
