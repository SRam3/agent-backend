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

`/agent/action` no recibe el mensaje entrante. La tentación es consultar el último inbound
de la conversación, y **es incorrecta**: el debounce (`ingest.py:293`) commitea el mensaje
en el paso 8b y *después* duerme 5 s antes de bumpear `strategy_version`. Durante esa
ventana existe un inbound más nuevo que todavía no invalidó nada, así que "el último
inbound" puede ser uno posterior al que originó este turno — y la condición 3 pasaría por
un mensaje que el cliente aún no ha visto contestado.

**Solución sin tocar n8n ni añadir columnas**: el ingest ya escribe `strategy_snapshot`
(JSONB, propiedad exclusiva del backend, reescrito en cada turno). Se le añade el timestamp
del mensaje disparador:

```python
strategy_snapshot={..., "trigger_message_at": msg_timestamp.isoformat()}
```

`/agent/action` lo lee del snapshot que corresponde al `strategy_version` que acaba de
validar. Queda atado por construcción al mismo turno: si hubiera otro inbound, habría otro
`strategy_version` y la llamada habría dado 409 (ADR-003).

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

`_normalize_city()`: casefold + quitar diacríticos + colapsar espacios, aplicado a los dos
lados del lookup. Es un test obligatorio, no un detalle.

### H4 — Qué exige el resumen para renderizarse

El ADR nombra los campos del fingerprint pero no dice cuáles son *requisito*.
`grind_preference` entra en el fingerprint y aparece en el ejemplo ("en grano"), pero no es
checkpoint del DAG ni necesario para la aritmética.

**Decisión**: requisito = `product_id`, `quantity`, `full_name`, `phone`, `shipping_city`,
`shipping_address`. La molienda es **opcional en el texto**: si no está, la cláusula no se
escribe; cuando llegue, cambia el fingerprint y sale un resumen nuevo — que es exactamente
el ciclo del §6. Alternativa descartada: exigirla y quedarnos sin resumen (y por tanto sin
venta posible) porque el cliente nunca dijo si la quería en grano.

---

## Arquitectura del fix

Módulo nuevo `app/services/order_summary.py`, puro (sin I/O), en la línea de
`language.py` y `validation.py`. Todo lo que decide se testea sin DB.

```python
SUMMARY_REQUIRED = ("product_id", "quantity", "full_name", "phone",
                    "shipping_city", "shipping_address")
FINGERPRINT_FIELDS = SUMMARY_REQUIRED + ("grind_preference",)   # + el envío aplicado

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

Flujo del turno, después del breaker:

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

## Migración 013

El ADR dice, en §3, que las reglas de envío son "edición de datos, no migración". Eso
**contradice la convención del repo**: CLAUDE.md exige migración para cambios de
`business_rules` que afecten comportamiento y para cambios de `system_prompt_template`.
Sigo la convención del repo — sin archivo, el cambio no es reproducible en otro entorno ni
auditable. El ADR conserva su punto: no es DDL, y va en el mismo archivo.

`013_backend_gobierna_resumen.sql`, tres secciones:

**1. DDL** (lo único que cambia el schema)
```sql
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS order_summary_fingerprint VARCHAR(64);
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS order_summary_sent_at TIMESTAMPTZ;
```

**2. `business_rules.shipping_rules`** — reemplazo completo. Manizales `5000` fijo;
Medellín, Envigado, Sabaneta `15000` fijo; el resto `to_confirm`. Se elimina el bloque
`zones` entero y se añade `pickup: false`.

**3. `system_prompt_template`** — cirugía sobre el template vivo (patrón de la 011,
idempotente con guard `LIKE`):
- Borrar la sección **PESO Y CANTIDAD** (`009:142-145`) y sustituirla por la regla dura:
  se vende en bolsas de 340g, no se hacen conversiones ni se ofrecen.
- Borrar **RESUMEN DE CONFIRMACIÓN** completa, con su ejemplo y su total (`009:224-244`),
  y **NO DUPLIQUES EL RESUMEN**.
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
de zonas. Con la 013 todas pasan a "por confirmar". Es lo que el ADR decide
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
- `prompt_context.py:44-75` — el bloque de envíos empieza con *"all values are approximate,
  pending carrier confirmation"* y escribe `aprox.` en cada ciudad. §3 dice "sin aprox" para
  las fijas. Reescribir con la forma nueva (`fixed` vs `to_confirm`) y añadir la línea de
  que no hay recogida en la finca.
- `models/core.py` — las dos columnas nuevas en `Conversation`.
- `ingest.py:352` — `trigger_message_at` en `strategy_snapshot` (H1).
- `CLAUDE.md` — sección "DAG gates" (el gate de `user_confirmation` cambia de naturaleza),
  schema post-013, estado de P15.
- `docs/ROADMAP.md` — resolver la referencia colgada: hoy `ROADMAP:539-540` y `:640` dan el
  número 010 al ADR de P10, que nunca se escribió. Aplicar el precedente de ADR-008 que el
  propio ADR-010 invoca: 010 queda tomado, P10 toma el siguiente libre cuando se escriba.

---

## Fases

Una sola PR, backend puro. Las fases son de orden de trabajo, no de despliegue.

**Fase 1 — `order_summary.py` + tests.** El módulo puro entero, contra los 5 casos
históricos. Sin tocar `agent_action` todavía. Al final de esta fase el fix está *probado*
aunque no esté *conectado*.

**Fase 2 — Migración 013 + modelo + `ingest.py`.** DDL, datos, prompt, columnas ORM,
`trigger_message_at`.

**Fase 3 — Cableado en `agent_action.py`.** Gate, invalidación, render, reemplazo del
outbound, side_effects.

**Fase 4 — Prompt/directive y docs.** `goal_strategy`, `prompt_context`, CLAUDE.md, ROADMAP.

**Fase 5 — Aplicar 013 en prod** (manual vía psql, transacción única con verificación de
longitud del template, como la 011 y la 012) y desplegar.

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
5. Render: sin `product_id` no hay resumen (H6); sin guion largo; es/en; sin molienda la
   cláusula desaparece; formato `$80.000`.
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

---

## Riesgos

| Riesgo | Mitigación |
|---|---|
| Falso negativo por desfase de relojes (H2) | Side_effect propio por esa razón; se mide antes de tocar nada |
| El resumen renderizado suena robótico en el momento más importante | El ADR lo anticipa: ajustar plantilla, nunca devolverle la redacción al LLM |
| Ciudades que pierden tarifa cotizada | Anunciar al negocio antes de aplicar la 013 |
| El LLM redacta su propio resumen en turnos sin render | Se corta en la sección 3 de la 013 (quitar RESUMEN DE CONFIRMACIÓN del prompt) |
| n8n manda `send_image_url` en el mismo turno del resumen | El backend no gestiona esa clave (la lee n8n del LLM). Iría foto + resumen juntos. Cosmético; registrar si se ve |
| Migración manual mal aplicada | Patrón 011/012: transacción única con verificación en el mismo `psql` |

---

## Definition of done

- [ ] `pytest` completo en verde (el gate de CI corre la suite entera desde PR #64).
- [ ] Los 5 casos históricos cubiertos, con el legítimo pasando.
- [ ] 013 aplicada en prod, con la verificación de longitud del template registrada en el
      encabezado `-- Applied:`.
- [ ] Verificado en una conversación real: resumen del backend con total correcto,
      confirmación aceptada solo después, y una corrección que invalida y re-resume.
- [ ] ADR-010 pasa de **Propuesto** a **Aceptado**.
- [ ] Referencia colgada del ROADMAP al "ADR-010 de P10" resuelta.
- [ ] CLAUDE.md actualizado; P15 cerrado en el ROADMAP.
