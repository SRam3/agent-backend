# Brief — ADR-010: el backend gobierna el resumen del pedido

- **ADR**: `docs/decisions/ADR-010-backend-gobierna-resumen.md` (Propuesto, 2026-08-23)
- **Frente**: P15
- **Rama**: `feat/adr-010-backend-gobierna-resumen` (desde `origin/main`)
- **Alcance declarado por el ADR**: 100% backend + una migración. **Cero cambios en n8n.**

---

## Lectura del ADR: qué problema resuelve realmente

El ADR se lee como "seis decisiones", pero solo una es el fix. Las otras cinco existen
porque el fix no se puede construir sin ellas.

El defecto es que `user_confirmation` afirma un **acto** ("el cliente aceptó su pedido") y
hoy se decide con un juicio de **lenguaje** sobre un mensaje suelto, sin nada contra qué
contrastarlo. El gate actual (`agent_action.py:182-188`) solo mide suficiencia de datos:
con los 4 slots puestos, cualquier `user_confirmation: true` del LLM entra. Por eso
`"Barrio El Campin"` cerró un pedido.

La solución no es juzgar mejor el mensaje: es **crear el hecho contra el cual juzgarlo**.
Hoy no existe. El resumen vive como texto libre dentro del `response_text` del LLM y el
backend no sabe que lo mandó. De ahí sale toda la cadena:

```
para validar la confirmación   → hace falta saber que hubo resumen
para saber que hubo resumen    → el backend tiene que mandarlo él (§1)
para mandarlo                  → tiene que calcular el total (§2)
para calcular el total         → necesita reglas de envío deterministas (§3)
                                 y una sola presentación, sin conversiones (§4)
y una vez que existe el hecho  → el gate se vuelve determinista (§5)
                                 y la invalidación también (§6)
```

**Consecuencia de diseño que conviene tener presente durante toda la implementación**: el
fingerprint no es un detalle de la Mecánica, es *el* entregable. Los §1-§4 son el precio de
producirlo.

### Lo que el ADR resuelve de rebote, y que NO hay que parchear

- **H6 (`product_id` en el gate)**: no se toca `_USER_CONFIRMATION_REQUIRES`. No hay resumen
  sin precio, no hay precio sin `product_id`, no hay confirmación sin resumen. Queda
  implicado. Añadirlo al gate sería el parche que el ADR declara innecesario.
- **`agent_action.py:161-162` (el desmarcado imposible)**: **no se cambia el filtro
  `and v`.** El ADR no pide que el LLM pueda desmarcar — pide que el **backend** invalide
  (§6). El filtro truthy sigue siendo correcto: mantiene al LLM sin autoridad para
  desmarcar, igual que no la tiene para marcar el pago. El camino de desmarcado lo abre la
  escritura propia del backend, no una excepción en el merge.

---

## Ubicaciones confirmadas (leídas sobre esta rama)

| Qué | Dónde |
|---|---|
| Merge + gates del DAG | `agent_action.py:142-191` (`compute_context_updates`) |
| Gate actual de confirmación | `agent_action.py:182-188` |
| Transición que dispara el aviso Telegram | `agent_action.py:194-204` (`is_new_user_confirmation`) |
| Persistencia del contexto y del outbound | `agent_action.py:336-393`, `:468-482` |
| Circuit breaker (corre ANTES del merge) | `agent_action.py:283-334` |
| Precio del producto | `agent_action.py:506-525` (`_fetch_product_price`) |
| Directive que aún ordena "presenta el resumen" | `goal_strategy.py:287` |
| Render de reglas de envío al prompt (con "aprox.") | `prompt_context.py:44-75` |
| `strategy_snapshot` (JSONB propiedad del backend) | `ingest.py:352-359` |
| `live_language` del turno | `ingest.py:417` (`detect_language`) |
| Precondición del botón del operador | `confirm_payment.py:78` |
| Reglas de envío vigentes en prod | `migrations/versions/005_*.sql:27-52` |
| Prompt: sección RESUMEN DE CONFIRMACIÓN y PESO Y CANTIDAD | `migrations/versions/009_*.sql:141-145`, `:224-244` |

**Verificado**: todos los lectores de `user_confirmation` usan `.get()` con truthiness
(`confirm_payment.py:78`, `goal_strategy.py:_evaluate`, `prompt_context.py:288`). Escribir
`False` es seguro en todos ellos y conserva el rastro de que alguna vez estuvo puesto, cosa
que borrar la clave no haría.

---

## Cuatro huecos del ADR y cómo se cierran

El ADR es explícito en el diseño y deja abierta la implementación en cuatro puntos. Aquí
van resueltos, porque cada uno puede romper el fix en silencio.

### H1 — ¿Cuál es "el inbound que disparó este turno"? (cond. 3)

**Qué significa aquí "sin tocar n8n".** El flujo pasa por n8n, evidentemente: n8n hace las
dos llamadas (`/ingest` → LLM → `/agent/action`). Lo que el ADR excluye es **editar el
workflow**. Y hace falta distinguirlo porque este hueco tiene una solución trivial que cae
justo del lado prohibido.

`/agent/action` recibe `conversation_id`, `strategy_version`, `response_text` — y **no
recibe el mensaje del cliente**. n8n sí lo tiene (le llegó por el webhook), así que lo fácil
sería añadir un campo al request y que n8n lo mande. Eso es abrir el nodo "POST Agent
Action" del workflow vivo, con export antes/después y una sesión dedicada: exactamente el
costo que el ADR saca del alcance, y la razón por la que la fase B2 de P11 sigue pendiente
desde julio.

Pero el mensaje **ya pasa por el backend** en `/ingest`. Así que el backend puede anotárselo
a sí mismo en la primera llamada y releerlo en la segunda. El hilo que une ambas es
`strategy_version`, que n8n ya devuelve sin cambio alguno.

**Por qué no sirve "el último inbound de la conversación".** El debounce
(`ingest.py:277-296`) commitea el mensaje en el paso 8b y *después* duerme 5 s antes de
bumpear la versión. En esa ventana existe un inbound más nuevo que todavía no invalidó
nada:

```
t=99.2  cliente: "enviame una foto"   → /ingest → version N → n8n llama al LLM
t=100.0 /agent/action del turno anterior: el backend envía el resumen (sent_at=100)
t=102.0 cliente: "gracias"            → /ingest → se persiste y COMMITEA (paso 8b)
                                        …y duerme 5 s antes de bumpear la versión
t=104.0 /agent/action de la version N llega con user_confirmation=true
        → no hay 409: "gracias" todavía no bumpeó nada
        → "último inbound" = 102 > sent_at = 100 → la condición 3 PASA
        → entra el falso positivo del 08-01
```

El disparador real era el de t=99.2, **anterior** al resumen. Y esa ráfaga no es un caso
rebuscado: es el patrón para el que existe el debounce.

**Solución, sin tocar el workflow ni añadir columnas**: el ingest ya escribe
`strategy_snapshot` (JSONB, propiedad exclusiva del backend, reescrito en cada turno). Se le
añade el timestamp del mensaje disparador:

```python
strategy_snapshot={..., "trigger_message_at": msg_timestamp.isoformat()}
```

`/agent/action` lo lee del snapshot correspondiente al `strategy_version` que acaba de
validar. Queda atado por construcción al mensaje que el LLM estaba contestando.

### H2 — Dos relojes distintos en la condición 3

`order_summary_sent_at` lo pone el backend (`datetime.now(timezone.utc)`).
`Message.created_at` del inbound viene del `timestamp` de Meta (`ingest.py:252`). Son
relojes distintos. La evidencia del 08-01 (795 ms antes) depende justamente de eso, así que
la comparación es la correcta — pero un desfase de reloj rechazaría una confirmación
legítima.

**Decisión**: comparación estricta (`>`), y cada rechazo por esta causa emite
`warning:confirmation_rejected_inbound_predates_summary`. Es el único de los cuatro
rechazos que puede ser un falso negativo, así que se mide antes de endurecer o relajar
nada. Un `"sí"` real exige un ida y vuelta por WhatsApp; sub-segundo es implausible.

### H3 — Normalización de ciudad

Hasta hoy quien "resolvía" la ciudad era el LLM leyendo texto. A partir de §3 la resuelve
el backend con un lookup, y ahí `"medellin"`, `"Medellín"` y `"MEDELLÍN"` son tres claves
distintas. El ADR escribe `Medellin` sin tilde; `business_rules` de prod tiene `Medellín`
con tilde (`005:33`). Sin normalización, el cliente de Medellín cae en "por confirmar".

**Decisión: se normaliza.** `_normalize_city()` = casefold + quitar diacríticos + colapsar
espacios, aplicado a los **dos** lados del lookup (la clave de `business_rules` y lo que
escribió el cliente). Es un test obligatorio, no un detalle: el cliente escribe sin tilde
la mayoría de las veces.

### H4 — Qué exige el resumen para renderizarse

El ADR nombra los campos del fingerprint pero no dice cuáles son *requisito*.

**Decisión: la molienda es requisito de venta.** Grano o molido no es una preferencia
decorativa: es lo que se le entrega al cliente, y un pedido sin ella no se puede despachar.
Requisito completo = `product_id`, `quantity`, `grind_preference`, `full_name`, `phone`,
`shipping_city`, `shipping_address`. Sin los siete no hay resumen, y sin resumen no hay
confirmación posible.

**Y eso abre un hueco que hay que cerrar en el mismo cambio** (ver la sección siguiente):
de esos siete, dos —`quantity` y `grind_preference`— no son checkpoints del DAG, y al
directive se le ordena hoy explícitamente **no pedirlos**.

### H5 — Nadie pide `quantity` ni `grind_preference`, y ahora bloquean la venta

`quantity` y `grind_preference` son `ORDER_FIELDS` (`agent_action.py:60-62`): se capturan si
el cliente los menciona, y el directive tiene la instrucción literal *"Do NOT ask for these
proactively — only capture what they volunteer"* (`goal_strategy.py:80-85`, de P12). El
único sitio del sistema que hoy empuja a conseguir la cantidad es la sección **RESUMEN DE
CONFIRMACIÓN** del prompt (`009:225`: *"…Y el cliente ya haya indicado cuántas bolsas
quiere"*) — y la migración 014 la borra.

Con H4 decidido, el resultado sin más cambios sería: **la venta se estanca en silencio.**
Datos completos, backend esperando `grind_preference` para renderizar, LLM con órdenes de no
preguntarla, y ningún estado que refleje que falta algo. Peor que el bug que venimos a
arreglar, porque no deja rastro.

**Cierre**: el directive gana un estado previo al resumen. Cuando los 4 slots del DAG y
`product_id` están completos pero falta `quantity` o `grind_preference`, `next_action` pide
**uno** de los dos, el que falte primero. La orden de "no preguntar proactivamente" se
mantiene **solo en la fase pre-producto**, que es donde P12 la puso y donde tiene sentido
(`if "product_id" in self.missing_fields`, `goal_strategy.py:80`). No se contradice a P12:
se acota a su fase.

Alternativa descartada: convertirlos en checkpoints del DAG. Cambiaría `progress_pct`, el
`all_complete` que dispara el auto-escalate y el contrato de `strategy_snapshot`, para
resolver algo que el directive resuelve con dos líneas.

---

## Arquitectura del fix

Módulo nuevo `app/services/order_summary.py`, puro (sin I/O), en la línea de
`language.py` y `validation.py`. Todo lo que decide se testea sin DB.

```python
SUMMARY_REQUIRED = ("product_id", "quantity", "grind_preference", "full_name",
                    "phone", "shipping_city", "shipping_address")
FINGERPRINT_FIELDS = SUMMARY_REQUIRED           # los siete + el envío aplicado

@dataclass(frozen=True)
class Shipping:
    cost: Decimal | None
    status: str          # "fixed" | "to_confirm"

@dataclass(frozen=True)
class SummaryState:      # lo que hay persistido en conversations
    fingerprint: str | None
    sent_at: datetime | None

def normalize_city(raw: str) -> str
def resolve_shipping(city, shipping_rules) -> Shipping
def compute_total(quantity, unit_price, shipping) -> Decimal
def compute_fingerprint(context, shipping) -> str          # sha256 canónico
def should_render_summary(merged_ctx, state, unit_price, shipping) -> bool
def render_summary(merged_ctx, product, shipping, language) -> str
def evaluate_user_confirmation(prior_ctx, merged_ctx, accepted, state,
                               trigger_at, shipping) -> tuple[bool, str]
def order_mutations(prior_ctx, accepted) -> list[str]
```

`evaluate_user_confirmation` devuelve `(aceptada, razón)` con una razón por condición:
`no_summary`, `summary_stale`, `inbound_predates_summary`, `order_modified_this_turn`.
Cuatro razones distinguibles no son cosmética: son el instrumento con el que se sabrá, en
prod, cuál de las cuatro condiciones está haciendo el trabajo — y si alguna produce falsos
negativos.

Las condiciones 2 y 4 se solapan a propósito. La 4 atrapa la modificación **en este mismo
turno**; la 2 atrapa la que se persistió en un turno anterior sin que saliera resumen nuevo.

### Cambios en `agent_action.py`

`compute_context_updates` gana un parámetro `confirmation_verdict` (o recibe el
`SummaryState` + `trigger_at` y delega en `order_summary`). Se mantiene pura: el caller
consulta la DB y le pasa hechos, igual que hoy hace con `current_context`.

Flujo del turno, después del breaker. **El `SummaryState` se lee UNA vez, al entrar**, y
las cuatro condiciones se evalúan contra ese estado — nunca contra el resumen que este mismo
turno pueda estar escribiendo, o la condición 1 se auto-concedería (el mismo vicio del
08-19, ahora del lado del backend):

1. `merged = {**prior, **accepted}`
2. Resolver precio (`_fetch_product_price`) y envío (`resolve_shipping`).
3. **Gate de confirmación** (§5): si el LLM propuso `user_confirmation`, aplicar las cuatro
   condiciones. Rechazo → fuera de `strategy_updates` + `side_effect` con la razón.
4. **Invalidación** (§6): si `order_mutations(prior, accepted)` no está vacío y
   `prior.get("user_confirmation")` → escribir `user_confirmation = False` en el contexto.
5. **Render** (§1): si `should_render_summary(...)` → reemplazar `final_response_text`,
   escribir `order_summary_fingerprint` y `order_summary_sent_at`, y **persistir el outbound
   con el texto renderizado**, no con el del LLM. `side_effect: order_summary_sent`.

`should_render_summary` es idempotente por construcción: solo renderiza si no hay
fingerprint o si el nuevo difiere del guardado. **El backend nunca puede repetir el mismo
resumen**, y por eso el circuit breaker puede quedarse donde está, evaluando el texto del
LLM antes del merge: un resumen del backend no puede entrar en loop consigo mismo. (El
resumen sí queda en el historial de outbounds como cualquier otro mensaje; es inocuo.)

**Efecto deseado en el aviso Telegram**: con `False` persistido, una reconfirmación tras una
corrección vuelve a ser una transición y `is_new_user_confirmation` vuelve a emitir
`checkpoint_completed:user_confirmed`. El operador recibe aviso del pedido **corregido**,
no del viejo. Eso es correcto y hay que verificarlo, no suprimirlo.

### Envío por confirmar: qué dice el texto

Cuando `status == "to_confirm"` el resumen **no da un total**. Prometer un total que no
incluye el envío es exactamente el tipo de dato inventado que el ADR viene a eliminar:

> "El café son $80.000. El envío a Bogotá lo confirmamos contigo y te aviso el valor."

Formato de dinero propio de `order_summary` (`$80.000`, sin `COP`, según el ejemplo del
ADR). No reusar `prompt_context._format_price`, que anexa la moneda porque va al prompt.
Y **sin guion largo** en ninguna plantilla — hay un test que lo asegura.

---

## Migración: partida en 013 y 014 (2026-09-03)

> **Corrección al plan original.** Este brief pedía una sola migración con tres secciones y
> aplicarla entera antes de desplegar. **Es incorrecto**: las secciones tienen restricciones de
> orden opuestas. El DDL debe ir ANTES del despliegue (el ORM ya declara las columnas: sin
> ellas, el 100 % de los turnos devuelve 500 y n8n lo traga en silencio). La cirugía del prompt
> debe ir DESPUÉS (si el prompt deja de enseñar a redactar el resumen y el código todavía no lo
> renderiza, no lo manda nadie y la venta se estanca en silencio).
>
> Partida en `013_add_order_summary_state.sql` (DDL, **antes**) y
> `014_shipping_rules_and_summary_prompt.sql` (datos y prompt, **después**). La regla general
> quedó escrita en **ADR-012**, y el frente es **P33**.

## Contenido de las dos migraciones

El ADR dice, en §3, que las reglas de envío son "edición de datos, no migración". Eso
**contradice la convención del repo**: CLAUDE.md exige migración para cambios de
`business_rules` que afecten comportamiento y para cambios de `system_prompt_template`.
Sigo la convención del repo — sin archivo, el cambio no es reproducible en otro entorno ni
auditable. El ADR conserva su punto: no es DDL, y va en el mismo archivo.

Contenido original, ahora repartido:

**1. DDL** (lo único que cambia el schema)
```sql
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS order_summary_fingerprint VARCHAR(64);
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS order_summary_sent_at TIMESTAMPTZ;
```

**2. `business_rules.shipping_rules`** — reemplazo completo. Manizales `5000` fijo;
Medellín, Envigado, Sabaneta `15000` fijo. **Todo lo demás sale**: Pereira, Armenia, Bogotá,
Cali, Bucaramanga, Barranquilla, Cartagena y Santa Marta pierden su tarifa, y el bloque
`zones` entero se elimina. Esas ciudades pasan a `to_confirm` y el operador coordina el
valor a mano. Se añade `pickup: false`.

Decidido así a propósito: eran cifras de abril (`005:27-52`) que nunca se contrastaron
contra un envío real, y sostenerlas es inventar tarifas. El resumen dice explícitamente que
el envío se confirma, en vez de prometer un número.

**3. `system_prompt_template`** — cirugía sobre el template vivo (patrón de la 011,
idempotente con guard `LIKE`):
- Borrar la sección **PESO Y CANTIDAD** (`009:142-145`) y sustituirla por la regla dura:
  se vende en bolsas de 340g, no se hacen conversiones ni se ofrecen.
- Borrar **RESUMEN DE CONFIRMACIÓN** completa, con su ejemplo y su total (`009:224-244`),
  y **NO DUPLIQUES EL RESUMEN**. Ojo: ahí vive la única instrucción del sistema que hoy
  empuja a conseguir la cantidad — su relevo es el directive (H5), no otra línea de prompt.
- Ajustar **FLUJO DE COMPRA** paso 3 ("Presentas el resumen" → el resumen lo envía el
  sistema).
- Dejar intacta la línea de `user_confirmation` en EXTRACCIÓN DE DATOS: §5 conserva la
  propuesta del LLM.

> **Esta tercera sección no es limpieza de prompt, es parte del fix.** Mientras el prompt
> siga enseñando a redactar resúmenes con total, el LLM los redactará en los turnos en que
> el backend *no* renderiza (fingerprint sin cambios) y su texto saldrá sin reemplazar. Se
> volvería a prometer dinero calculado por el modelo, justo lo que §2 elimina.

**Regresión deliberada a verificar**: hoy el bot cotiza Pereira $10.000, Armenia $10.000,
Bogotá $18.000, Cali $18.000, Bucaramanga, Barranquilla, Cartagena, Santa Marta y un bloque
de zonas. Con la 014 todas pasan a "por confirmar". Es lo que el ADR decide
("automatizar antes de tener los datos sería inventar tarifas") y son cifras de abril que
nunca se contrastaron contra un envío real — pero es un cambio visible para el cliente y
debe anunciarse al negocio antes de aplicar, no descubrirse en una conversación.

---

## Otros archivos que hay que tocar (o el fix queda a medias)

- `goal_strategy.py:287` — el directive todavía ordena *"Present an order summary with all
  the collected data"*. El backend estaría mandando el resumen y el prompt pidiéndole al LLM
  que lo mande también. Pasa a: esperar la respuesta del cliente al resumen que el sistema
  ya envió.
- `goal_strategy.py:64-68` — la rama `all_complete` sugiere "confirm with the customer and
  close politely". Revisar que no reintroduzca el resumen.
- `goal_strategy.py:80-85` + `_action_text` — **el cierre de H5**: acotar la orden de "no
  preguntar proactivamente" a la fase pre-producto (donde ya está condicionada) y añadir el
  estado previo al resumen que pide `quantity` o `grind_preference` cuando son lo único que
  falta. Sin esto, H4 estanca la venta en silencio.
- `prompt_context.py:44-75` — el bloque de envíos empieza con *"all values are approximate,
  pending carrier confirmation"* y escribe `aprox.` en cada ciudad. §3 dice "sin aprox" para
  las fijas. Reescribir con la forma nueva (`fixed` vs `to_confirm`) y añadir la línea de
  que no hay recogida en la finca.
- `models/core.py` — las dos columnas nuevas en `Conversation`.
- `ingest.py:352` — `trigger_message_at` en `strategy_snapshot` (H1).
- `CLAUDE.md` — sección "DAG gates" (el gate de `user_confirmation` cambia de naturaleza),
  schema post-014, estado de P15.
- `docs/ROADMAP.md` — resolver la referencia colgada: hoy `ROADMAP:539-540` y `:640` dan el
  número 010 al ADR de P10, que nunca se escribió. Aplicar el precedente de ADR-008 que el
  propio ADR-010 invoca: 010 queda tomado, P10 toma el siguiente libre cuando se escriba.

---

## Fases

Una sola PR, backend puro. Las fases son de orden de trabajo, no de despliegue.

**Fase 1 — `order_summary.py` + tests.** El módulo puro entero, contra los 5 casos
históricos. Sin tocar `agent_action` todavía. Al final de esta fase el fix está *probado*
aunque no esté *conectado*.

**Fase 2 — Migraciones 013 y 014 + modelo + `ingest.py`.** DDL, datos, prompt, columnas ORM,
`trigger_message_at`.

**Fase 3 — Cableado en `agent_action.py`.** Gate, invalidación, render, reemplazo del
outbound, side_effects.

**Fase 4 — Prompt/directive y docs.** `goal_strategy`, `prompt_context`, CLAUDE.md, ROADMAP.

**Fase 5 — Despliegue en cuatro pasos, en este orden** (manual vía psql, transacción única con
verificación, como la 011 y la 012):

1. Aplicar **013** (DDL). Es aditiva y nullable, así que es segura con el código viejo corriendo.
2. Mergear y desplegar. El CI construye y actualiza el Container App solo.
3. Aplicar **014** (reglas de envío y prompt), con la verificación de longitud del template.
4. Verificar en una conversación real: resumen del backend con total correcto, confirmación
   aceptada solo después, y una corrección que invalida y re-resume.

Invertir 1 y 2 deja producción con el 100 % de los turnos en 500. Invertir 2 y 3 deja la venta
estancada sin que nadie mande el resumen.

---

## Tests obligatorios

`tests/services/test_order_summary.py` (nuevo):

1. **Los 5 casos históricos como regresión**, cada uno con su razón esperada:
   `"2"` → `order_modified_this_turn`; `"Unidad campestre sorry"` →
   `order_modified_this_turn`; `"enviame una foto"` → `inbound_predates_summary`;
   `"Barrio El Campin"` → `no_summary`; y `"Si gracias"` (07-15) → **aceptada**.
   El quinto es tan obligatorio como los cuatro: un gate que rechaza todo también da 0
   falsos positivos.
2. Fingerprint: sensible a los 8 componentes, uno por uno; `"2"` y `2` producen el mismo
   (el LLM manda ambos); estable entre llamadas.
3. `normalize_city`: `"medellin"`, `"Medellín"`, `"MEDELLIN"`, `" Medellín "` → $15.000.
   Ciudad desconocida → `to_confirm`.
4. Total: con envío fijo incluye el envío; con `to_confirm` **no hay total en el texto**.
5. Render: sin `product_id` no hay resumen (H6); **sin `grind_preference` tampoco** (H4);
   sin guion largo; es/en; formato `$80.000`.
6. Idempotencia: mismo fingerprint → `should_render_summary` es False.

`tests/services/test_agent_action.py` (ampliar):

7. La invalidación escribe `user_confirmation = False` (no borra la clave) y el fingerprint
   viejo deja de valer.
8. Tras la invalidación, la reconfirmación vuelve a ser transición
   (`is_new_user_confirmation` → True): el operador recibe aviso del pedido corregido.
9. El filtro `and v` de `agent_action.py:161-162` **sigue descartando** un
   `user_confirmation: false` propuesto por el LLM. Es un guard de no-regresión: el LLM
   no gana autoridad de desmarcado por la puerta de atrás.
10. `_USER_CONFIRMATION_REQUIRES` **no** incluye `product_id` (H6 se resuelve
    estructuralmente, no por parche).

`tests/services/test_goal_strategy.py` (ampliar):

11. **H5**: con los 4 slots + `product_id` completos y `quantity` o `grind_preference`
    ausentes, el directive los pide. Y la orden de "no preguntar proactivamente" sigue
    apareciendo en la fase pre-producto (no-regresión de P12).

---

## Riesgos

| Riesgo | Mitigación |
|---|---|
| Falso negativo por desfase de relojes (H2) | Side_effect propio por esa razón; se mide antes de tocar nada |
| El resumen renderizado suena robótico en el momento más importante | El ADR lo anticipa: ajustar plantilla, nunca devolverle la redacción al LLM |
| Ciudades que pierden tarifa cotizada, y Manizales que baja de $7.000 a $5.000 | Cambio visible para el cliente. **Confirmado por el dueño el 2026-09-03**, y anotado en el encabezado de la 014 |
| H5 sin cerrar → venta estancada sin rastro | El directive pide `quantity`/`grind_preference`; test 11 lo cubre |
| El LLM redacta su propio resumen en turnos sin render | Se corta en la 014 (quitar RESUMEN DE CONFIRMACIÓN del prompt), que por eso va DESPUÉS del despliegue |
| n8n manda `send_image_url` en el mismo turno del resumen | El backend no gestiona esa clave (la lee n8n del LLM). Iría foto + resumen juntos. Cosmético; registrar si se ve |
| Migración manual mal aplicada | Patrón 011/012: transacción única con verificación en el mismo `psql` |

---

## Definition of done

- [ ] `pytest` completo en verde (el gate de CI corre la suite entera desde PR #64).
- [ ] Los 5 casos históricos cubiertos, con el legítimo pasando.
- [ ] 013 aplicada en prod **antes** del despliegue, y 014 **después**, cada una con su
      verificación registrada en su encabezado `-- Applied:`.
- [ ] Verificado en una conversación real: resumen del backend con total correcto,
      confirmación aceptada solo después, y una corrección que invalida y re-resume.
- [ ] Verificado que el bot pide la molienda y la cantidad cuando son lo único que falta
      (H5): una venta que se estanca ahí es el modo de fallo nuevo que introduce este cambio.
- [ ] ADR-010 pasa de **Propuesto** a **Aceptado**.
- [ ] Referencia colgada del ROADMAP al "ADR-010 de P10" resuelta.
- [ ] CLAUDE.md actualizado; P15 cerrado en el ROADMAP.
