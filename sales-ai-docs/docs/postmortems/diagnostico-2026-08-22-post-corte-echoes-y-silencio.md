# Diagnóstico — Post-corte del 2026-08-19: los echoes sí llegan, y el silencio tiene forma

> **Saneado el 2026-09-04**: se retiraron identificadores de infraestructura y de plataforma,
> y los datos personales de clientes, según la «Convención de anonimización» de
> `docs/README.md`. Las conversaciones y los `client_user` se citan con etiquetas estables
> (`conv-MM-DD`, `cliente-MM-DD`). **El análisis y sus conclusiones no cambiaron.**

**Fecha del diagnóstico**: 2026-08-22 (14:53–15:05 UTC) · **Ventana analizada**: 2026-08-19 19:45 UTC → 2026-08-22 15:00 UTC
**Objeto**: conversación `conv-08-19-a` (clienta BSUID `CO.…`, `client_user` `cliente-08-19-a`)
**Continúa**: `analisis-2026-08-19-venta-bsuid-colision-operador.md`, que cerró su ventana a las 19:45 UTC.

**Modo de ejecución**: READ-ONLY estricto. Sesión Postgres con `default_transaction_read_only = on` (verificado
con `SHOW` como primer comando); contra n8n solo `GET /api/v1/workflows[/{id}]` y `GET /api/v1/executions`; contra
Azure solo `az keyvault secret show` y `az containerapp show / revision list`. El único toque a la ruta de producción
fue un `GET` (nunca `POST`) al path del webhook para comprobar registro. Cero escrituras, cero cambios en workflows,
cero despliegues.

**Convención de evidencia**: `exec NNNN` = ejecución viva de n8n leída completa; `[DB]` = confirmado con SELECT contra
la Postgres viva. Timestamps en UTC. PII enmascarada: nombres a iniciales, teléfonos a últimos 4, BSUID a prefijo +
últimos 4, contenidos largos sustituidos por su longitud. **Los nombres de campo y el anidamiento se conservan
literales.**

---

## 0. Estado del pipeline — apagado intencional, todavía vigente

> **Corrección respecto a la primera entrega de este diagnóstico.** La primera versión reportó la desactivación del
> workflow `master` como una caída de causa desconocida. **No lo es: el dueño apagó el workflow a propósito**, esa misma
> noche. Lo que sigue conserva la medición (es correcta y sirve para fechar la decisión) pero corrige la lectura.

El workflow `master` (`<workflow_id>`) está en `active: false` **por decisión del operador**, tomada la noche
del 2026-08-19. Sigue así al momento de este diagnóstico.

Lo que la telemetría añade a ese hecho conocido:

- `master` es el **único** punto de entrada del pipeline: 247 de 247 de sus ejecuciones son `mode=webhook`.
  `cafe_arenillo_v2` (`<workflow_id>`) figura como `active: true`, pero es un sub-workflow
  (`executeWorkflowTrigger`, 247/247 `mode=integrated`): **su interruptor es cosmético**. Apagar `master` apaga todo.
- El apagado es efectivo a nivel de ruta: `GET` al path de producción devuelve
  `404 "The requested webhook … is not registered."`
- **No hay buffer.** Chakra recibe el 404 y no reintenta. Todo lo que la clienta o cualquier otro haya escrito desde esa
  noche está perdido, sin registro en ninguna parte.
- El resto de la infraestructura está sana, y conviene dejarlo dicho para descartar el fallo del 2026-07-21: la Container
  App `<N8N_CONTAINER_APP>` corre en la revisión `--0000010` (creada 2026-08-18T12:19:43Z) con `minReplicas = maxReplicas = 1`, y
  los dos crons horarios siguieron ejecutándose sin fallar hasta `exec 10820` del 2026-08-22T14:00:54Z. **No es
  scale-to-zero.** Es el interruptor.

### La telemetría fecha la decisión con precisión de ~82 segundos

| Instante | Evidencia |
|---|---|
| 2026-08-19T22:15:19.789Z | Última ejecución de WhatsApp de toda la historia (`exec 10691`) |
| 2026-08-19T22:15:19.833Z | Fin de esa ejecución |
| 2026-08-19 22:15:15.143 | Último mensaje en toda la DB `[DB]` |
| ≈2026-08-19T22:16:41Z | Echo del operador que **no dejó ninguna ejecución** → el webhook ya no existía |

El apagado ocurrió entre las **22:15:19.833Z y las 22:16:41Z**. Es decir: **entre 4 y 86 segundos después de que el bot
irrumpiera con un saludo genérico** en mitad del cierre manual de la venta (§2). La secuencia sugiere causa directa —
el bot metió la pata, y el interruptor se bajó acto seguido.

Dos notas de método sobre esa datación:

- `updatedAt` del workflow se quedó en `2026-08-18T12:35:53.397Z`, así que el toggle de activación **no lo modifica**.
  El instante exacto no es recuperable por la API de n8n; la ventana de 82 s se deriva de la ausencia de ejecuciones,
  no de un registro del apagado.
- Los IDs de ejecución son contiguos de 10670 a 10820: **nadie purgó nada**. La ausencia es real, no un artefacto de
  retención.

Duración del apagado al momento de este diagnóstico: **2 días, 16 horas, 40 minutos**.

---

## 1. La hipótesis del echo

> **Veredicto: SÍ. Los echoes llegan al webhook de n8n, se procesan, y se descartan en silencio con HTTP 200.**

No es que nunca lleguen. Existen, están completos, traen la identidad correcta, y mueren en el nodo
`If Message Exists` del sub-workflow. Diez de ellos en la retención, todos del 2026-08-19.

### 1.1 El echo de esa noche: `exec 10678`

`exec 10678` · `master` · inicio **2026-08-19T22:11:34.461Z** · duración **132 ms** · status `success`.
Su par en el sub-workflow, `exec 10679`, recorrió exactamente tres nodos:
`When Executed by Another Workflow → If Message Exists → Stop`.

Payload crudo (`body`), estructura intacta:

```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "<waba_id>",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "<número del negocio>",
              "phone_number_id": "<phone_number_id>"
            },
            "contacts": [
              {
                "profile": { "username": "<username>" },
                "user_id": "CO.…"
              }
            ],
            "message_echoes": [
              {
                "from": "<número del negocio>",
                "id": "wamid.…",
                "to_user_id": "CO.…",
                "timestamp": "1787177492",
                "text": { "body": "«saludo del operador», 21 chars" },
                "type": "text"
              }
            ]
          },
          "field": "smb_message_echoes"
        }
      ]
    }
  ]
}
```

Tres cosas que leer en ese payload:

- El array portador es **`message_echoes[]`**, no `messages[]`.
- La identidad del interlocutor viene en **`to_user_id`** (destinatario), no en `from_user_id`. El campo `from` es el
  número del negocio, no el de la clienta.
- **`contacts[0].user_id` está presente e intacto**, idéntico al de un inbound normal.

### 1.2 Por qué muere: dos nodos que solo saben leer `messages[]`

El `Set` whitelist `map_webhook_data_arenillo` del `master` copia 10 campos, y 6 de ellos cuelgan de
`value.messages[0]`. En un echo ese array no existe, así que la copia produce `null`. Salida real en `exec 10678`:

```json
{"body":{"entry":[{"changes":[{"value":{
  "messages":[{ "from":null, "timestamp":null, "text":{"body":null},
                "id":null, "type":null, "from_user_id":null }],
  "contacts":[{ "profile":{"name":null,"username":"<username>"},
                "user_id":"CO.…", "wa_id":null }]
}}]}]}}
```

El echo entra como un mensaje vacío con la identidad correcta pegada al lado. Después, `If Message Exists` aplica su
condición viva:

```js
var m = ((c.value || {}).messages || [])[0];
return !!(m && m.id && (m.from_user_id || m.from));
```

`m.id` es `null` ⇒ `false` ⇒ rama falsa ⇒ `Stop` (un `NoOp`). Sin log, sin error, sin respuesta de error: la ejecución
se marca `success`.

Nótese que el gate pregunta por `from_user_id` — el campo que P14 agregó — pero **un echo nunca lo trae**: su
equivalente se llama `to_user_id` y vive en `message_echoes[]`.

### 1.3 Los diez echoes

| exec | inicio (UTC) | dur. | desenlace | ventana |
|---|---|---:|---|---|
| 10585 | 2026-08-19T19:42:03.314Z | 136 ms | Stop | colisión operador #1 |
| 10603 | 2026-08-19T19:42:35.070Z | 135 ms | Stop | colisión operador #1 |
| 10619 | 2026-08-19T19:43:24.121Z | 182 ms | Stop | colisión operador #1 |
| 10639 | 2026-08-19T19:44:17.398Z | 701 ms | Stop | colisión operador #1 |
| 10645 | 2026-08-19T19:44:28.366Z | 139 ms | Stop | colisión operador #1 |
| 10657 | 2026-08-19T19:45:27.467Z | 156 ms | Stop | colisión operador #1 |
| 10661 | 2026-08-19T19:45:31.806Z | 179 ms | Stop | colisión operador #1 |
| 10665 | 2026-08-19T19:45:35.509Z | 201 ms | Stop | colisión operador #1 |
| 10678 | 2026-08-19T22:11:34.461Z | 132 ms | Stop | cierre logístico |
| 10682 | 2026-08-19T22:14:33.707Z | 189 ms | Stop | cierre logístico |

El contenido del segundo echo tardío (`exec 10682`, ts `1787177671` = 22:14:31Z) es operativo, no PII:
«Te comento que mañana nos entregan el café recién tostado de la tostadora. ¿Podríamos realizarte la entrega pasado
mañana?». **El sistema nunca supo que esa promesa de entrega existió.**

**Cota inferior, no total.** La retención de n8n solo conserva ejecuciones de `master`/`cafe_arenillo_v2` de los días
2026-08-15, 16, 18 y 19. «Diez echoes» es lo que sobrevive, no lo que ha ocurrido en la vida del sistema. Y no existe
ninguna ejecución a las 22:16:41Z: ese echo cayó ya con el webhook apagado.

---

## 2. Estado real de la conversación tras el corte de las 19:45

Dos correcciones al postmortem del 2026-08-19.

> **Corrección 1 — La venta SÍ se registró.** El dueño pulsó el botón de Telegram a las 21:40:54 UTC y el cierre
> funcionó completo. La expectativa registrada («ninguno, la venta no se registró») era incorrecta.

> **Corrección 2 — El bug del mensaje vacío se repitió a las 22:15:15 UTC**, en una conversación nueva, 35 minutos
> después de cerrar la venta. Confirmado con evidencia, no inferido.

### 2.1 La conversación `conv-08-19-a` cambió después del corte `[DB]`

| campo | postmortem (19:45) | hoy |
|---|---|---|
| `state` | `active` | **`closed`** |
| `strategy_version` | 23 | 23 — sin cambio |
| `current_checkpoint` | `product_matched` | `product_matched` — sin cambio |
| `message_count` | 54 | 54 — sin cambio |
| `last_message_at` | 2026-08-19 19:44:33.212 | idem |
| `updated_at` | ~19:45 | **2026-08-19 21:40:55.097** |

`extracted_context` completo, hoy:

```json
{
  "phone":               "<teléfono>",
  "quantity":            2,
  "full_name":           la clienta,
  "shipping_city":       "Manizales",
  "grind_preference":    "grano",
  "shipping_address":    "«dirección», 17 chars",
  "user_confirmation":   true,
  "payment_confirmation": true
}
```

- `user_confirmation: true` — el falso positivo de P15 sigue persistido.
- `payment_confirmation: true` — **nuevo**; lo escribió el operador a las 21:40:55.

### 2.2 `audit_log` desde las 19:45 — hay un `sale_closed` `[DB]`

| timestamp UTC | event_type | actor | efecto |
|---|---|---|---|
| 21:40:55.097 | **`sale_closed`** | `operator` | `payment_confirmed_by_operator`, `state_changed:active→closed`, `sale_recorded_in_profile` |
| 22:15:13.204 | `message_ingest` | `system` | ingreso del audio — **conversación nueva** `conv-08-19-c` |
| 22:15:15.143 | `agent_turn` | `agent` | el bot respondió |

El botón se pulsó **dos veces** y la idempotencia aguantó:

- `exec 10675` (21:40:54.921Z) → `{confirmed: true, already_confirmed: false, new_state: "closed"}`
- `exec 10692` (22:15:30.326Z) → `{confirmed: true, already_confirmed: true, side_effects: []}`
  · «Esa venta ya estaba confirmada y cerrada. No se duplicó nada.»

El workflow `operator_confirm_telegram` tiene exactamente **2 ejecuciones**. El registro previo que decía «0
ejecuciones» está desactualizado.

### 2.3 Mensajes desde las 19:45: cero en `conv-08-19-a`, dos en una conversación nueva

`SELECT … FROM messages WHERE conversation_id = conv-08-19-a AND created_at >= '19:45'` devuelve **0 filas** `[DB]`.

Pero cerrar la venta cerró la conversación, y el siguiente inbound abrió otra desde cero — `conv-08-19-c`,
creada 22:15:08.068 UTC, `state=active`, `strategy_version=1`:

| UTC | dir. | tipo | len | content | wamid |
|---|---|---|---:|---|---|
| 22:15:00 | inbound | **`audio`** | **0** | **vacío** | `wamid.……` → decodifica a `CO.…` |
| 22:15:15.143 | outbound | `text` | 65 | «Hola, ¿cómo estás? Aquí estoy para ayudarte con lo que necesites.» | `<none>` |

El turno costó **5 739 prompt tokens** en `gpt-4o-mini` para producir un saludo genérico. El wamid del audio confirma
que era la clienta, no el operador.

> **Cerrar la venta empeoró el bug del mensaje vacío en vez de contenerlo.**

Con la conversación en `closed`, el ingest no reanuda: crea una conversación nueva en `v1`, sin historial, sin contexto,
sin los seis slots ya extraídos. Un audio que el pipeline no transcribe entra como `content=''`, y el LLM — sin nada que
leer y sin nada que recordar — hace lo único que puede hacer: presentarse. A una clienta que acababa de comprar,
mientras el dueño cerraba la logística a mano.

Es el mismo fallo del `edit` de las 19:40 (`exec 10513`), con la agravante de que ahora el sistema **ya no recordaba
quién era ella**.

### 2.4 La secuencia completa de esa noche

| UTC | qué pasó | evidencia |
|---|---|---|
| 21:40:54 | El dueño pulsa el botón de Telegram. Venta cerrada, perfil actualizado. | `exec 10675` · `sale_closed` |
| 22:11:32 | El operador escribe «Hola M…, ¿cómo vas?». El echo llega y se descarta. | `exec 10678` · 132 ms · Stop |
| 22:14:31 | El operador propone la entrega para pasado mañana. El echo llega y se descarta. | `exec 10682` · 189 ms · Stop |
| 22:15:00 | La clienta responde con una nota de voz. Entra como `content=''`. | `exec 10686` · 9 542 ms · conv nueva `conv-08-19-c` v1 |
| 22:15:15 | **El bot irrumpe**: «Hola, ¿cómo estás? Aquí estoy para ayudarte con lo que necesites.» | `agent_turn` · 5 739 tokens |
| 22:15:30 | El dueño vuelve a pulsar el botón. «Ya estaba confirmada. No se duplicó nada.» | `exec 10692` |
| ≈22:16 | **El dueño apaga el workflow.** No vuelve a ejecutarse nada de WhatsApp. | última ejecución 10691 a las 22:15:19.789Z |

### 2.5 El `client_user` quedó bien `[DB]`

```
phone_number:      NULL                  ← camino BSUID puro, como diseñó P14
bsuid:             "CO.…"
display_name:      "<nombre>"
lifecycle_stage:   "customer"            ← promovido desde 'engaged'
first_contact_at:  2026-08-19 19:36:09.794
last_contact_at:   2026-08-19 22:15:08   ← lo tocó el audio, no un mensaje real

profile: {
  "city":             "Manizales",
  "phone":            "<teléfono>",
  "full_name":        la clienta,
  "first_name":       "M.",
  "shipping_address": "«dirección», 17 chars",
  "purchase_count":   1,
  "purchases": [ {
      "date":            "2026-08-19T21:40:55.114224+00:00",
      "quantity":        2,
      "total":           null,           ← sin precio
      "product_id":      null,           ← el producto nunca se resolvió
      "conversation_id": "conv-08-19-a-…-436ee515497c"
  } ]
}
```

Es la **primera compra registrada en la historia de la base**. Quedó, como estaba previsto, sin `product_id` ni
`total`: la clienta se refirió al café por deixis y el DAG cerró `user_confirmation` sin exigir producto.

---

## 3. La imagen de la 01:36 UTC del 2026-08-20

> **Veredicto: esa imagen no existe en ninguna parte. No hay ejecución, no hay fila en la DB. Y no fue la forma del
> payload — fue el apagado.**

A las 01:36 UTC del 20 de agosto el webhook llevaba tres horas sin existir, por decisión del operador. n8n estaba
despierto: los crons horarios `exec 10697` (01:00:37Z) y `exec 10699` (02:00:37Z) enmarcan ese instante y ambos
corrieron sin novedad. No fue cold start, no fue `If Message Exists`, no fue un tipo de medio no reconocido. Chakra
recibió un 404 y no reintentó.

**Consecuencia esperada del apagado, pero consecuencia real**: ese mensaje de la clienta está perdido, y no hay forma
de recuperarlo desde el sistema.

### 3.1 La forma del medio, de la única imagen de esta misma conversación

`exec 10487`, del 2026-08-19T19:37:03.405Z — la «imagen ciega» sobre la que la clienta habló por deixis.

```json
"messages": [
  {
    "from_user_id": "CO.…",
    "id":           "wamid.…",
    "timestamp":    "1787168215",
    "type":         "image",
    "image": {
      "mime_type": "image/jpeg",
      "sha256":    "ThKwn3b8ea3WeMRr3Xad1kt2tRCPj5j+hzyPRFRiKXU=",
      "id":        "1093320880328215",
      "url":       "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1093320880328215&source=webhook&ext=1787168517&hash=ATwiChdVx3re6wT78lIA8tzJR1M-TQK_aL-jUrwEwgjZGA"
    }
  }
]
```

- La clave del objeto de medio **lleva el nombre del tipo** (`image`, y presumiblemente `audio` para el de las 22:15).
- **No hay `caption`** en este mensaje. Cuando existe, es hermana de `mime_type` dentro del objeto `image`.

### 3.2 La aritmética del `ext`

```
ext        = 1787168517   → 2026-08-19T19:41:57Z
timestamp  = 1787168215   → 2026-08-19T19:36:55Z
─────────────────────────────────────────────────
vida útil  =        302 s   (5 min 2 s)
```

No es casualidad de este mensaje. Medido sobre las 8 imágenes que quedan en la retención, la diferencia es
**301 o 302 segundos, siempre**:

| exec | timestamp | ext | Δ | caption |
|---|---|---|---:|---|
| 10053 | 1786802618 | 1786802920 | 302 s | — |
| 10105 | 1786802710 | 1786803011 | 301 s | sí |
| 10113 | 1786802721 | 1786803023 | 302 s | sí |
| 10145 | 1786802754 | 1786803055 | 301 s | sí |
| 10147 | 1786802753 | 1786803055 | 302 s | — |
| 10165 | 1786802782 | 1786803084 | 302 s | — |
| 10169 | 1786802788 | 1786803089 | 301 s | — |
| 10487 | 1787168215 | 1787168517 | 302 s | — |

- **No hay `oe` ni `oh`.** Los cuatro parámetros son siempre `mid`, `source=webhook`, `ext` y `hash`. La firma va en
  `hash`.
- **`caption` es opcional**, hermana de `mime_type`. Aparece en 3 de 8.
- **Cinco minutos es el presupuesto real.** El webhook de `exec 10487` llegó 8 s después del `timestamp`; quedaban
  294 s para descargar el binario. Cualquier diseño que guarde la URL en vez del binario está guardando un enlace
  muerto.

---

## 4. Contraste de formas

> **Hipótesis confirmada: `contacts[0].user_id` está presente en 247 de 247 payloads. Es la única ruta de identidad
> que nunca falla.**

| campo | inbound normal | echo del operador | inbound con medio | status callback |
|---|---|---|---|---|
| `changes[0].field` | `messages` | **`smb_message_echoes`** | `messages` | `messages` |
| array portador | `messages[]` | **`message_echoes[]`** | `messages[]` | **`statuses[]`** |
| `messaging_product` | sí | sí | sí | sí |
| `metadata.phone_number_id` | sí | sí | sí | sí |
| **`contacts[0].user_id`** | **sí — 100 %** | **sí — 100 %** | **sí — 100 %** | **sí — 100 %** |
| `contacts[0].wa_id` | intermitente | **nunca (0/10)** | intermitente | intermitente |
| `contacts[0].profile` | intermitente | siempre (10/10) | intermitente | intermitente |
| `…[0].id` (wamid) | sí | sí | sí | sí |
| `…[0].timestamp` | sí | sí | sí | sí |
| `…[0].type` | `text` | `text` | `image` / `audio` | **ausente** |
| identidad del emisor | `from_user_id` | **`to_user_id`** (destinatario) | `from_user_id` | `recipient_user_id` |
| `from` | solo con `wa_id` | **= el negocio** | solo con `wa_id` | ausente |
| cuerpo | `text.body` | `text.body` | `image.url` + `sha256` | ninguno |

### 4.1 Presencia medida sobre las 247 ejecuciones

| campo | presencia | % |
|---|---:|---:|
| `contacts[0].user_id` | **247 / 247** | **100 %** |
| `contacts[0].wa_id` | 150 / 247 | 60,7 % |
| `contacts[0].profile` | 93 / 247 | 37,7 % |
| `messages[0].from_user_id` | 79 / 247 | 32,0 % |

### 4.2 Conclusión práctica

**Normalizar por `contacts[0].user_id`, no por `from_user_id`.** `from_user_id` solo existe en la forma inbound —
79 de 247 payloads — y por diseño nunca aparecerá en un echo, donde el campo análogo se llama `to_user_id` y significa
lo contrario.

El detalle irónico: `map_webhook_data_arenillo` **ya copia** `contacts[0].user_id` (asignación `p14-contact-user-id`),
y por eso el BSUID sobrevive intacto en los echoes. **La identidad ya llega bien.** Lo que falta es que el guardián
deje de preguntarle a `messages[]`.

Un detalle que importa para discriminar formas: `contacts[]` también viene en los status callbacks, así que su
presencia **no basta** para decidir si algo es un mensaje. Lo que discrimina es **cuál array está poblado** —
`messages[]`, `message_echoes[]` o `statuses[]` — y ese es el switch que hoy no existe.

---

## 5. Barrido de silencio

247 ejecuciones de `master` y sus 247 pares en `cafe_arenillo_v2`, cubriendo los días 2026-08-15, 16, 18 y 19.
**Todas marcadas `success`. Cero errores en toda la retención.**

| métrica | n | % |
|---|---:|---:|
| Ejecuciones del pipeline | 247 | 100 % |
| Terminaron en `Stop` | **168** | 68 % |
| …de ellas en < 100 ms, marcadas «success» | **163** | 66 % |
| Llegaron a responder (5–15 s) | 79 | 32 % |

### 5.1 Las 168 paradas silenciosas, por causa

| causa | n | desglose | ¿legítimo? |
|---|---:|---|---|
| **Status callback** | 158 | 63 `delivered` · 61 `sent` · 33 `read` · **1 `failed`** | Sí — salvo el `failed` |
| **Echo del operador descartado** | 10 | 10 `text`, todos del 2026-08-19 | **No — pérdida real** |
| **Forma no reconocida** | **0** | ninguna | — |
| **Total** | **168** | de 247 | |

El resultado tiene un lado tranquilizador: **no hay formas desconocidas.** Cada payload de la retención encaja en una
de las cuatro formas de §4. El agujero no es la variedad del mundo, es que el guardián solo reconoce una de ellas.

Se escurren dos cosas por ahí. Una es el `status: failed`: un mensaje del bot que WhatsApp no pudo entregar, y del que
nadie se enteró — el mismo `Stop` que traga los echoes traga los avisos de no-entrega. La otra es lo que **sí** pasó el
filtro:

### 5.2 Los 79 inbound que sí se procesaron, por tipo

| type | n | ¿trae texto? | qué hizo el bot |
|---|---:|---|---|
| `text` | 51 | sí | respondió con contexto |
| **`unsupported`** | 16 | **no** | **respondió a la nada** |
| `image` | 8 | solo caption | respondió a ciegas |
| **`audio`** | 3 | **no** | **respondió a la nada** |
| **`edit`** | 1 | **no** | **se re-presentó** |
| **Sin contenido legible** | **20** | | **25 % de todo lo que el bot contestó** |

### 5.3 El patrón, dimensionado

El pipeline es simétrico en su silencio: **descarta sin decir nada lo que no reconoce** (168 ejecuciones, 10 de ellas
mensajes reales del operador) y **responde en serio a lo que no puede leer** (20 de 79 turnos). El default en ambos
extremos es no avisar — ni al operador, ni al log, ni a Chakra. Un `NoOp` llamado `Stop` y un HTTP 200.

**Alcance del barrido.** «Histórico disponible» son cuatro días. n8n conserva 1 178 ejecuciones en total (IDs
1345–10820, desde 2025-09-15), pero de `master`/`cafe_arenillo_v2` solo sobreviven las de 2026-08-15 en adelante. Los
conteos de esta sección son **cotas inferiores**, no totales históricos. Y no hay telemetría que permita reconstruir lo
purgado: es la deuda #3 otra vez.

---

## Anexo — inventario de evidencia

| exec | workflow | UTC | qué es |
|---|---|---|---|
| 10487 | master | 2026-08-19T19:37:03.405Z | Imagen de la clienta (forma del medio, §3) |
| 10513 | master | 2026-08-19T19:40:26.787Z | Evento `edit` → mensaje vacío |
| 10585–10665 | master | 19:42–19:45 | 8 echoes del operador, todos descartados |
| 10675 | operator_confirm_telegram | 21:40:54.921Z | Botón: venta cerrada |
| 10678 / 10679 | master / v2 | 22:11:34.461Z | Echo #9 — payload completo en §1.1 |
| 10682 / 10683 | master / v2 | 22:14:33.707Z | Echo #10 — promesa de entrega perdida |
| 10686 / 10687 | master / v2 | 22:15:07.921Z | Audio `content=''` → conversación nueva + saludo genérico |
| 10688–10691 | master / v2 | 22:15:19Z | Status callbacks. **Últimas ejecuciones de WhatsApp de la historia** |
| 10692 | operator_confirm_telegram | 22:15:30.326Z | Segundo botón, idempotente |
| 10697 / 10699 | crons | 2026-08-20 01:00 / 02:00Z | Enmarcan la imagen perdida de la 01:36 |
| 10820 | cron | 2026-08-22T14:00:54.020Z | n8n sigue vivo |
