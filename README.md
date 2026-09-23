# Sales AI Agent — Backend

Plataforma para que **empresas vendan por WhatsApp**. Un cliente escribe, un asistente conversa con él en nombre del negocio, arma el pedido y una persona del negocio confirma el pago.

Es **multi-negocio**: cada empresa tiene su catálogo, sus reglas y su tono, y sus datos están separados de los demás. Hoy opera con su primer negocio en producción; lo que es común a todos y lo que queda específico de cada uno se irá definiendo con los siguientes.

> **La idea central: la IA conversa, el backend decide.**
> El modelo de lenguaje escribe las respuestas y *propone* datos ("el cliente se llama Ana", "quiere 2 unidades"). El backend valida cada propuesta con reglas fijas antes de guardarla. Los precios, el total, el orden del pedido y la confirmación del pago nunca los decide la IA.

```mermaid
flowchart LR
    LLM["🤖 IA<br/>escribe y propone"] -- "propuesta" --> B{"🛡️ Backend<br/>¿cumple las reglas?"}
    B -- "sí" --> DB[("💾 se guarda")]
    B -- "no" --> X["🗑️ se descarta<br/>+ warning"]
    B -- "texto final" --> C["💬 Cliente"]
```

---

## Contenido

1. [Las piezas del sistema](#1-las-piezas-del-sistema)
2. [Qué pasa con cada mensaje](#2-qué-pasa-con-cada-mensaje)
3. [El camino de una venta](#3-el-camino-de-una-venta)
4. [El operador humano](#4-el-operador-humano)
5. [La memoria del vendedor](#5-la-memoria-del-vendedor)
6. [Estados de una conversación](#6-estados-de-una-conversación)
7. [Garantías verificadas en CI](#7-garantías-verificadas-en-ci)
8. [API](#8-api)
9. [Estructura del código](#9-estructura-del-código)
10. [Correr localmente](#10-correr-localmente)
11. [Dónde está cada cosa](#11-dónde-está-cada-cosa)

---

## 1. Las piezas del sistema

```mermaid
flowchart LR
    C["📱 Cliente<br/>WhatsApp"] <--> K["Chakra HQ<br/>API de WhatsApp"]
    O["🧑 Operador<br/>app de WhatsApp"] -. "sus mensajes<br/>(echoes)" .-> K
    K <--> N["⚙️ n8n<br/>orquesta"]
    N <--> B["🛡️ Backend<br/>FastAPI"]
    N <--> AI["🤖 OpenAI<br/>gpt-4o-mini"]
    B <--> DB[("💾 PostgreSQL")]
    N -- "venta lista" --> T["📨 Telegram<br/>del operador"]
    T -- "botón «Confirmar pago»" --> B
```

| Pieza | Qué hace | Qué **no** hace |
|---|---|---|
| **n8n** | Recibe el webhook, llama al backend, llama a la IA y envía la respuesta | No guarda estado ni decide nada del pedido |
| **Backend** (este repo) | Guarda todo, decide qué falta, valida lo que la IA propone, arma el resumen con precios | No genera lenguaje |
| **IA** | Conversa con el cliente y extrae datos del texto | No tiene herramientas ni memoria propia; no confirma pagos |
| **Operador** (persona del negocio) | Atiende cuando hace falta y confirma el pago desde Telegram | — |

### Qué configura cada negocio

Cada empresa es un *tenant*: toda petición trae su identificador (`X-Client-ID`) y toda consulta a la base filtra por él.

```mermaid
flowchart LR
    subgraph N["🏢 Configuración de un negocio"]
        direction TB
        P["🗣️ Personalidad y tono<br/>(prompt del asistente)"]
        C["📦 Catálogo y precios"]
        R["📋 Reglas de negocio<br/>envíos · medios de pago · descuentos · pausas"]
        M["🤖 Modelo de IA y temperatura"]
    end
    N --> E["⚙️ Motor común<br/>flujo de venta · validaciones · memoria · estados"]
```

Cambiar precios, tarifas de envío o medios de pago es un cambio de datos, no de código. Algunas partes del flujo todavía nacieron del primer negocio (por ejemplo, los detalles de pedido que se capturan); generalizarlas es trabajo pendiente.

---

## 2. Qué pasa con cada mensaje

Cada mensaje del cliente produce **dos llamadas al backend y una a la IA**. Nunca hay bucles.

```mermaid
sequenceDiagram
    autonumber
    participant N as n8n
    participant B as Backend
    participant AI as IA

    N->>B: POST /ingest/message
    Note right of B: guarda el mensaje,<br/>decide si se responde<br/>y qué dato falta
    B-->>N: contexto + instrucción + versión
    N->>AI: prompt armado con ese contexto
    AI-->>N: respuesta + datos propuestos
    N->>B: POST /agent/action (con la versión)
    Note right of B: valida los datos,<br/>puede reemplazar el texto<br/>por el resumen del pedido
    B-->>N: texto final + ¿se envía?
    N->>N: envía por WhatsApp
```

**La versión (`strategy_version`) es un candado.** Si otro mensaje cambió la conversación mientras la IA pensaba, el backend rechaza la segunda llamada con `409 stale_context` y no guarda nada ([ADR-003](sales-ai-docs/docs/decisions/ADR-003-strategy-version.md)).

### ¿Se responde o no?

Antes de llamar a la IA, el backend decide si este mensaje merece respuesta. **El mensaje siempre se guarda**; lo único que cambia es si el bot habla.

```mermaid
flowchart TD
    M["📩 Mensaje entrante"] --> D{"¿Ya lo vimos?<br/>(mismo id de WhatsApp)"}
    D -- sí --> S1["🔇 no hace nada"]
    D -- no --> BL{"¿Cliente bloqueado?"}
    BL -- sí --> S2["⛔ 403"]
    BL -- no --> G["💾 se guarda"]
    G --> U{"¿Tiene texto legible?<br/>(foto, audio, reacción…)"}
    U -- no --> S3["🔇 calla"]
    U -- sí --> OP{"¿El operador escribió<br/>en los últimos 30 min?"}
    OP -- sí --> S4["🔇 calla: atiende un humano"]
    OP -- no --> DB{"Espera 5 s:<br/>¿llegó otro mensaje?"}
    DB -- sí --> S5["🔇 responde el último"]
    DB -- no --> R["🗣️ se llama a la IA"]
```

---

## 3. El camino de una venta

El pedido avanza por pasos en orden. El backend sabe cuál falta y se lo indica a la IA como sugerencia suave, sin presionar al cliente.

```mermaid
flowchart LR
    P["📦 Producto"] --> L["👤 Nombre<br/>+ teléfono"]
    L --> E["🏠 Ciudad<br/>+ dirección"]
    E --> R["🧾 Resumen<br/>(lo escribe el backend)"]
    R --> UC["✅ El cliente<br/>confirma"]
    UC --> PC["💳 El operador<br/>confirma el pago"]
    PC --> Z["🏁 Venta cerrada"]

    style R fill:#dbeafe,stroke:#1d4ed8,color:#0f172a
    style PC fill:#fef3c7,stroke:#b45309,color:#0f172a
```

Tres reglas protegen el cierre:

| Momento | Quién decide | Regla |
|---|---|---|
| **Resumen del pedido** | Backend | Cuando el pedido está completo (producto, cantidad, detalles del producto, nombre, teléfono, ciudad y dirección), el backend **reemplaza** el texto de la IA por un resumen con precio, envío y total calculados por él. Si la ciudad no tiene tarifa, dice que el envío se confirma aparte. ([ADR-010](sales-ai-docs/docs/decisions/ADR-010-backend-gobierna-resumen.md)) |
| **Confirmación del cliente** | Backend | Solo vale si el resumen se envió, sigue vigente, el "sí" llegó **después** del resumen y ese mensaje no cambia el pedido. Si el cliente corrige algo, la confirmación se anula y sale un resumen nuevo. |
| **Pago** | Solo el operador | Si la IA dice "ya pagó", se descarta siempre. El pago lo confirma el operador con un botón en Telegram. Confirmar dos veces no crea dos ventas. ([ADR-009](sales-ai-docs/docs/decisions/ADR-009-handoff-closure-loop.md)) |

```mermaid
sequenceDiagram
    participant C as Cliente
    participant B as Backend
    participant T as Telegram (operador)

    B->>C: 🧾 "Va el pedido: 2 unidades… total $85.000. ¿Todo bien?"
    C->>B: "Sí"
    B->>B: ✅ confirmación válida (llegó después del resumen)
    B->>T: aviso "venta lista" con botón
    Note over C,T: el cliente paga y manda el comprobante por WhatsApp;<br/>el operador lo revisa
    T->>B: 💳 Confirmar pago
    B->>B: registra la venta y cierra la conversación
```

---

## 4. El operador humano

Cuando el operador escribe desde la app de WhatsApp del negocio, ese mensaje (un *echo*) también entra al sistema ([ADR-013](sales-ai-docs/docs/decisions/ADR-013-operador-como-autor.md)):

```mermaid
flowchart LR
    O["🧑 Operador escribe"] --> E["Echo"]
    E --> H["📜 Queda en el historial<br/>marcado como [operador]"]
    E --> P["🔇 El bot se calla<br/>30 min en esa conversación"]
    E -. "nunca" .-> X["❌ No toca el pedido,<br/>ni el estado, ni la versión"]
```

El operador aporta **contexto**, no hechos: lo que escribe lo lee la IA, pero no avanza el pedido.

---

## 5. La memoria del vendedor

Un cliente que vuelve es reconocido. Una conversación dura 24 h desde el último mensaje; cuando el cliente vuelve después, el backend resume la anterior y guarda el resumen en su perfil antes de responder.

```mermaid
flowchart LR
    C1["💬 Conversación<br/>anterior"] -- "al abrir una nueva" --> S["📝 Resumen con IA<br/>(JSON estricto)"]
    S --> PR[("👤 Perfil del cliente<br/>nombre · ciudad · dirección<br/>compras · último resumen")]
    PR --> C2["💬 Conversación nueva<br/>arranca con ese contexto"]
```

La identidad del cliente es su **BSUID** de WhatsApp, no su teléfono, porque algunos clientes ocultan el número.

---

## 6. Estados de una conversación

```mermaid
stateDiagram-v2
    [*] --> active
    active --> human_handoff : el bot se repite 3 veces (circuit breaker)
    active --> closed : el operador confirma el pago
    human_handoff --> closed : el operador confirma el pago
    human_handoff --> active
    closed --> [*]
```

`closed` termina **la venta**, no la relación: si el cliente vuelve a escribir, se abre una conversación nueva.

---

## 7. Garantías verificadas en CI

Las reglas importantes están escritas como **invariantes** en [`sales-ai-docs/docs/system-model/`](sales-ai-docs/docs/system-model/). Cada una apunta a los tests que la prueban, y CI falla si un test declarado desaparece o si una garantía se afirma sin prueba.

```bash
python tools/check_invariants.py --report
```

Algunos ejemplos:

| Garantía | Invariante |
|---|---|
| El pago solo lo confirma el operador | `INV-OP-001` |
| Confirmar dos veces no crea dos ventas | `INV-OP-002` |
| Un mensaje repetido por WhatsApp no se procesa dos veces | `INV-MSG-004` |
| Un turno calculado sobre datos viejos no se aplica | `INV-CONV-003` |
| El echo del operador nunca mueve el pedido | `INV-OP-004` |

El reporte también muestra lo que **todavía no se cumple** y a qué frente de trabajo pertenece. Lo abierto, en orden de prioridad, está en el [ROADMAP](sales-ai-docs/docs/ROADMAP.md).

---

## 8. API

Todas las rutas, excepto `/health`, piden:

```
Authorization: Bearer <token>
X-Client-ID: <UUID del negocio>
```

| Ruta | Quién la llama | Token | Para qué |
|---|---|---|---|
| `GET /health` | cualquiera | — | Chequeo de vida |
| `POST /api/v1/ingest/message` | n8n | servicio | Llamada 1: guardar el mensaje y preparar el turno |
| `POST /api/v1/agent/action` | n8n | servicio | Llamada 2: validar la propuesta de la IA |
| `POST /api/v1/ingest/operator-echo` | n8n | servicio | Guardar un mensaje del operador |
| `POST /api/v1/operator/confirm-payment` | botón de Telegram | operador | Confirmar el pago y cerrar la venta |

El token del operador solo abre `/operator/*`, y el de servicio no la abre. Con `ENV` distinto de `production`, la documentación interactiva está en `/api/docs`.

---

## 9. Estructura del código

```
sales_agent_api/app/
├── main.py                  # arranque, autenticación, rutas
├── api/v1/                  # endpoints delgados: validan y delegan
└── services/
    ├── ingest.py            # llamada 1: guardar, decidir si responder, preparar el turno
    ├── agent_action.py      # llamada 2: validar datos, resumen, breaker, guardar respuesta
    ├── ingest_operator_echo.py  # mensajes del operador
    ├── confirm_payment.py   # cierre de la venta por el operador
    ├── goal_strategy.py     # qué paso del pedido falta (función pura)
    ├── order_summary.py     # resumen, precio, envío y compuerta de confirmación (puro)
    ├── state_machine.py     # los 3 estados y sus transiciones (puro)
    ├── conversation_summary.py  # memoria del vendedor
    ├── prompt_context.py    # bloques de contexto para la IA
    ├── language.py          # idioma del cliente
    └── validation.py        # formato de teléfono
migrations/versions/         # SQL numerado, aplicado a mano (001–015)
tests/                       # 343 tests, sin base de datos ni red
tools/check_invariants.py    # verificador del modelo de sistema
```

La lógica pura (estrategia, resumen, estados) no toca la base de datos, así que se prueba en milisegundos.

---

## 10. Correr localmente

```bash
pip install -r sales_agent_api/requirements.txt -r tools/requirements.txt

# tests y garantías
pytest tests/
python tools/check_invariants.py --report

# la app
cd sales_agent_api && uvicorn app.main:app --reload --port 8000
```

Variables de entorno mínimas (en `sales_agent_api/.env`):

```dotenv
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/sales_ai
SALES_AI_SERVICE_TOKEN=token-local
SALES_AI_OPERATOR_TOKEN=otro-token-local
ENV=dev
```

En producción, las credenciales de base de datos y la clave de OpenAI se leen de Azure Key Vault (`KEY_VAULT_URL`).

**Despliegue:** cada push a `main` que toca `sales_agent_api/` corre los tests, publica la imagen Docker y actualiza la Container App de Azure. Las migraciones se aplican a mano y su orden respecto del despliegue lo fija [ADR-012](sales-ai-docs/docs/decisions/ADR-012-orden-migracion-despliegue.md).

---

## 11. Dónde está cada cosa

| Pregunta | Dónde |
|---|---|
| ¿Qué falta y en qué orden? | [`ROADMAP.md`](sales-ai-docs/docs/ROADMAP.md): frentes abiertos, deudas y orden de cierre |
| ¿Por qué se decidió así? | [`decisions/`](sales-ai-docs/docs/decisions/): 13 ADRs |
| ¿Qué garantías tiene el sistema? | [`system-model/`](sales-ai-docs/docs/system-model/) + `tools/check_invariants.py` |
| ¿Qué pasó en un incidente? | [`postmortems/`](sales-ai-docs/docs/postmortems/) |
| ¿Qué se cerró y cómo? | [`registros/roadmap-historico.md`](sales-ai-docs/docs/registros/roadmap-historico.md) |
