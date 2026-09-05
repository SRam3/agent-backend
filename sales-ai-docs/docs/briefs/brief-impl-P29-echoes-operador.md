# Brief de implementación — P29 acotado: echoes del operador y pausa determinista

> **Sistema REAL (no greenfield)**: backend FastAPI en producción (Azure Container Apps),
> Postgres 16, migraciones 001–014 aplicadas, ADR-010 desplegado en la revisión `--0000058`
> (2026-09-04 02:47 UTC). Este brief **edita código vivo, toca schema y toca el workflow
> `master`**. Es el frente más caro abierto hasta hoy: [ADR] + [DB] + [B] + [N8N].
>
> **Alcance honesto**: P29 acotado hace que el sistema **vea** al operador y se **calle**
> mientras el humano atiende. Lo que **no** hace: no mata el turno que ya está en vuelo
> (§2.6 — el caso «llave 1234» del 08-19 queda solo parcialmente cubierto), no reactiva
> automáticamente, no construye vista de operador (deuda #4), y no es una capa de post-venta
> (ver §9).
>
> **Sustituye a P31.** El diseño de ventana temporal post-venta se descartó: era un proxy de
> una señal que ya llega al webhook. Ver §1.3.
>
> **Disciplina**: ADR-013 escrito y aceptado ANTES de tocar código. Plan por archivo antes de
> codear, esperar confirmación. Un commit con sus tests, suite verde entre fases. Rama:
> `feat/p29-echoes-operador`. La fase de n8n va aparte y con export antes y después.

---

## 1. Contexto

### 1.1 Lo que está establecido, con evidencia

**Los echoes llegan.** El diagnóstico del 2026-08-22 §1.1 tiene el payload crudo de
`exec 10678`. No es hipótesis:

```json
"changes": [{
  "value": {
    "metadata": { "phone_number_id": "<phone_number_id>" },
    "contacts": [{ "profile": {"username": "…"}, "user_id": "CO.…" }],
    "message_echoes": [{
      "from": "<número del negocio>",
      "id": "wamid.…",
      "to_user_id": "CO.…",
      "timestamp": "1787177492",
      "text": { "body": "…" },
      "type": "text"
    }]
  },
  "field": "smb_message_echoes"
}]
```

**Mueren en dos nodos que solo saben leer `messages[]`.** El `Set` whitelist
`map_webhook_data_arenillo` copia diez campos, seis de ellos colgando de `value.messages[0]`;
en un echo ese array no existe y la copia produce `null`. Después, `If Message Exists` evalúa
`!!(m && m.id && (m.from_user_id || m.from))` → `m.id` es `null` → rama falsa → `Stop`, marcado
`success` en ~130 ms. Sin log, sin error, sin registro.

**La identidad ya llega bien.** `contacts[0].user_id` está presente en **247 de 247** payloads
(100 %), y `map_webhook_data_arenillo` **ya lo copia** (asignación `p14-contact-user-id`). El
BSUID sobrevive intacto en los echoes. Lo que falta no es identidad: es que el guardián deje de
preguntarle a `messages[]`.

**Ocurrencias reales, tres, la última tres días antes de la auditoría**: 2026-08-19 (10 echoes),
08-26 (4), 08-29 (1), **08-30 (2)**. Total 17 en la retención, que es **cota inferior**: la
retención de n8n solo conserva algunos días.

**El daño concreto, medido**: el 2026-08-19 a las 22:14:31Z el operador escribió *«mañana nos
entregan el café recién tostado… ¿podríamos realizarte la entrega pasado mañana?»*. **El sistema
nunca supo que esa promesa de entrega existió.** Y a las 19:42–19:45, el bot inventó la «llave
1234» ENTRE la promesa del operador de compartir la llave y la llave real, porque razonaba sobre
un diálogo al que le faltaba la mitad.

### 1.2 Lo que discrimina las formas

`contacts[]` viene también en los status callbacks, así que su presencia **no basta** para
decidir qué es un payload. Lo que discrimina es **cuál array está poblado**:

| | `changes[0].field` | array portador | identidad del interlocutor |
|---|---|---|---|
| inbound normal | `messages` | `messages[]` | `from_user_id` (32 % de presencia) |
| **echo del operador** | **`smb_message_echoes`** | **`message_echoes[]`** | **`to_user_id`** (destinatario) |
| status callback | `messages` | `statuses[]` | `recipient_user_id` |

**Ese switch no existe hoy.** Construirlo es el corazón de la fase 4.

### 1.3 Por qué esto sustituye a P31

P31 proponía callar al bot durante N minutos después de un `sale_closed`. Se descartó porque la
ventana temporal es un **proxy** de «hay un humano atendiendo», y el proxy falla en los tres
casos que importan:

1. El operador coordina el envío y escribe al cliente **60 minutos después** del cierre: la
   ventana ya expiró y la colisión vuelve.
2. El cliente agradece **tres días después**: fuera de cualquier ventana.
3. El operador se fue: la ventana calla al bot cuando **no hay nadie**, y el cliente queda sin
   respuesta y sin aviso.

El echo no tiene ninguno de esos problemas porque **se refresca solo**: cada mensaje del operador
extiende la pausa, sea a los 2 minutos o a los 3 días. Se deja de adivinar una señal que ya se
recibe.

**P31 se cierra como decisión, no como trabajo** — mismo tratamiento que la auditoría le dio a
P23. Conserva su número y su entrada registra por qué se rechazó la ventana.

---

## 2. Decisiones de diseño

### 2.1 Superficie propia, no el `ingest`

**Endpoint nuevo: `POST /api/v1/ingest/operator-echo`.** No se reutiliza
`POST /api/v1/ingest/message`.

Razón principal, y es arquitectónica, no de comodidad: **un echo no es un turno**. No debe
disparar debounce, ni el `sleep(5)`, ni el cómputo del directive, ni el bump de
`strategy_version`, ni ninguna llamada al LLM. Meterlo en `ingest.py` significaría enhebrar un
condicional grande por la función más compleja del sistema, que es exactamente donde vive la
deuda #2.

Razón secundaria, y más importante a largo plazo: **el invariante se vuelve estructural.** La
regla «ningún checkpoint puede marcarse desde un echo» no se cumple porque esté escrita en un
comentario, sino porque **el endpoint no tiene la capacidad de escribir un checkpoint**. Es el
mismo patrón que `OPERATOR_ONLY_FIELDS` usó para el pago en P11: no una regla de prompt, una
imposibilidad de código.

### 2.2 El operador es un autor, no una dirección

`messages.direction` seguirá siendo `outbound` para un echo — desde el negocio, el mensaje salió.
Lo que falta es **quién lo escribió**. Columna nueva:

```sql
author VARCHAR(20) CHECK (author IN ('bot','operator','customer'))
```

VARCHAR + CHECK, no ENUM nativo, por ADR-006. Nullable y aditiva, con backfill en la misma
migración (`inbound → 'customer'`, `outbound → 'bot'`, 673 filas). **Orden respecto al despliegue:
ANTES**, por ADR-012 — es aditiva y el código viejo la ignora.

Se descartó `direction = 'operator'`: rompería `ck_message_direction` y todas las queries que
hoy cuentan outbound (incluido el circuit breaker, que dejaría de ver los suyos).

### 2.3 La idempotencia sale gratis

El echo trae `id: "wamid.…"`. `messages.chakra_message_id` ya tiene índice UNIQUE (mecanismo de
idempotencia de ADR-001/001-schema). Persistir el wamid del echo ahí significa que **una
reentrega de Meta rebota contra el índice sin escribir nada** — cero código de idempotencia.

Efecto colateral que conviene registrar: los mensajes del operador serán **las primeras filas
outbound del sistema con `chakra_message_id` real**. Hoy son 314 de 314 sin wamid (deuda #11).
No cierra la deuda —el outbound del bot sigue sin él— pero deja de ser 100 %.

### 2.4 La pausa es supresión con vencimiento, no cambio de estado

**No** se transiciona a `human_handoff`. Ese estado no tiene vuelta: la máquina de ADR-007 es
`active → human_handoff → closed` y no existe camino de regreso. Una pausa que no expira no es
una pausa.

Mecanismo: en el `ingest`, antes de computar el directive, si existe un mensaje con
`author='operator'` en esta conversación dentro de los últimos N minutos, se devuelve
`build_suppressed_response(reason="operator_active")`. Ese helper ya existe desde el fix de la
deuda #13 (PR #63) y ya sirve a tres caminos (`debounce`, `duplicate`, `unreadable_content`); este
es el cuarto. n8n ya sabe leerlo por `IF Should Respond`. **Cero cambios en n8n para la pausa.**

**N = 30 minutos**, en `business_rules.operator_pause_minutes` con default en código.
Justificación del 30: modela «un humano está en este chat ahora mismo», y la atención humana en un
chat se mide en minutos. A diferencia de la ventana de P31, **no hay que acertarle**: la señal se
refresca con cada echo, así que equivocarse por lo bajo solo significa que el bot vuelve un poco
antes, y el siguiente mensaje del operador lo calla otra vez.

### 2.5 El turno suprimido tiene que dejar rastro — no negociable

El `ingest` retorna en `ingest.py:289-320`, **antes** del `AuditLog` de `ingest.py:383`. Hoy hay
**36 inbound sin evento `message_ingest`** por esa razón (deuda #19), y no se puede distinguir un
turno callado por el guard de uno callado por debounce ni de uno perdido por un 500.

**Este brief no puede añadir un cuarto camino silencioso a ese agujero.** La supresión por
`operator_active` escribe su propio evento `AuditLog` con
`{"reason": "operator_active", "last_echo_at": <iso>, "pause_minutes": <int>}`. Es criterio de
aceptación, no mejora opcional. Si el rastro no está, la fase no se cierra.

### 2.6 Lo que NO cubre la fase 1: el turno en vuelo

Si el cliente escribe, el `ingest` corre, n8n llama al LLM, y **mientras tanto** el operador
escribe, el `/agent/action` de ese turno va a aprobar y enviar igual. La pausa solo actúa desde el
turno siguiente.

Ese es exactamente el caso «llave 1234» del 08-19. **Queda parcialmente descubierto en la fase 1,
y hay que decirlo así en el ROADMAP**, no vender P29 como resuelto.

La solución existe y es elegante: que el echo **incremente `strategy_version`**. ADR-003 creó ese
mecanismo justamente para invalidar contexto viejo, y un echo cambia el contexto de forma
material; el `/agent/action` en vuelo devolvería **409 stale** y n8n suprimiría el envío.

**No se hace en la fase 1, a propósito.** Nunca ha ocurrido un 409 en la vida del sistema (cero en
268 ejecuciones retenidas, auditoría §2.5). Esto sería el primer productor real de 409, y hoy un
409 cae en el `else` de `Process Backend Response` como `suppressed_reason: "backend_error"` —
indistinguible de un backend caído, y sin alerta. Crear el primer 409 del sistema en el mismo PR
que crea la ingesta de echoes es lo contrario de baby-steps. Se registra como fase 6, después de
que P5 exista y con `suppressed_reason` propio.

---

## 3. El fix, por archivo

> Referencias `archivo:línea` al estado en `main` del 2026-09-04. **Verificarlas antes de editar.**
> Si no coinciden, parar y reportar; no adivinar.

### 3.1 `migrations/versions/015_add_message_author.sql` — nueva

```sql
-- Orden: ANTES del despliegue
-- Por qué: columna aditiva y nullable que el ORM nuevo declara; el código viejo la ignora.
```

`ALTER TABLE messages ADD COLUMN IF NOT EXISTS author VARCHAR(20)`, CHECK con los tres valores,
backfill por `direction`. Índice a evaluar: la consulta de la pausa filtra por
`(conversation_id, author, created_at DESC)` — con 673 filas hoy no hace falta, **pero la decisión
se toma explícita**, no por omisión. Si se añade, va en la misma migración.

### 3.2 `sales_agent_api/app/api/ingest_operator_echo.py` — módulo nuevo

Endpoint pequeño, una sola responsabilidad. Secuencia:

1. Auth: token de servicio (el mismo que usa n8n para el ingest). **No** el de operador —
   `SALES_AI_OPERATOR_TOKEN` está escopado a `/operator/*` y esto no es una acción de operador,
   es ingesta de webhook.
2. Resolver `client_id` desde `phone_number_id` (misma ruta que el ingest normal).
3. Resolver `client_user` por **`(client_id, bsuid)`** usando `to_user_id`/`contacts[0].user_id`.
   Si no existe: crear, igual que hace el ingest — el operador puede haber escrito primero.
4. Conversación en ventana de 24 h. Si no hay: crear con el helper existente (mismo
   comportamiento, incluida compaction y seed — ver §8, pregunta 3).
5. Persistir en `messages`: `direction='outbound'`, `author='operator'`,
   `chakra_message_id=<wamid del echo>`, `content=text.body`, `timestamp` del echo.
6. `AuditLog` con `event_type='operator_echo_ingested'`.
7. **NO**: bump de `strategy_version`, cómputo de directive, merge en `extracted_context`, sync a
   `profile`, llamada al LLM, transición de estado. Nada de eso existe en este módulo.

**Idempotencia**: capturar la violación del UNIQUE de `chakra_message_id` y devolver
`200 {"status": "duplicate"}`. No 409 — una reentrega de Meta no es un error.

### 3.3 `sales_agent_api/app/api/ingest.py` — la pausa

Un solo punto de enganche, junto al guard de contenido ilegible y al debounce
(`ingest.py:289-320`). Antes del cómputo del directive:

```
si existe messages.author='operator' en esta conversación
   con created_at >= now() - pause_minutes:
       AuditLog(reason='operator_active', last_echo_at=…, pause_minutes=…)
       return build_suppressed_response(reason='operator_active')
```

Orden respecto de los guards existentes: **después** del guard de contenido ilegible (un medio
ilegible se sigue suprimiendo por su propia razón, que es más específica) y **antes** del
debounce (no tiene sentido dormir 5 s para luego callar).

### 3.4 `sales_agent_api/app/services/prompt_context.py` — el historial

`recent_messages` debe incluir los mensajes del operador con una marca de autoría legible por el
LLM, p. ej. `[operador]` como prefijo o un campo `author` en el ítem. **Sin esto, la mitad del
valor se pierde**: el bot seguiría razonando sobre un diálogo incompleto cuando la pausa expire.

Ojo con el orden temporal: los inbound llevan reloj de Meta y los outbound `now()` de pared
(ADR-011 §5.2.4). Los echoes traen **timestamp de Meta**, así que hay que persistir ese, no
`now()`, o el historial se desordena. Es la costura que ADR-011 documentó.

### 3.5 n8n — `master` (fase 4, sesión aparte)

**Este es el nodo que causó el drop silencioso de P14.** Regla de la sesión: **solo asignaciones
aditivas**. No se modifica ninguna de las diez asignaciones existentes de
`map_webhook_data_arenillo`.

1. `map_webhook_data_arenillo`: añadir un campo `payload_kind` calculado por cuál array está
   poblado (`messages` / `message_echoes` / `statuses` / `unknown`), y las asignaciones nuevas del
   echo (`echo_id`, `echo_timestamp`, `echo_text`, `echo_to_user_id`).
2. `If Message Exists` → pasa a **Switch** de tres ramas por `payload_kind`. La rama `messages`
   conserva **byte a byte** la condición viva
   (`!!(m && m.id && (m.from_user_id || m.from))`). La rama `message_echoes` va al sub-workflow
   nuevo. La rama `statuses` va a `Stop` **por ahora**, con una nota: hoy ese mismo `Stop` traga
   los avisos de no-entrega de WhatsApp, y eso es alcance de P5.
3. Nodo HTTP nuevo → `POST /api/v1/ingest/operator-echo`, con `continueOnFail: false` y su rama
   de error conectada al aviso de P5.

**Re-export de los tres workflows antes y después** (deuda #15: el export del repo dejó de ser el
vivo el 2026-08-29 y no consta quién lo editó).

---

## 4. Fases

| # | Qué | Riesgo | Precondición |
|---|---|---|---|
| 0 | **ADR-013** escrito y `Accepted` | [ADR] | — |
| 1 | Migración `015` (`author` + backfill), aplicada en prod ANTES del despliegue | [DB] | ADR-013 |
| 2 | Endpoint `/ingest/operator-echo` + ORM + tests. Probable con `curl` usando el payload de `exec 10678` | [B] | 1 |
| 3 | Pausa en el `ingest` + evento de auditoría + `author` en `recent_messages` | [B] | 2 |
| 4 | **Sesión n8n única: P5 + P9 + switch de echoes**, con re-export antes y después | [N8N] | 3 |
| 5 | Verificación en prod con un echo real | — | 4 |
| 6 | *(registrada, no abierta)* bump de `strategy_version` en echo → mata el turno en vuelo | [B] | P5 vivo |

**Las fases 2 y 3 se pueden desplegar sin la 4**: el endpoint queda vivo sin recibir tráfico y la
pausa nunca dispara porque no hay filas con `author='operator'`. **Cero riesgo de regresión.** Esa
propiedad es deliberada: permite que todo el backend esté probado y desplegado antes de tocar el
nodo más frágil del sistema.

---

## 5. Tests — verificación por mutación

**Unitarios / contrato del endpoint**
1. Echo con el payload literal de `exec 10678` → persiste una fila con `author='operator'`,
   `direction='outbound'`, `chakra_message_id` = el wamid.
2. El mismo echo dos veces → segunda vez `200 duplicate`, **una sola fila**.
3. Echo de un `client_user` que no existe → lo crea por `(client_id, bsuid)`.
4. Echo sin conversación en ventana de 24 h → crea conversación.
5. **Echo con `client_id` distinto → no toca datos de este tenant.** Aislamiento multi-tenant.
6. Sin token o con el token de operador → 401/403. El endpoint no acepta
   `SALES_AI_OPERATOR_TOKEN`.
7. **Invariante**: tras procesar un echo cuyo texto es «listo, ya quedó confirmado»,
   `extracted_context` está **idéntico**: ni `user_confirmation`, ni `payment_confirmation`, ni
   ningún slot. Este test es el que protege el invariante central.
8. `strategy_version` no cambia (hasta la fase 6).

**Integración del `ingest`**
9. Echo hace 5 min → el turno siguiente devuelve `should_respond: false`,
   `reason: "operator_active"`.
10. Echo hace 45 min, pausa 30 → el bot responde normal. **No-regresión.**
11. Segundo echo a los 20 min → la pausa se **extiende** desde el segundo, no desde el primero.
12. El evento de auditoría `operator_active` existe, con `last_echo_at` y `pause_minutes`.
13. Un medio ilegible **durante** la pausa se suprime por `unreadable_content`, no por
    `operator_active`: la razón más específica gana.
14. `recent_messages` incluye el mensaje del operador con su autoría y en el orden temporal
    correcto.

**Mutación obligatoria antes de cerrar la fase 3**: invertir la comparación de la ventana y
confirmar que 9 y 10 fallan; borrar la asignación de `author` y confirmar que 1 y 7 fallan. Si
pasan, los tests están mal escritos.

---

## 6. Criterios de aceptación

- [ ] Un mensaje del operador queda persistido con su wamid real y su autoría.
- [ ] Un echo reentregado no duplica fila.
- [ ] **Ningún checkpoint, slot ni transición de estado puede escribirse desde un echo** (test 7).
- [ ] El bot se calla mientras el operador escribe, y **vuelve solo** al expirar la ventana.
- [ ] Un turno callado por `operator_active` es distinguible en `audit_log` de uno callado por
      guard, por debounce o perdido por un 500.
- [ ] `recent_messages` muestra la conversación completa, en orden temporal correcto.
- [ ] Ningún `phone_number_id` de otro tenant afecta a este (test 5).
- [ ] Suite completa en verde. Cero regresiones sobre los 254 tests existentes.
- [ ] Export de los tres workflows en el repo, byte-idéntico al vivo, antes y después de la fase 4.

**Verificación en prod (fase 5)** — la query se escribe ANTES de desplegar:

```sql
SELECT m.created_at, m.author, m.chakra_message_id IS NOT NULL AS tiene_wamid
FROM messages m
WHERE m.client_id = :client_id AND m.author = 'operator'
ORDER BY m.created_at DESC LIMIT 20;
```

Regla de la auditoría §9: **un frente que termina en «verificar X» no se cierra hasta leer X.**
P29 pasa a ✅ cuando esa query devuelve filas de un operador real y existe al menos un evento
`operator_active` correspondiente.

---

## 7. PII y postura de producción — decidir antes, no después

Persistir echoes cambia la naturaleza del dato: dejan de ser mensajes de clientes y pasan a
incluir **comunicaciones del personal del tenant**. Tres cosas que se deciden en este brief, no en
el siguiente:

- **Retención.** Hoy no hay política de retención sobre `messages`. Con contenido de operador, la
  ausencia de política deja de ser aceptable. Decidir el plazo aquí aunque el barrido se
  implemente después.
- **Aviso al tenant.** El acuerdo con el cliente necesita una línea diciendo que los mensajes
  enviados desde el número del negocio se procesan y almacenan. Es un párrafo hoy y una
  conversación legal más adelante.
- **Acceso.** Hoy hay un solo token de servicio compartido, sin scopes ni rotación (deuda #6). El
  día que exista una vista de operador (deuda #4) que muestre contenido de personal, eso deja de
  ser diferible. Registrarlo, no resolverlo aquí.

**Logging**: el contenido del echo **no se loguea**. Ni en el endpoint, ni en el error de
idempotencia, ni en la rama de fallo de n8n. Se loguea el wamid y la longitud, nunca el cuerpo.

---

## 8. Preguntas a responder antes de codear

1. ¿El `Switch` del `master` enruta por `phone_number_id` **antes** o **después** del whitelist?
   Determina si el `payload_kind` se calcula una vez o por tenant. **Y determina si `max_natural`
   hereda el cambio** — si es un nodo compartido, esta sesión toca dos tenants (deuda #17).
2. ¿Existe algún índice utilizable en `messages` para `(conversation_id, author, created_at)`? Si
   no, ¿se acepta el seq scan con 673 filas o entra en la migración `015`?
3. Cuando un echo llega sin conversación en ventana, ¿debe disparar la lazy compaction de la
   anterior? **Default propuesto: sí**, por consistencia con el ingest normal. Consecuencia: un
   mensaje del operador puede provocar una llamada al LLM. Confírmalo.
4. ¿El operador **siempre** escribe desde el número del negocio? Si alguna coordinación va por
   llamada o por WhatsApp personal, hay un punto ciego que ningún echo cubre, y eso limita lo que
   se puede prometer de este frente.

---

## 9. Lo que este brief NO autoriza

- Vista de operador, dashboard o inbox — es deuda #4, y hoy no existe frente abierto.
- Métricas de desempeño del operador o «auditoría del humano» como superficie de producto. Los
  datos quedan disponibles; **construir encima es otra decisión** y hoy hay un solo operador.
- Capa de post-venta (seguimiento, confirmación de entrega, recompra). Depende de que la venta
  exista fuera de una conversación `closed`, que es **P24** (`purchase_intents`), 🟡 registrado.
- Mensajes proactivos al cliente — plantilla aprobada, costo por mensaje, Ley 2300/2023 y RNE. Es
  **P27**, bloqueado por P24 y P26.
- Marcar cualquier cosa desde un echo. **Nunca.**

---

## 10. Actualización de documentación

Ningún commit de código se mergea sin su commit de documentación.

### 10.1 `docs/decisions/ADR-013-operador-como-autor.md` — NUEVO, precondición de la fase 1

Siguiente número libre según `docs/decisions/README.md`. Contenido mínimo:

- **Contexto**: tres colisiones reales; los echoes llegan y se descartan; el sistema desconoce la
  mitad de la conversación; la promesa de entrega del 08-19 que nunca existió para el sistema.
- **Decisión**: (1) el operador es autor de primera clase, con columna `author`; (2) los echoes se
  ingieren por una superficie propia sin capacidad de escribir checkpoints; (3) **los echoes son
  contexto, nunca hecho** — ningún checkpoint, slot ni transición puede originarse en un echo;
  (4) la pausa es supresión con vencimiento, refrescada por cada echo, no cambio de estado.
- **Alternativas descartadas**: ventana temporal post-venta (P31, §1.3); `direction='operator'`
  (§2.2); botón «tomo la conversación» en Telegram (depende de que el operador avise, y el
  testimonio del 08-19 dice justo lo contrario: *«me quedé atendiendo porque vi el bot muy
  perdido»* — cuando el bot falla, el humano va al chat, no a Telegram); endpoint de pausa manual
  (mismo problema).
- **Consecuencias**: el turno en vuelo sigue descubierto en la fase 1 (§2.6); los mensajes de
  operador son los primeros outbound con wamid real; entra contenido de personal en la base.
- **Cuándo revisar**: cuando exista un segundo operador, o cuando exista vista de operador.

### 10.2 `docs/decisions/ADR-009-handoff-closure-loop.md` — nota as-built

La consecuencia «el bot acompaña en `active` hasta el botón» deja de describir el comportamiento:
desde P29 el bot se calla mientras el operador escribe, sin cambio de estado y sin botón. **El
cuerpo del ADR no se reescribe** (regla §1 de `decisions/README.md`); solo el bloque as-built.

### 10.3 `CLAUDE.md`

- **Schema**: `messages` gana `author`; nota de la migración `015` con su `-- Orden:`.
- **Patrón de dos llamadas**: superficie nueva `POST /api/v1/ingest/operator-echo`, y la pausa
  como cuarto camino de supresión junto a `debounce`, `duplicate` y `unreadable_content`.
- **Deuda #14** → **remediada** en su mitad de presencia; anotar que el turno en vuelo queda
  fuera (§2.6) y que eso es la fase 6.
- **Deuda #11** → deja de ser 100 %: los outbound de operador sí llevan wamid.
- **Deuda #15** → resuelta si el re-export se hace; si no, no tocar su estado.
- **Deuda #19** → no se cierra, pero anotar que el camino nuevo **sí** deja rastro, a diferencia
  de los tres anteriores.

### 10.4 `docs/ROADMAP.md`

- **P29**: alcance real, fases, y explícitamente qué **no** cubre.
- **P31**: → ✅ **cerrado como decisión, sin implementar**, con la razón (§1.3). Mismo tratamiento
  que P23. El número no se recicla.
- **Registro canónico**: filas P29, P31, P5, P9. Header `Última actualización`.
- **Orden de cierre**: los puntos 3 (P31) y 6 (P29) se funden; P32 sube.
- **Fase 6** registrada como frente nuevo con el siguiente número libre (**P34**), o como nota
  dentro de P29 — decidir, pero la regla del repo dice que si aparece dos veces en prosa, toma
  número.

### 10.5 `n8n_workflow/CLAUDE.md`

Documentar el `Switch` de `payload_kind` y la regla de que `map_webhook_data_arenillo` **solo
admite asignaciones aditivas**. Ese nodo ya causó el drop de P14 y es el cuello por donde no pasa
ningún campo nuevo; que la regla esté escrita es más barato que la tercera repetición.

### 10.6 Deuda de documentación pendiente desde el 2026-09-04

El pase de actualización quedó a medias, y la parte desactualizada es la **canónica**
(`ROADMAP.md`), mientras el espejo operativo (`CLAUDE.md`) va adelantado — la inversión exacta que
P1 existió para cerrar. Arrastrar en el commit de documentación de la fase 1:

| Archivo | Dice | Debe decir |
|---|---|---|
| `ROADMAP.md` header | `Última actualización: 2026-09-01` | 2026-09-04 |
| `ROADMAP.md` fila **P4** | «falta rotar el secreto» | rotado 09-04, cargado por `--0000058`; falta verificar la compaction |
| `ROADMAP.md` fila **P15** | «falta aplicar la 013 y desplegar» | en producción desde 09-04 (PR #68); falta verificar |
| `ROADMAP.md` entrada **P15** | «**Falta**: mergear y desplegar» | ídem |
| `decisions/README.md` fila 010 | `Propuesto ⚠️` + nota de rama | `Accepted`, sin ⚠️ |
| `ADR-012` | `Propuesto` | `Accepted` — ejercido con éxito en su primer caso real el 09-04 |

`ADR-011` se queda en `Propuesto`: aún no se ha ejercido. Su único entregable —el microfix del
mismo segundo, `created_at >= msg_timestamp AND id != message.id`— **sigue sin dueño**; el sitio
natural es la sesión de n8n de la fase 4, junto a P9.
