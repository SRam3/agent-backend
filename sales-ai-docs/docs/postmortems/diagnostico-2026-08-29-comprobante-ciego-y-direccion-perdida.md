# Diagnóstico — Miércoles 2026-08-26: el comprobante ciego y la dirección perdida

> **Saneado el 2026-09-04**: se retiraron identificadores de infraestructura y de plataforma,
> y los datos personales de clientes, según la «Convención de anonimización» de
> `docs/README.md`. Las conversaciones y los `client_user` se citan con etiquetas estables
> (`conv-MM-DD`, `cliente-MM-DD`). **El análisis y sus conclusiones no cambiaron.**

**Fecha del diagnóstico**: 2026-08-29 · **Ventana analizada**: 2026-08-26 17:28–17:42 UTC y 21:53–21:57 UTC
**Disparador**: el dueño reporta que «el bot no se silenció» al recibir un comprobante de pago, que volvió a
entrar tras cerrarse la venta, y que una segunda clienta envió una dirección incorrecta.

**Modo de ejecución**: READ-ONLY estricto. Sesión Postgres con `default_transaction_read_only = on`
(verificado con `SHOW` como primer comando); contra n8n solo `GET /api/v1/executions`. Cero escrituras,
cero cambios en workflows, cero despliegues.

**Convención de evidencia**: `exec NNNN` = ejecución viva de n8n leída completa; `[DB]` = confirmado con
SELECT contra la Postgres viva. Timestamps en UTC (Bogotá = UTC−5). PII enmascarada salvo los nombres que
el propio dueño aportó al pedir el análisis.

> **Contexto temporal que ordena todo**: el guard de contenido ilegible se desplegó el **2026-08-23 a las
> 19:28 UTC** (PR #63). El miércoles analizado es el **2026-08-26**, seis días DESPUÉS. El guard estaba
> vivo durante ambas conversaciones. Cualquier lectura que asuma lo contrario es incorrecta.

---

## 0. Resumen — tres fallos independientes que se sintieron como uno

| Lo que se percibió | Qué pasó realmente | Frente |
|---|---|---|
| «el bot no se silenció con la imagen» | **Sí se silenció.** Respondió al «Excelente!!!» que el cliente escribió 55 s después | — (funcionó como se diseñó) |
| — (no se percibió) | **Pidió el comprobante que ya estaba enviado** | **P16**, mitad abierta |
| «volvió a entrar cuando yo ya atendía» | Cerrar la venta **reinicia** al bot, no lo silencia | **P29** / deuda #14 |
| «la dirección estaba incorrecta» | El LLM **nunca la extrajo**; se perdió antes de cualquier gate | familia **P12** |
| «¿no tenemos validación de direcciones?» | No. Solo existe la de teléfono | **P30** (registrado aquí) |

**El guard hizo exactamente lo que prometía y nada más.** Los tres fallos reales son huecos que ya
estaban abiertos, no regresiones suyas.

---

## 1. La venta de las 12:38 pm — conversación `conv-08-26-a`

Conversación completa 17:28:25 → 17:38:38 UTC (12:28–12:38 Bogotá).
Cerrada por el operador, `payment_confirmed`, 80 %. `[DB]`

### 1.1 El guard SÍ silenció el comprobante

| exec | hora UTC | `message_type` | `should_respond` | `reason` | último nodo | ¿outbound? |
|---|---|---|---|---|---|---|
| 11144 | 17:34:31 | `reaction` | `false` | `unreadable_content` | `Stop` | no |
| 11146 | 17:34:33 | `reaction` | `false` | `unreadable_content` | `Stop` | no |
| **11150** | **17:37:35** | **`image`** (comprobante) | **`false`** | **`unreadable_content`** | **`Stop`** | **no** |
| 11158 | 17:38:25 | `text` | `true` | — | `IF Notify Operator` | sí |

El comprobante murió en `Stop` en **177 ms**, sin llamada al LLM y sin salida. `[exec 11150]`

Dos observaciones que valen más que el caso puntual:

- Los dos eventos **`reaction`** también se silenciaron. Es un tipo que nadie habría puesto en una lista
  de tipos permitidos — la confirmación práctica de que preguntar por el CONTENIDO y no por el tipo era
  la decisión correcta.
- La respuesta que el dueño interpretó como «el bot contestó a la imagen» salió de `exec 11158`,
  disparada por el texto **«Excelente!!!»** que el propio cliente escribió a las 17:38:22 `[DB]`. Texto
  legible, turno legítimo.

### 1.2 El daño real: pidió el comprobante que ya tenía

Secuencia `[DB]`:

```
17:37:27  inbound  image  ""              ← el comprobante de pago
17:38:22  inbound  text   "Excelente!!!"
17:38:31  outbound text   "Me alegra que estés contento. Cuando realices el pago,
                           no olvides enviarme el comprobante. Quedo atento."
```

**Sesenta y cuatro segundos después de recibir el comprobante, el bot lo pidió.**

El guard evitó que respondiera *a* la imagen; no puede hacer que la *vea*. Para el sistema ese pago no
existe: `content = ''`, sin bytes, sin OCR, sin nada. El bot no está alucinando — está operando sobre un
diálogo al que le falta el artefacto más importante de la venta.

Esto es **P16 en su mitad abierta** (descarga de medios), y añade un caso que su entrada no contemplaba:
el frente no es solo «el bot responde a ciegas», es «el bot **contradice** lo que el cliente acaba de
hacer». Silenciar el turno no lo evita.

### 1.3 Cerrar la venta reinicia al bot, no lo silencia

```
17:38:38.287  sale_closed        operator   conv-08-26-a → closed     [DB, audit_log]
17:41:12      inbound  text      "Jajajaa sisas"
17:41:15.685  conversación NUEVA conv-08-26-b creada, v1, active      [DB]
17:41:22.085  outbound text      "¿Cómo vas, Juan? ¿En qué te puedo ayudar hoy?"
```

Con `conv-08-26-a` en `closed`, el ingest no la puede reutilizar: crea otra desde cero, sin historial. El bot
se presenta de nuevo a un cliente que acaba de comprar, mientras el operador atiende a mano.

**Es el mismo patrón exacto de la venta del 2026-08-19** (ver
`analisis-2026-08-19-venta-bsuid-colision-operador.md` y
`diagnostico-2026-08-22-post-corte-echoes-y-silencio.md` §2.3). Segunda ocurrencia real, en clientes
distintos, con once días de diferencia. Ya no es anécdota: es el comportamiento.

**El guard no aplica ni podría**: «Jajajaa sisas» es texto perfectamente legible. No existe hoy ningún
mecanismo para callar al bot — es exactamente **P29** / deuda #14.

---

## 2. La dirección de las 4:55 pm — conversación `conv-08-26-c`

Clienta: la clienta (`client_user` `cliente-08-26-c`, bsuid `CO.…`, primer contacto 2026-08-26
21:53:36 UTC). Conversación `active`, v8, checkpoint `product_matched`, 0 %. `[DB]`

### 2.1 La dirección no se guardó mal: no se guardó

```
21:55:45  inbound   "te paso la dirección"          extracted_data = NULL
21:55:51  inbound   "<dirección>"                 extracted_data = NULL
21:55:54  outbound  "Claro, pero primero necesito saber en qué ciudad estás…"
                                                    extracted_data = {"grind_preference": "grano"}
21:56:00  outbound  "¿Me compartes el nombre de la ciudad?"
                                                    extracted_data = {"grind_preference": "grano"}
21:56:04  inbound   "Bello"
21:56:17  outbound  "Gracias, apunto que el envío es a Bello…"
                                                    extracted_data = {"shipping_city": "Bello",
                                                                      "grind_preference": "grano"}
```

`extracted_context` de la conversación, hoy `[DB]`:

```json
{ "shipping_city": "Bello", "grind_preference": "grano" }
```

**`shipping_address` no existe.** El LLM nunca extrajo `<dirección>`: no llegó a ningún gate, no fue
rechazada por ninguna validación — se perdió antes. La clienta dio el dato y el sistema lo tiró.

Cuando el flujo llegue al paso de la dirección se la volverá a pedir, algo que el prompt vivo prohíbe
explícitamente: *«Si el cliente mencionó un dato en esta misma conversación (nombre, teléfono, ciudad,
dirección, cantidad, preferencia de molido), úsalo exactamente. Nunca lo cambies, nunca lo inventes,
nunca se lo vuelvas a pedir.»* `[DB: system_prompt_template]`

Es la **familia de P12**: dato ofrecido fuera del orden esperado, descartado. P12 arregló la captura
oportunista de los `ORDER_FIELDS` (`quantity`, `grind_preference`) en fase pre-producto; aquí el
descartado es un campo del **DAG** (`shipping_address`) ofrecido antes de su turno. El mecanismo es el
mismo, la superficie no está cubierta.

### 2.2 No existe validación de direcciones

`services/validation.py` son **32 líneas con una sola función**: `is_plausible_phone` (ADR-008). En
`agent_action.py`, `shipping_address` aparece únicamente en `STRATEGY_FIELDS` y en
`_USER_CONFIRMATION_REQUIRES` — es decir, el gate exige que el campo **esté presente**, nunca que sea
válido.

Consecuencia: si el LLM hubiera extraído `<dirección>`, se habría persistido tal cual y habría contado
para `user_confirmation`. La venta habría quedado lista para cerrar con una dirección a la que nadie
puede despachar.

Registrado como **P30**. No abierto.

### 2.3 Dos mensajes seguidos pidiendo lo mismo

21:55:54 y 21:56:00 — dos outbounds con 5,5 s de diferencia, ambos pidiendo la ciudad. Los disparan sus
dos inbounds de 21:55:45 y 21:55:51, separados por **6 segundos**: justo fuera de la ventana de 5 s del
debounce. No son idénticos, así que el circuit breaker tampoco los ve (exige 3 idénticos consecutivos —
ver la nota de calibración en P8).

Tercera manifestación del mismo patrón en once días. Es insumo de **P7** (rediseño del debounce), que
sigue esperando su ADR.

---

## 3. Lo que este diagnóstico NO encontró

- **Ninguna regresión del guard de contenido ilegible.** Se comportó según diseño en los cuatro mensajes
  ilegibles de la ventana (2 `reaction`, 1 `image`, y los audios del cliente anterior).
- **Ningún 500.** El contrato de respuesta (deuda #13) devolvió `should_respond: false` + `reason` válidos
  en las tres supresiones.
- **Ningún echo del operador descartado** en esta ventana — pero tampoco ninguno recibido: los mensajes
  que el operador escribió desde la app siguen sin pasar por el webhook (deuda #14).

---

## 4. Qué queda anotado en el ROADMAP

| Frente | Qué se le añadió |
|---|---|
| **P16** | Que cerrarlo debe cubrir también el caso «pedir lo que ya se envió», no solo «responder a ciegas» |
| **P29** | Segunda ocurrencia real; cerrar la venta reinicia en vez de silenciar |
| **P12** | La familia se extiende a campos del DAG ofrecidos fuera de orden, no solo `ORDER_FIELDS` |
| **P30** | Registrado: sin validación de direcciones (única validación viva = teléfono) |
| **P7** | Tercer caso de near-duplicado por inbounds justo fuera de la ventana de 5 s |

Ninguno se abre con este diagnóstico. Es evidencia puesta donde se va a leer.
