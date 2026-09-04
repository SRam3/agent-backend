# Auditoría — ROADMAP y planeación (2026-09-01)

**Fecha de la sesión**: 2026-09-01 (Bogotá) · **Ventana de ejecución**: 2026-09-02 00:11–00:40 UTC
**Objeto**: refutar el `ROADMAP.md` con evidencia — alcance mal dimensionado, frentes ya resueltos,
duplicados, dependencias no declaradas, frentes sin evidencia, y problemas reales sin registrar.
**Encargo**: auditar y corregir el plan existente, nunca escribir un plan paralelo.

**Modo de ejecución**: READ-ONLY estricto. Sesión Postgres con `default_transaction_read_only = on`
(verificado con `SHOW` como primer comando de la sesión); contra n8n solo `GET /api/v1/workflows[/{id}]`
y `GET /api/v1/executions`; contra Azure solo `az containerapp show`, `az containerapp logs show` y
`az keyvault secret show`. Cero escrituras, cero cambios en workflows, cero despliegues, cero ediciones
del repo durante la auditoría. Este documento es el único artefacto que produce.

**Convención de evidencia**: `[DB]` = confirmado con SELECT contra la Postgres viva; `exec NNNN` =
ejecución de n8n leída por API; `[n8n]` = leído de la definición viva del workflow; `[az]` = leído de
Azure; `archivo:línea` = verificado en el repo. Timestamps en UTC. PII enmascarada.

**Rama de origen**: `origin/main` en `055a885` (2026-08-29). Continúa
`diagnostico-2026-08-29-comprobante-ciego-y-direccion-perdida.md`, que cerró su ventana el 08-26.

---

## 0. Resumen — tres cosas que cambian el plan, y una que lo reordena

1. **La compaction lleva 80 días rota por una clave de OpenAI inválida, y la causa estaba en el log
   desde que P4 la hizo observable.** El fix es rotar un secreto (§2.1). Cierra la causa raíz de P4 y
   la deuda #7, y retira evidencia que hoy se le atribuye a P21 y a P24.
2. **La migración 013 rompe producción si se mergea antes de aplicarse**, y sus tres secciones tienen
   restricciones de orden opuestas entre sí (§2.2). El CI decide el orden por ti.
3. **La colisión bot/operador ocurrió por tercera vez el 2026-08-30**, tres días antes de esta
   auditoría, sobre una conversación que sigue `active` (§4.1). No está en ningún documento.
4. **El orden de cierre trabaja en lo interesante antes que en lo que desbloquea ventas**: P16 (bajar
   bytes) y P22 (motor sin LLM) están por delante de callar al bot cuando el humano atiende y de
   avisar cuando el sistema falla (§10).

---

## 1. Estado del repo y del sistema vivo

### 1.1 Dos ROADMAP divergentes, y solo diez ADRs

- `main` local: `3c1d5eb` (2026-08-17). `origin/main`: `055a885` (2026-08-29). La rama
  `feat/adr-010-backend-gobierna-resumen` sale de `794612b`, que es el merge-base con `origin/main`.
- Existen **diez ADRs**, no once. `ADR-010-backend-gobierna-resumen.md` vive **solo** en esa rama sin
  mergear, con estatus **`Propuesto`**, y `docs/decisions/README.md` sigue diciendo que *«010 está
  reservado»* para P10. En `origin/main` la entrada de P10 ya dice lo contrario
  (`ROADMAP.md:575-577`: «no existe ningún `ADR-010-*.md`»). Las dos afirmaciones no pueden ser
  ciertas después del merge.
- El merge va a chocar en la entrada de P10 y en el contador de frentes.

### 1.2 Drift de contadores en el propio ROADMAP

`ROADMAP.md:49` dice *«Frentes nuevos toman el siguiente número libre — hoy, **P31**»* y
`ROADMAP.md:707` dice *«Siguiente número libre: **P30**»*, con P30 ya asignado en la tabla
(`:705`). En la rama de ADR-010 la misma pareja dice P28 y P30. El registro canónico se contradice
consigo mismo en el mismo archivo.

### 1.3 Sistema vivo

| Pieza | Estado verificado |
|---|---|
| `ca-backend` | revisión `0000057`, imagen `sebra3/sales-agent-api:caabe894…`, `minReplicas: 1` `[az]` |
| Código desplegado | `caabe89` = PR #64; **incluye** el guard de contenido ilegible y el fix de deuda #13 |
| Postgres | 16.14, `TimeZone = UTC`, 6 tablas, **sin** tabla de versiones de migración `[DB]` |
| Prompt vivo | `len 17261`, `md5 3e14d875b0dabeb864dd6fc689440753`, `updated_at 2026-07-31` = migración 011 `[DB]` |
| Migración 013 | **NO aplicada**: `conversations` tiene 16 columnas, sin `order_summary_*` `[DB]` |
| `master`, `cafe_arenillo_v2`, `operator_confirm_telegram`, `max_natural` | los cuatro `active: true` `[n8n]` |
| Inventario | 39 `client_users` (6 con bsuid, 1 sin teléfono), 53 conversaciones, 673 mensajes, 647 audit `[DB]` |

**El export de n8n del repo ya no es el workflow vivo.** `cafe_arenillo_v2` vivo tiene
`updatedAt: 2026-08-29T23:38:06.112Z`; el export `n8n_workflow/cafe_arenillo_v2.json` es del
2026-08-18T12:37:31.419Z. El diff funcional es nulo (dos nodos Telegram perdieron `resource` y
`operation`, que n8n rellena por default), pero **el archivo que el propio ROADMAP declara «único
respaldo, n8n no tiene historial de versiones» dejó de ser byte-idéntico**, y no consta quién lo
editó ni con qué intención. No determinable por la API sin el token MCP de instancia.

---

## 2. Refutaciones al ROADMAP, con evidencia

### 2.1 P4 / deuda #7 — la causa raíz estaba en el log, y es una clave inválida

`ROADMAP.md:601` y `:679` dicen «causa raíz aún pendiente de leer del log de prod». Se leyó.

```
2026-08-30T21:53:34.869Z  ERROR app.services.conversation_summary
summarize_conversation: LLM call FAILED for 420b5d3b… (failure #3 this process)
— AuthenticationError: Error code: 401 - {'error': {'message':
'Incorrect API key provided: <redactado>', 'code': 'invalid_api_key'}}
```
`[az containerapp logs show -n ca-backend -g rg-backend --type console]`, stack en
`conversation_summary.py:220` → `:163`.

Es la **candidata 2** del `diagnostico-2026-06-14-P4-compaction.md` («auth inválida — la key carga
pero está expirada o es de otra org»), que aquel diagnóstico rankeó segunda tras el egress. El
backend resuelve `OPENAI_API_KEY` del secreto `openai-key` de Key Vault (`main.py:64-78`); la
Container App no tiene esa variable en el entorno `[az]`, así que viene del vault. n8n llama a OpenAI
con **otra** credencial (`admin_key_open_ai`) y por eso el bot conversa mientras la memoria muere.

Consecuencia en datos, hoy: **0 de 39** `client_users` con `last_conversation_summary`, 29 perfiles
vacíos `[DB]`. Y consecuencia sobre el plan: parte de la evidencia que hoy sostiene a **P21** y a
**P24** es de esta clave, no del prompt ni de la falta de `purchase_intents` — el Lodge re-saludado el
08-19 y el cliente `dd842508` con perfil vacío tras conversaciones el 08-28 y el 08-30 son casos de
esto. **Rotar el secreto es la corrección de mayor efecto y menor riesgo del inventario completo.**

### 2.2 ADR-010 / migración 013 — el orden de despliegue no está resuelto y rompe prod

El modelo ORM de la rama declara las dos columnas nuevas (`models/core.py:193-194`). Cada
`select(Conversation)` de `ingest` y de `agent_action` las pediría. Producción no las tiene `[DB]`.
`.github/workflows/docker-publish.yml` despliega **automáticamente** al mergear a `main` cuando cambia
`sales_agent_api/**`. Secuencia si se mergea antes de aplicar la 013: 100 % de los turnos devuelven
500, `POST Ingest Message` lo traga por `continueOnFail: true`, y **todas las ejecuciones se marcan
`success`**. Es la deuda #12 amplificando un fallo total.

Y las tres secciones de la 013 tienen restricciones **opuestas**:

| Sección | Contenido | Cuándo debe aplicarse |
|---|---|---|
| 1 · DDL | dos `ADD COLUMN IF NOT EXISTS` | **antes** del despliegue |
| 2 · `business_rules` | reemplazo de `shipping_rules`, `presentation` | con o después |
| 3 · `system_prompt_template` | quita RESUMEN DE CONFIRMACIÓN y las conversiones | **después** del despliegue |

Si la sección 3 entra antes que el código, nadie redacta el resumen: el prompt ya no enseña a hacerlo
y el backend todavía no lo hace. El brief (`brief-impl-ADR-010…:376`, Fase 5) dice «aplicar 013 en
prod y desplegar», en ese orden y sin distinguir secciones. La 012 **sí** se diseñó retrocompatible y
lo documentó; esta no.

### 2.3 ADR-010 §3 baja el envío de Manizales de $7.000 a $5.000, y nadie lo anunció

`business_rules.shipping_rules` en prod: `"Manizales": {"cost": 7000, "method": "domicilio"}` `[DB]`,
vigente desde la migración 005. La venta del 08-19 cotizó «2×40.000 + ~7.000 ≈ 87.000». La 013
(`013_backend_gobierna_resumen.sql:69`) la fija en **5.000**. El brief anuncia como cambio visible
las ciudades que *pierden* tarifa, pero no el cambio de precio de la ciudad principal. O es decisión
de negocio y debe constar, o es un error y el ADR necesita nota as-built antes de aplicarse.

### 2.4 «n8n conserva ~4 días» — la frase confunde ventana con días con tráfico

El 2026-09-01 la ejecución más antigua de `master`/`cafe_arenillo_v2` es la **10474**, del
2026-08-19T19:35:55Z: **13 días** de historia del pipeline, no 4. Lo que el diagnóstico del 08-22
listó como «4 días» eran los días **con tráfico** (08-15, 16, 18 y 19), no la retención. Hoy los días
con tráfico son seis: 08-19, 23, 26, 28, 29 y 30.

El mecanismo de poda **no es determinable**: sobreviven ejecuciones de otros workflows desde
2025-09-15 (ids 1345…) mientras las del pipeline anteriores al 08-19 desaparecieron. Los conteos
siguen siendo cota inferior para todo lo anterior al 08-19; la corrección es que la ventana es tres
veces mayor de lo escrito, y que la frase debe decir «días con tráfico».

### 2.5 P5 — el 409 no mata la ejecución; el mecanismo documentado es incorrecto

`CLAUDE.md:307` y la entrada de P5 dicen que un 409 «mata la ejecución → drop silencioso».
`POST Agent Action` lleva **`continueOnFail: true`** `[n8n]`, igual que el ingest. Un 409 se convierte
en un item con `error`, cae al `else` de `Process Backend Response` como
`suppressed_reason: "backend_error"` y la ejecución termina **`success`**. El drop es real; el
mecanismo no. Lo que falta no es `onError`: es **una alerta**.

Y el 409 **nunca ha ocurrido**: 0 en las 268 ejecuciones retenidas del pipeline `[n8n]`. Lo que sí
ocurrió son cinco 500 del ingest (execs 10452, 10566, 10600, 10612, 10650), todos del 08-19, todos en
ejecuciones `success`, ya corregidos por deuda #13.

### 2.6 P18 y P19 — resueltos desde el 2026-07-31, con evento en audit_log

`ROADMAP.md:412-421` y `CLAUDE.md:363` dicen que queda «el UPDATE puntual del e2e
(`purchase_count: 2`)». Ese UPDATE se hizo:

```
2026-07-31 20:31:19.716909+00  profile_corrected  operator  15d89710
{"reason":"duplicate_sale_cleanup","purchase_count_before":2,"purchase_count_after":1,
 "removed_by_path":"legacy_llm_payment_confirmation","fix":"PR #57"}  [DB]
```

El `purchase_count: 2` que hoy tiene `15d89710` son **dos compras distintas y legítimas** (07-20 y
08-01), no el duplicado. No hay filas en estado inconsistente: las 4 conversaciones con
`payment_confirmation` están las 4 en `closed` `[DB]`. **P18 y P19 se cierran sin implementar nada.**

### 2.7 P11 — la fase B2 está hecha

`CLAUDE.md:326` lista como pendiente «quitar `payment_confirmation` del nodo Build LLM Prompt». La
lista viva de `Valid extracted_data keys` de ese nodo **no la contiene** `[n8n]`. Además, en toda la
historia el LLM solo propuso `payment_confirmation` **una vez** (`messages.extracted_data`, n=1)
`[DB]`, y el gate la descartó.

### 2.8 P16 — el alcance está sobredimensionado por un episodio bot-a-bot

Medición completa, no cota `[DB]`:

| `message_type` | inbound | vacíos |
|---|---:|---:|
| text | 301 | 0 |
| unsupported | 35 | 35 |
| image | 14 | 14 |
| audio | 5 | 5 |
| reaction | 2 | 2 |
| edit | 1 | 1 |
| revoke | 1 | 1 |
| **total** | **359** | **58 (16,2 %)** |

Pero **39 de los 58 no son medios** (`unsupported`, `reaction`, `revoke`, `edit`) y ya están
resueltos por el guard. Los medios son 19 mensajes (5,3 %), y **7 de las 14 imágenes pertenecen a un
solo episodio bot-a-bot** (conv `e59e6100`, 08-15). El material real de clientes en cinco meses es del
orden de una docena de mensajes, sobre un catálogo de **un** producto. Ninguna de las tres imágenes
retenidas trae `caption` `[n8n]`. «Bajar los bytes» no está dimensionado como el 15,6 % que dice la
entrada.

### 2.9 P7 — la carrera de fondo no reaparece desde el fix, y el caso texto→imagen ya está muerto

En la ventana posterior al 2026-08-23 no hay ninguna carrera del debounce `[DB]`. El par del 08-26
21:55:07 que parecía doble respuesta fue una coalescencia **correcta**: «mándame una bolsa en grano»
no tiene evento `message_ingest`, es decir el turno se suprimió. Los pares a 5–12 s no son carrera:
son el tamaño de la ventana. Y el caso «un texto precede a una imagen» que la entrada describe quedó
neutralizado por otra vía: el lookahead del debounce filtra por contenido legible
(`ingest.py:307-311`), así que un medio ya no le cede el turno a nadie.

Lo que sí queda es un caso distinto y de una línea, ver §5.

### 2.10 P10 — la amenaza ya se materializó dos veces, y el breaker bastó las dos

`ROADMAP.md:661` dice «cuando la amenaza se materialice». Ocurrió el 2026-07-18 (bot de vuelos) y el
2026-08-15 (bot de soporte de telecomunicaciones, conv `e59e6100`): 28 inbound en 4 minutos, 16
outbound, breaker disparado a las 14:06:28 `[DB]`. De esos 28 inbound, **23 eran ilegibles** (16
`unsupported`, 7 `image`): con el guard vivo desde el 08-23, ese episodio produciría 5 turnos en vez
de 28. El guard desactivó la mayor parte del caso observado de P10 sin proponérselo.

### 2.11 «La imagen del 08-20 perdida por 404» no es un caso de reloj

La hipótesis de relojes la cita como instancia del vencimiento de la URL firmada. El propio
`diagnostico-2026-08-22…:§3` estableció que a esa hora **el webhook llevaba tres horas apagado a
propósito**: el 404 es del path del webhook, no de `lookaside`. **No existe ningún caso medido de
pérdida por vencimiento de URL**, porque nadie descarga bytes todavía. Los 301–302 s son un dato de
diseño para P16, no un fallo ocurrido.

### 2.12 La base de evidencia de ventas es más pequeña de lo que sugieren los documentos

Hay 4 conversaciones con `payment_confirmation`, en 3 `client_users` `[DB]`. **Dos de las cuatro**
(07-20 y 08-01) son del mismo `client_user` `15d89710`, que acumula 10 conversaciones, aparece en los
e2e y cuyo interlocutor el bot llama «Sebastián». Si esa es la identidad del dueño, las ventas a
clientes externos son **dos**: 08-19 (`90aa2b87`) y 08-26 (`ba58a211`). **No determinable desde los
datos**; lo confirma o lo desmiente el dueño. Importa porque varios frentes se priorizan citando «la
venta real del 08-01».

---

## 3. Dependencias no declaradas

La entrada de P7 ya declara la conocida (deuda #13 antes que el guard de P16). Faltan:

| Antes | Después | Por qué |
|---|---|---|
| 013 sección 1 (DDL) | merge de ADR-010 | sin las columnas, todo turno da 500 (§2.2) |
| despliegue de ADR-010 | 013 sección 3 (prompt) | invertido, nadie redacta el resumen (§2.2) |
| **P4** (rotar la clave) | **P24** y **P21** | `pending_intent` existe (`ingest.py:505-510`) y nunca corrió; decidir `purchase_intents` sin ver funcionar la continuidad es decidir a ciegas |
| **P5** (alerta) | **P16** (bytes) | una descarga puede fallar a los 301 s y hoy no hay ninguna rama de error salvo dos `continueOnFail` |
| **ADR-010** | **P28** | P28 es la tesis del ADR aplicada a otro dato; su título ya dice «y los datos operacionales» |
| mecanismo determinista de consentimiento | **P26** «captura mínima» | una frase en el prompt más extracción del LLM es exactamente el hecho-afirmado-por-interpretación que P15 acaba de cerrar |
| re-export de los tres workflows | cualquier trabajo en n8n | el respaldo declarado dejó de ser el vivo (§1.3) |

Y una corrección de alcance: **P16 (bytes) obliga a tocar el `master`, no solo `cafe_arenillo_v2`.**
El `Set` whitelist `map_webhook_data_arenillo` copia diez campos y **ninguno es el objeto
`image`/`audio`** `[n8n]`: la URL de descarga nunca llega al sub-workflow. Ese nodo fue la causa
primaria del drop de P14; la entrada de P16 lo marca como `[N8N]` genérico.

---

## 4. Problemas reales que el ROADMAP no registra

### 4.1 La colisión bot/operador ocurrió el 2026-08-30, y la conversación sigue abierta

Los echoes del operador **sí llegan** al webhook y mueren en `If Message Exists` (establecido el
08-22). Lo nuevo es que **no se detuvieron el 08-19**: hay **17 echoes** en la retención `[n8n]`:

| Fecha | Echoes | Contexto |
|---|---:|---|
| 2026-08-19 | 10 | colisión documentada + cierre logístico |
| 2026-08-26 | 4 | 17:38:09 → 17:40:01, alrededor del `sale_closed` de las 17:38:38 |
| 2026-08-29 | 1 | 23:05:37 |
| **2026-08-30** | **2** | **22:24:50 y 22:48:28** |

La secuencia del 08-30, conversación `d5c070c9` (cliente `dd842508`) `[DB + n8n]`:

```
22:24:50  🧑 operador escribe (echo, 38 chars)        exec 11506 → Stop
22:27:06  cliente responde "solo tienen honey o…"     [DB]
22:27:17  🤖 el bot contesta                          exec 11511 · agent_turn
22:48:28  🧑 operador escribe otra vez (72 chars)     exec 11518 → Stop
```

**Tercera ocurrencia real, la más reciente, tres días antes de esta auditoría.** La conversación sigue
`active` en `v2`: si ese cliente vuelve a escribir, el bot vuelve a contestar en paralelo con el
humano. Es P29 y la deuda #14, pero el ROADMAP los describe como un evento del 08-19 con una segunda
ocurrencia el 08-26; son **tres**, y el patrón es que el operador atiende a mano de forma rutinaria.

### 4.2 Cerrar la venta no silencia al bot — con una corrección al diagnóstico del 08-29

`ROADMAP.md:272-282` registra dos ocurrencias. Con el guard vivo, **solo una sigue siendo
alcanzable**:

- **08-19 22:15**, conv `d7c70f32`: el disparador fue un **audio** (`content=''`). Hoy el guard lo
  suprime — verificado con el `revoke` del 08-29 (exec 11435: `should_respond: false`,
  `reason: unreadable_content`, 0,12 s, sin llamada al LLM) `[n8n]`. **Ya no puede ocurrir así.**
- **08-26 17:41**, conv `5fcca6eb`: el disparador fue «Jajajaa sisas», texto legible. El guard no
  aplica ni podría. **Sigue vivo.**

Y una precisión sobre el síntoma: el bot **no** se re-presentó el 08-26. Dijo «¿Cómo vas, Juan? ¿En
qué te puedo ayudar hoy?» — usó el nombre, porque el seed desde el `profile` funciona. Lo que falta no
es la identidad: es la **memoria de la venta que acaba de cerrarse** (deuda #7, §2.1) y un mecanismo
para callarse mientras el operador atiende. El mérito de la corrección: silenciar al bot en la ventana
posterior a `sale_closed` es un cambio de backend acotado, y ataca el momento exacto en que el
operador está escribiendo (§4.1).

### 4.3 La historia que ve el LLM no dice que llegó un medio

`recent_messages` entrega `content: ""` para una imagen (`ingest.py:419-434`). Por eso el 08-26 el bot
pidió el comprobante 64 s después de recibirlo. **No hace falta bajar bytes para evitar ese daño**: un
placeholder por tipo en el historial («[el cliente envió una imagen]») más una línea de directive
resuelve el caso de contradicción, se hace 100 % en backend, y no toca el `master` ni la ventana de
301 s. Es la mitad útil de P16, y es separable de la mitad cara.

### 4.4 El outbound se persiste antes de enviarse

`agent_action.py:661-674` escribe el mensaje y devuelve `approved`; recién después n8n llama a Chakra
(`Send WhatsApp via Chakra`, **sin** `continueOnFail`) `[n8n]`. Si el envío falla, la DB afirma un
envío que no ocurrió, el historial se contamina y el circuit breaker cuenta fantasmas. No está en
ningún documento. Relacionado con la deuda #11 (314 de 314 outbound sin `chakra_message_id` `[DB]`)
pero es un problema distinto: no es idempotencia, es veracidad del registro.

### 4.5 Los turnos suprimidos no dejan rastro en `audit_log`

El guard y el debounce retornan en `ingest.py:289-320`, **antes** del `AuditLog` de `ingest.py:383`.
Hoy hay **36 inbound sin evento `message_ingest`** (18 `text`, 8 `image`, 5 `unsupported`, 2 `audio`,
2 `reaction`, 1 `revoke`) `[DB]`. La «observabilidad sin telemetría» que el brief del allowlist declaró
suficiente descansa solo en `messages.message_type`: no se puede distinguir un turno suprimido por el
guard de uno suprimido por debounce ni de uno perdido por un 500.

### 4.6 La instancia de n8n es compartida, y el filtro por error no sirve como alerta

`Predicción horaria → Telegram` (`cpQs5t6aQw5022Lk`) falla **siete veces al día desde al menos el
08-19** — 70 ejecuciones en error en la retención, y son **todas** las ejecuciones en error de la
instancia `[n8n]`. `Liquidación de señales` tiene tantas ejecuciones como el pipeline de ventas. Dos
consecuencias: cualquier alerta basada en `status=error` nace ahogada en ruido ajeno, y la réplica
única (`minReplicas = maxReplicas = 1`, correcta por el postmortem del 07-21) comparte CPU con
automatizaciones que no son del producto.

### 4.7 `max_natural` es un segundo tenant enrutado por `master`, sin P14 y con arquitectura opuesta

El `Switch` del `master` enruta `phone_number_id = 420972257763953` a `max_natural`, activo, 15 nodos
`[n8n]`. Su `Set` whitelist copia **cuatro** campos y ninguno de identidad de P14: un cliente suyo con
privacidad de número se descarta hoy en silencio, exactamente la exec 9459 que originó P14. Y el
workflow es un **AI Agent con tools** (`agent`, `toolCalculator`, dos `dataTableTool`), que es
justamente lo que ADR-002 descartó y lo que `n8n_workflow/CLAUDE.md` prohíbe explícitamente. Si
`max_natural` es un cliente real, es una superficie de producto sin ninguna de las defensas de este
sistema; si no lo es, su rama debería ir a `Stop`. No está mencionado en ningún documento del repo.

### 4.8 `message_count` cuenta el doble

15 de 53 conversaciones tienen `message_count` distinto del número real de filas `[DB]`. Cosmético,
tres documentos lo mencionan como hallazgo incidental desde julio, y sigue sin número.

---

## 5. Diagnóstico transversal de relojes

**Veredicto: la hipótesis se confirma a medias.** De los fallos difíciles del sistema, viven en el
desfase entre relojes el falso positivo de confirmación, la ventana del debounce y el riesgo de la
013. **No** viven ahí el drop LID, la llave inventada, los echoes, la compaction, el reinicio
post-venta ni el scale-to-zero. Es aproximadamente un tercio, concentrado en dos costuras.

### 5.1 Lo medido

| Magnitud | Valor | Fuente |
|---|---|---|
| Resolución del `timestamp` de Meta | **1 s** — 359 de 359 inbound en segundo entero | `[DB]` |
| Retraso de entrega, texto: mediana / p90 / máx | **3,5 s / 6,8 s / 9,1 s** (n=108, desde 08-01) | `[DB]`, `audit − messages − 5 s` |
| Retraso de entrega, imagen / audio (mediana) | **7,9 s / 8,2 s** | `[DB]`, n=6 y n=3 |
| Diferencia media media−texto | **≈ 4,4 s** | derivada |
| Cota del adelanto del reloj de Meta | **≤ 0,2 s** (mínimo observado 4,80 s sobre un sleep de 5 s) | `[DB]`, n=323 |
| Entregas tardías extremas | **20, 22 y 80 minutos** (conv `4b5ba0ec`, 06-12) | `[DB]` |
| Pares de inbound en el mismo segundo | **1**, con 3 respuestas (conv `d6349fa0`, 07-18) | `[DB]` |
| Vida útil de la URL firmada | **301–302 s** desde el `timestamp` | diagnóstico 08-22 §3.2 |

La cota de 0,2 s no es una medida limpia: el `sleep(5)` no es exacto y el timestamp está pisado al
segundo. Es cota superior del desfase, suficiente para lo que se decide con ella.

### 5.2 Instancias nuevas del patrón

1. **El debounce compara reloj de Meta contra espera de pared.** `ingest.py:300` duerme 5 s de pared;
   `ingest.py:307` busca `Message.created_at > msg_timestamp`, que es tiempo de Meta. La ventana
   efectiva es `5 + δ(A) − δ(B)` y, con la dispersión medida, va de ~2 s a ~8 s según qué llegue.
2. **El `>` sobre segundos enteros ignora las ráfagas dentro del mismo segundo.** Dos inbound con el
   mismo `timestamp` no se ven entre sí: el loop del 07-18 tuvo ese caso y produjo tres respuestas.
   Corrección de una línea: `created_at >= msg_timestamp AND id != message.id`.
3. **Los 795 ms del falso positivo del 08-01 son un artefacto del piso al segundo.** El inbound
   «enviame una foto del producto» tiene `timestamp 17:51:35` (segundo entero) y el resumen del LLM se
   persistió a las `17:51:35.795216` `[DB]`. El envío real pudo ocurrir en cualquier punto de ese
   segundo: entre 795 ms **antes** y 205 ms **después**. **El orden sub-segundo no es determinable.**
   ADR-010 lo resuelve bien igualmente: su condición 3 usa `<=` (`order_summary.py:315`), y el piso
   empuja hacia el rechazo, que es el lado seguro. Conviene que el ADR lo diga, porque su tabla lo
   presenta como un hecho medido.
4. **`recent_messages` mezcla dos relojes en el orden del historial**: los inbound llevan tiempo de
   Meta y los outbound `now()` de pared, y se ordenan juntos (`ingest.py:419-434`). Hoy produce el
   orden verdadero de los cruces, pero nadie lo decidió por escrito.
5. **`timestamp` ausente cae a `now()`** (`ingest.py:251`): el mismo campo lleva dos relojes según el
   camino. Ocurrió en 2 mensajes `[DB]`.
6. **Cuarto reloj, el humano**: 010 el 06-16, 011 el 07-31, 012 el 08-17, 013 pendiente. Todas
   manuales, con el CI desplegando solo. La 012 se diseñó para sobrevivir el intervalo y lo documentó;
   la 013 no (§2.2).

### 5.3 Veredicto

**Merece un ADR corto, no un frente propio.** Contenido: una tabla de siete filas que diga en qué
reloj vive cada decisión temporal (ventana de 24 h, debounce, `trigger_message_at`,
`order_summary_sent_at`, orden del historial, expiración de medios, orden migración↔despliegue), más
los dos invariantes medidos: el piso de 1 s y la cota de desfase. Lo único que se implementa desde ahí
es la corrección de §5.2.2 y la regla de orden de migraciones. Un frente P haría que se acumule
diseño sobre un problema que ya está mayormente mitigado.

---

## 6. Modos de falla silenciosos — inventario

El default del pipeline ante una forma que no reconoce es `Stop` marcado `success`. **209 de las 268
ejecuciones retenidas de `cafe_arenillo_v2` terminan en `Stop`; las 268 están marcadas `success`; cero
ejecuciones en error** `[n8n]`. Superficies, además del `If Message Exists` ya documentado:

| Superficie | Qué traga | Evidencia |
|---|---|---|
| `IF Should Respond` rama falsa | no distingue debounce legítimo de backend caído | 5×500 en `success` (execs 10452…10650) |
| `Process Backend Response` → `backend_error` | 409 y 5xx de `/agent/action` | rama viva, 0 ocurrencias aún |
| `Send WhatsApp via Chakra` (sin `continueOnFail`) | marca error, pero nadie mira; la DB ya registró el envío | §4.4 |
| `Notify Owner WhatsApp` | está **encadenado antes** de `IF Notify Operator`: si falla, el aviso Telegram del breaker no sale | `[n8n]` cadena `IF Escalated → Notify Owner → IF Notify Operator` |
| `Switch` del `master` | un `phone_number_id` desconocido no tiene rama | `[n8n]` |
| status `failed` de WhatsApp | muere en el mismo `Stop` que los `delivered` | 1 caso el 08-19, ya podado |
| compaction | traga y cuenta, pero nadie lee el contador | §2.1 — 80 días |
| carga de la clave de OpenAI | solo `logger.warning` si falla (`main.py:66,78`) | §2.1 |
| turnos suprimidos | sin `audit_log` | §4.5 |

---

## 7. Configuración legacy que sostiene el sistema sin que nadie la haya puesto ahí

Además de los dos `continueOnFail: true` ya documentados:

- **El whitelist `map_webhook_data_arenillo` es a la vez el único filtro de forma y el cuello por
  donde no pasa ningún campo nuevo** (medios, `caption`, echoes). Cada frente que necesite un campo
  nuevo pasa por ahí, y P14 demostró que es el punto de drop primario.
- **`pool_size=5, max_overflow=10`** (`core/database.py:77-78`) con un `sleep(5)` por ingest limita a
  ~15 mensajes concurrentes antes de que el ingest empiece a devolver 500 invisibles. Con 40 inbound
  por semana no importa; con el primer cliente con volumen, sí.
- **El fallback `'gpt-4.1-mini'`** del nodo `Build LLM Prompt` si el backend omitiera `ai_model`: un
  modelo que nunca se ha usado ni evaluado, a un `||` de distancia.
- **`uq_client_user_phone` referenciado por nombre** en el upsert (`ingest.py:619`), ya documentado
  por la 012 — se anota aquí porque pertenece a la misma clase.
- **`typeValidation: strict`** en `IF Should Respond` sobre `$json.should_respond`, que en el item de
  error no existe. Funciona hoy por cómo n8n evalúa un booleano ausente; es la misma clase de
  fragilidad que produjo el drop de P14 en `If Message Exists`.
- **Constantes duplicadas fuera de la DB**: `OPERATOR_CHAT_ID`, el teléfono del dueño, el `client_id`
  y las tres URL de Chakra, hardcodeados en nodos `[n8n]`.

---

## 8. Lógica duplicada sin nada que la mantenga sincronizada

Además de la identidad copiada a mano en cuatro nodos y del par `is_unreadable()` /
`_sql_has_readable_content()`:

| Duplicado | Dónde vive | Qué lo rompe |
|---|---|---|
| Parser de la forma de `shipping_rules` | `order_summary._shipping_cities` y `prompt_context.format_business_context` | ambos toleran dos formas «hasta que se aplique la 013»; nadie las retirará a la vez |
| Lista de campos del pedido | `_USER_CONFIRMATION_REQUIRES`, `SUMMARY_REQUIRED`, `required_fields` del DAG, `_ORDER_FIELDS` de `prompt_context` | **cuatro** sitios; P30 los toca todos |
| Claves válidas de `extracted_data` | `STRATEGY_FIELDS ∪ ORDER_FIELDS`, el nodo `Build LLM Prompt`, la sección EXTRACCIÓN del prompt en DB | la 011 tuvo que tocar dos de los tres |
| Regla «el bot se calla» | `IF Should Respond`, `approved` en `agent_action`, `Process Backend Response`, `state_machine.STATES` | la lista de estados está escrita a mano en cada nodo |
| Teléfono del dueño | nodo `Notify Owner WhatsApp` y `business_rules.notification_phone` | — |

---

## 9. Retención: qué decisiones ya no son re-verificables

**Ya no lo son** (solo sobreviven como cita documental): la exec 9459 que fundó P14; las ocho imágenes
con las que se midió la ventana de 301–302 s; el único status `failed`; y las ejecuciones del 08-15
que sostienen el episodio bot-a-bot de §2.10. Los diez echoes del 08-19 saldrán de la ventana en
días.

**Siguen siéndolo, y se re-verificaron en esta sesión** `[DB]`: los 795 ms del 08-01, las 0 de 39
compactaciones, los 314 de 314 outbound sin wamid, las 47 conversaciones `active` con ventana muerta,
las 4 ventas con pago, y el `profile_corrected` del 07-31.

**Regla que este hallazgo sugiere**, sin cambiar el método: *un diagnóstico que termine en «leer X» no
se cierra hasta leer X en la misma sesión.* La causa raíz de P4 estuvo disponible desde el 06-14 —
cuando P4 la hizo observable — y se leyó hoy, 80 días después, tras cinco documentos que la citan como
pendiente.

---

## 10. Orden de cierre — evaluación de los ocho primeros y propuesta

### 10.1 Contra el criterio declarado («que clientes reales lo usen con confianza»)

| # actual | Frente | Veredicto |
|---|---|---|
| 1-2 | P14, deuda #13 | ✅ cerrados, correctos |
| 3 | **P15** | **Justificado**, pero su entrada no dice que ya es un cambio grande, que altera precios de envío y que introduce un modo de estancamiento nuevo (sin molienda no hay resumen) |
| 4 | Decidir P28 y P29 | **Inercia a medias**: P28 ya está decidido por ADR-010 (es su tesis); P29 tiene dos mitades de costo muy distinto y el orden las trata como una sesión de razonamiento |
| 5 | **P16** | **Lo interesante antes que lo que desbloquea**: bajar bytes toca el `master`, la ventana de 5 min y el manejo de errores, para ~5 % del inbound y un comprobante que el operador ya ve |
| 6 | P17, P18, P19, P5+P9 | **Mal clasificado**: P5 no es higiene, es la única forma de enterarse de que el sistema falló. P18 y P19 ya están cerrados (§2.6) |
| 6 bis | «captura mínima de opt-in» | **No es barata ni segura**: sin mecanismo determinista es el bug de P15 otra vez, sobre un dato con consecuencia regulatoria |
| 7 | **P22** | **North-star, no deuda #1**: los 254 tests existentes ya son puros; los fallos que dolieron vivieron en las costuras con Postgres y n8n, que un motor con stubs no ejercita |
| 8 | **P24** | **Se argumenta con evidencia prestada**: el re-saludo y los perfiles vacíos son de la clave inválida (§2.1) |

### 10.2 Orden propuesto

1. **Rotar `openai-key`** y verificar con el próximo cliente recurrente. Cierra la causa raíz de P4 y
   la deuda #7. Cero código, y es la promesa central del producto.
2. **ADR-010 en tres pasos**: 013 sección 1 → merge y despliegue → 013 secciones 2 y 3 → verificación.
   Antes, confirmar el precio de Manizales. Al mergear: ADR a `Accepted`, índice de ADRs, y resolver
   la referencia colgada de P10.
3. **Silencio post-venta** (frente nuevo, backend puro): tras un `sale_closed` reciente, persistir y
   suprimir. Es el momento exacto en que el operador está escribiendo (§4.1, §4.2).
4. **Medios visibles en el historial** (frente nuevo, backend puro): placeholder por tipo en
   `recent_messages` + línea de directive. Cubre «pidió el comprobante que ya tenía» y permite decir
   «no puedo escuchar audios» en vez de callar (§4.3).
5. **P5 re-alcanzado a alerta**, sin retry: un IF sobre `backend_error` y sobre el item de error del
   ingest, un Telegram, `suppressed_reason` en el aviso. Misma sesión: **P9** y **re-export de los
   tres workflows** antes y después.
6. **P29 acotado a echoes**: el `master` deja pasar `message_echoes[]`, el backend los persiste como
   outbound del operador, y una regla determinista pausa al bot N minutos tras un echo.
7. **P17**, cierre formal de **P18** y **P19**, decisión de **P20**. Sesión corta.
8. **Medios de pago gobernados por el backend**, como sección nueva de ADR-010 en vez de P28.

Después, sin fecha: **P21** con la compaction viva y el prompt ya recortado; decisión de **P24** con
datos reales de `pending_intent`; **P22** re-alcanzado a tests de integración contra Postgres en CI;
**P16 bytes** solo si el negocio exige el comprobante dentro del sistema; **P26** y **P27** manuales.

---

## 11. Lo que NO se debe hacer

| Frente | Recomendación | Razón |
|---|---|---|
| **P18, P19** | **Cerrar sin implementar** | resueltos el 07-31 (§2.6) |
| **P28** | **Fusionar** en ADR-010 como sección nueva | es la misma decisión sobre otro dato |
| **P30** | **Fusionar** en ADR-010 | el caso del 08-26 fue de extracción (familia P12); el resumen con confirmación explícita es la validación que el negocio necesita hoy |
| **P23** | **Decidir «no por ahora»** y cerrar como decisión | cero 409 en 268 ejecuciones; desbloquea el ADR de P7 sin rediseñar nada |
| **P7** | **Posponer**; extraer solo el microfix del mismo segundo (§5.2.2) | sin carreras desde el fix (§2.9) |
| **P6** | **Posponer** hasta que exista outbound business-initiated | lo urgente de su alcance (wamid de salida) entra con P5 |
| **P22** | **Re-alcanzar** a tests de integración contra Postgres | el motor con stubs es north-star |
| **P25** | **Posponer** | 5 audios en la historia; el placeholder de §4.3 da la salida amable |
| **P26** «captura mínima» | **No intercalar** | capturar consentimiento por botón o plantilla, nunca por extracción |
| **P16 bytes** | **Posponer**; su mitad útil pasa al frente nuevo de §4.3 | ~5 % del inbound, un solo producto (§2.8) |
| **P10** | **Aparcar** con nota de que ocurrió dos veces y el breaker bastó | §2.10 |
| **P27** | Solo versión manual, como ya dice | sin cambio |

---

## 12. Frentes nuevos propuestos

El siguiente número libre es **P31** (`ROADMAP.md:49`; `:707` lo contradice — ver §1.2).

- **P31 · Silencio post-venta** — 🔴 [B]. El bot contesta a un cliente que acaba de comprar mientras el
  operador atiende. Evidencia: conv `5fcca6eb` (08-26 17:41:22), con 4 echoes del operador entre
  17:38 y 17:40. La ocurrencia gemela del 08-19 ya la cubre el guard (§4.2). Toca la consecuencia
  aceptada de ADR-009 §4 → nota as-built.
- **P32 · Placeholder de medios en el historial** — 🔴 [B]. Mitad útil de P16, separable de la
  descarga. Evidencia: conv `ba58a211`, imagen 17:37:27 → el bot pide el comprobante 17:38:31 (§4.3).
- **P33 · Orden migración↔despliegue** — 🔴 [ADR]. Regla escrita más partir la 013 en dos archivos.
  Evidencia: `core.py:193-194` contra `information_schema`, y el trigger del CI (§2.2).

**Sin número nuevo, a propósito**: la rotación de la clave es el fix de **P4**; la alerta es **P5**
re-alcanzado; la ingesta de echoes es **P29** acotado.

**Deudas nuevas para `CLAUDE.md`** (la última asignada es #14): export de n8n editado fuera de banda
(§1.3); instancia n8n compartida con workflows ajenos que fallan cada hora (§4.6); `max_natural`
enrutado sin P14 y con AI Agent (§4.7); outbound persistido antes del envío (§4.4); turnos suprimidos
sin `audit_log` (§4.5). La deuda **#7 no necesita número nuevo**: necesita su causa raíz anotada.

---

## 13. ADRs que faltan, y contradicciones explícitas

1. **ADR de relojes** (§5.3). No contradice nada; formaliza lo que ADR-010 ya hace en su condición 3.
2. **ADR de orden migración↔despliegue** (§2.2). Contradice el **brief** de ADR-010, no el ADR.
3. **Nota as-built en ADR-009 §4**, o ADR nuevo si se prefiere un estado explícito.
   **Contradice una decisión ya tomada**: la consecuencia «`closed` cierra la VENTA, no la RELACIÓN;
   el siguiente mensaje abre una conversación nueva en `active`» asume que ese mensaje es una próxima
   venta. La evidencia de dos cierres reales dice que es la conversación en curso, con el operador
   escribiendo. La decisión de cerrar sigue siendo correcta; el supuesto sobre el mensaje siguiente
   no lo es.
4. **Nota as-built en ADR-002**: su fail-safe escrito el 2026-04-03 («si el backend rechaza la
   propuesta del LLM, el texto de respuesta igual va al usuario») tiene hoy **dos excepciones, y
   ninguna está registrada ahí**. La primera es **P8**: el circuit breaker devuelve `approved=False`
   y `final_response_text=""`, es decir **suprime** el texto. La segunda la añade **ADR-010 §1**, que
   lo **reemplaza** por el resumen del backend. Ambas son coherentes con la tesis del sistema y
   ninguna rompió la conversación, pero la consecuencia escrita ya no describe el comportamiento.
5. **Nota as-built en ADR-010 §3** si el precio de Manizales es un error (§2.3).
6. Al mergear ADR-010: pasarlo a **`Accepted`**, agregarlo al índice de `decisions/README.md`, y
   corregir ahí la reserva del 010 para P10.

**No hace falta ADR** para P28, P30, P22, ni para P31 si se implementa como ventana temporal.

---

## 14. Lo que no fue determinable

- Quién editó `cafe_arenillo_v2` el 2026-08-29 a las 23:38 UTC y con qué intención: el historial
  nativo de n8n exige un token MCP de instancia que no está configurado.
- La fecha de última rotación del secreto `openai-key`: la lectura de sus atributos fue denegada en
  esta sesión.
- Si Chakra reintenta entregas fallidas (pendiente desde el postmortem del 07-21).
- Si `max_natural` atiende clientes reales.
- Si `15d89710` es la identidad del dueño (§2.12).
- El mecanismo de poda de ejecuciones de n8n (§2.4).
- El orden sub-segundo real del cruce del 08-01 (§5.2.3): estructuralmente indeterminable.

---

## Anexo — inventario de evidencia

| Fuente | Qué aportó |
|---|---|
| `[DB]` sesión read-only, 2026-09-02 00:11:30 UTC | schema post-012 sin las columnas de la 013; 39/53/673/647; 0/39 compactaciones; 314/314 sin wamid; 58/359 inbound vacíos; `profile_corrected` del 07-31; 4 ventas en 3 usuarios; relojes de §5.1 |
| `[n8n]` 268 execs de `master` + 268 de `cafe_arenillo_v2` | 17 echoes; 209 `Stop`; 0 en error; 5×500; 0 409; formas de payload; retención real |
| `[az]` `containerapp logs show -n ca-backend` | el 401 de OpenAI del 2026-08-30T21:53:34.869Z |
| `[az]` `containerapp show -n ca-backend` | revisión 0000057, imagen `caabe89…`, `minReplicas: 1` |
| Repo, rama `feat/adr-010-…` | ADR-010, migración 013, `order_summary.py`, modelo ORM |
| Repo, `origin/main` | ROADMAP vigente, diagnóstico del 08-29, P30 |
| Ejecuciones citadas | 10452, 10474, 10487, 10566, 10600, 10612, 10650, 11150, 11153, 11165, 11170, 11174, 11435, 11444, 11506, 11511, 11518 |
