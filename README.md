# Sales AI Agent — Backend

Backend de ventas por WhatsApp donde **la IA conversa y el backend gobierna**. El LLM escribe las palabras; el backend decide la estrategia del turno, valida qué datos se pueden guardar y cuándo, y garantiza que el flujo de compra respete el orden correcto.

---

## Tabla de contenidos

1. [¿Qué problema resuelve?](#qué-problema-resuelve)
2. [Cómo funciona: el patrón de dos llamadas](#cómo-funciona-el-patrón-de-dos-llamadas)
3. [El GoalStrategyEngine](#el-goalstrategyengine)
4. [DAG gates en `agent_action`](#dag-gates-en-agent_action)
5. [Máquina de estados de conversación](#máquina-de-estados-de-conversación)
6. [Endpoints](#endpoints)
7. [Ejemplo concreto end-to-end](#ejemplo-concreto-end-to-end)
8. [Limitaciones conocidas](#limitaciones-conocidas)
9. [Decisiones técnicas](#decisiones-técnicas)
10. [Estructura del proyecto](#estructura-del-proyecto)
11. [Correr localmente](#correr-localmente)
12. [Producción](#producción)

---

## ¿Qué problema resuelve?

Un chatbot típico de WhatsApp funciona así:

```
WhatsApp → LLM → respuesta
```

Simple, pero frágil: el LLM puede saltar pasos, pedir comprobante de pago sin haber confirmado nada, inventar precios, o confundirse entre turnos. El prompt ayuda pero no es código — no hay garantías.

Este backend se sienta en el medio:

```
WhatsApp → n8n → Backend → LLM → Backend → WhatsApp
```

Hace tres cosas que un backend de chatbot típico no hace:

1. **Le dice al LLM qué pedir a continuación.** El `GoalStrategyEngine` recorre un DAG de checkpoints y devuelve una directiva como "ya tienes producto y ciudad; falta dirección y teléfono; no empujes el cierre todavía".
2. **Valida cada dato extraído antes de aceptarlo.** Si el LLM dice "el usuario confirmó el pedido" pero todavía no tenemos teléfono, esa confirmación se ignora (y se registra un warning). El orden se respeta por código, no por rezar al prompt.
3. **Auto-escala a humano cuando corresponde.** Cuando se completan todos los datos de compra (incluyendo comprobante de pago), la conversación pasa a `human_handoff` automáticamente.

---

## Cómo funciona: el patrón de dos llamadas

Cada mensaje entrante en WhatsApp genera **exactamente 2 llamadas HTTP** de n8n al backend y **1 llamada al LLM**. Sin tool-calling, sin loops.

```mermaid
sequenceDiagram
    participant W as WhatsApp
    participant N as n8n
    participant B as Backend FastAPI
    participant L as LLM (gpt-4o-mini)
    participant DB as PostgreSQL

    W->>N: Webhook Chakra HQ

    rect rgb(240, 248, 255)
    Note over N,DB: LLAMADA 1 — Ingesta
    N->>B: POST /api/v1/ingest/message
    B->>DB: 1. Validar tenant + idempotencia
    B->>DB: 2. Upsert client_user + bloqueo
    B->>DB: 3. Conversación (ventana 24h)
    B->>DB: 4. Advisory lock + persistir inbound
    Note over B: 5. Debounce 5s
    B->>B: 6. GoalStrategyEngine → directive
    B->>DB: 7. Bump strategy_version
    B-->>N: strategy_directive + strategy_version + client_config<br/>user_context + product_catalog + business_context<br/>conversation_summary + recent_messages
    end

    N->>L: system prompt + directive + historial
    L-->>N: response_text + extracted_data

    rect rgb(255, 248, 240)
    Note over N,DB: LLAMADA 2 — Validación
    N->>B: POST /api/v1/agent/action
    B->>B: 1. Verificar strategy_version (409 si stale)
    B->>B: 2. DAG gates → merge extracted_data
    B->>DB: 3. Auto-escalate si all_complete
    B->>DB: 4. Persistir outbound + audit_log
    B-->>N: approved + final_response_text + side_effects
    end

    N->>W: final_response_text via Chakra
```

**Propiedad clave:** si el backend rechaza un dato (p. ej. `user_confirmation` sin teléfono), el texto que el LLM generó **igual se envía al usuario**. La conversación no se interrumpe — solo no se persiste el dato prematuro.

---

## El GoalStrategyEngine

Función pura: `(goal, extracted_context, business_rules) → StrategyDirective`. Sin DB, sin LLM, sin red. Corre en microsegundos.

### DAG actual del goal `close_sale`

```mermaid
graph TD
    A["📦 product_matched<br/>product_id"]
    B["👤 lead_qualified<br/>full_name · phone"]
    C["🏠 shipping_info_collected<br/>shipping_address · shipping_city"]
    D["✅ user_confirmed<br/>user_confirmation"]
    E["💳 payment_confirmed<br/>payment_confirmation"]

    A --> B --> C --> D --> E
```

> **Nota histórica:** los checkpoints `intent_identified` y `order_created` fueron removidos. `intent` nunca se capturaba de forma confiable (bloqueaba el auto-escalate), y `order_created` requería materializar órdenes en DB — algo que hoy se maneja manualmente por WhatsApp.

### Ejemplo de directiva generada

```
SALES PROGRESS: [████░░░░░░] 40%
NEXT INFO NEEDED: shipping_address, shipping_city
HINT (low priority, only when the conversation flows there naturally):
  try to learn the full delivery address (neighborhood, street, number, apartment).
IMPORTANT: Your priority is to have a warm, natural conversation.
Answer what the customer asks.
Do NOT mention or push toward the next step unless the customer brings it up.
Never end a message with 'puedo ayudarte con el pedido' or similar sales push.
```

### Business rules que modifican el DAG

Configurables por cliente en `clients.business_rules` (JSONB):

| Regla | Efecto |
|---|---|
| `skip_lead_qualification: true` | Elimina el checkpoint `lead_qualified`. |
| `require_id_number: true` | Agrega `identification_number` a los campos requeridos. |
| `require_email: true` | Agrega `email` a los campos requeridos. |

---

## DAG gates en `agent_action`

Cuando el LLM devuelve `extracted_data`, el backend lo mergea en `conversation.extracted_context` — **pero solo si el orden del DAG lo permite**. Los gates que existen hoy:

| Campo que el LLM intenta guardar | Requisito previo | Qué pasa si falta |
|---|---|---|
| `user_confirmation` | `full_name`, `phone`, `shipping_address`, `shipping_city` | Se descarta y se registra `warning:premature_summary_missing_<campos>` en `side_effects`. |
| `payment_confirmation` | `user_confirmation`, `phone`, `shipping_address` | Se descarta silenciosamente (warning en logs). |

Los demás campos (`product_id`, `full_name`, `phone`, `shipping_*`) se aceptan siempre que vengan con valor truthy — el DAG los ordena naturalmente por `blocked_by`.

**Auto-escalate:** cuando todos los checkpoints del DAG están completos, la conversación pasa de `active` → `human_handoff` y aparece `escalated:purchase_data_complete` en `side_effects`. n8n puede usar ese flag para notificar al operador humano.

---

## Máquina de estados de conversación

```mermaid
stateDiagram-v2
    [*] --> active : mensaje entrante
    active --> human_handoff : auto-escalate (all checkpoints OK)
    active --> closed
    human_handoff --> active : operador interviene
    human_handoff --> closed : operador cierra
    closed --> [*]
```

Estados y transiciones permitidas:

| Estado | Transiciones válidas |
|---|---|
| `active` | → `human_handoff`, `closed` |
| `human_handoff` | → `active`, `closed` |
| `closed` | (terminal) |

> **Histórico:** la máquina original tenía 7 estados (`idle`, `qualifying`, `selling`, `ordering`, etc.). En la práctica ninguna conversación transicionó fuera de `active`/`human_handoff`, así que se colapsó a 3 estados en el refactor 2026-04-19.

---

## Endpoints

Autenticación para todos los endpoints excepto `/health`:

```
Authorization: Bearer <SALES_AI_SERVICE_TOKEN>
X-Client-ID: <UUID del tenant>
```

El token se valida con `hmac.compare_digest` (constant-time). `X-Client-ID` identifica el tenant — todas las queries filtran por este ID.

### GET /health

Sin auth. `{"status": "ok"}`.

### POST /api/v1/ingest/message

Procesa un mensaje entrante.

**Request:**

```json
{
  "chakra_message_id": "wa-abc-123",
  "phone_number": "+573001234567",
  "content": "Hola, quiero café",
  "display_name": "Carlos",
  "message_type": "text"
}
```

**Response (simplificada):**

```json
{
  "should_respond": true,
  "conversation_id": "c420db40-…",
  "conversation_state": "active",
  "strategy_directive": "SALES PROGRESS: [░░░░░░░░░░] 0%\nNEXT INFO NEEDED: product_id\n…",
  "strategy_meta": {
    "goal": "close_sale",
    "progress_pct": 0,
    "current_checkpoint": "product_matched",
    "next_action": "Help the customer choose a product from the catalog.",
    "missing_fields": ["product_id"]
  },
  "strategy_version": 1,
  "client_config": {
    "system_prompt_template": "Eres Sebastian…",
    "ai_model": "gpt-4o-mini",
    "ai_temperature": 0.3,
    "business_rules": {…}
  },
  "user_context": {
    "display_name": "Carlos",
    "phone_number": "*********4567",
    "has_full_name": false,
    "has_email": false,
    "has_address": false,
    "has_city": false,
    "is_blocked": false
  },
  "product_catalog": [{"id": "<uuid>", "name": "…", "sku": "…", "price": 40000, …}],
  "business_context": "PRODUCT CATALOG:\n- Café Arenillo (id: <uuid>, sku: CAFE-001): $40.000 COP\n…",
  "conversation_summary": "CUSTOMER CONTEXT: New customer, no data collected yet.",
  "recent_messages": [ … ]
}
```

**Casos especiales:**
- Duplicado (mismo `chakra_message_id`): `{"should_respond": false, …}`.
- Cliente inactivo: `HTTP 404`.
- Usuario bloqueado: `HTTP 403`.
- Debounce: si llega otro mensaje en los 5 segundos siguientes, este turno no responde (`should_respond: false` con reason `debounce`).

### POST /api/v1/agent/action

Persiste el turno del agente y mergea datos extraídos.

**Request:**

```json
{
  "conversation_id": "c420db40-…",
  "strategy_version": 1,
  "response_text": "Hola, soy Sebastian de Café Arenillo. ¿En qué te puedo ayudar?",
  "extracted_data": {
    "product_id": "<uuid del catálogo, NUNCA el SKU>",
    "full_name": "Juan Pérez",
    "phone": "3001234567",
    "shipping_city": "Manizales",
    "shipping_address": "Calle 10 #5-20",
    "user_confirmation": true,
    "send_image_url": "https://…"
  },
  "proposed_transition": null,
  "ai_model": "gpt-4o-mini",
  "prompt_tokens": 245,
  "completion_tokens": 38,
  "latency_ms": 820
}
```

> `proposed_action` existe en el schema por compatibilidad pero ya no se valida ni ejecuta. El contrato real del turno es: `response_text` + `extracted_data`.

**Response:**

```json
{
  "approved": true,
  "final_response_text": "Hola, soy Sebastian de Café Arenillo. ¿En qué te puedo ayudar?",
  "new_state": "active",
  "side_effects": ["context_updated:['full_name', 'phone']"],
  "rejection_reason": null
}
```

**Side effects observables:**
- `context_updated:[…]` — campos mergeados a `extracted_context`.
- `warning:premature_summary_missing_<campos>` — el LLM intentó confirmar algo sin datos suficientes.
- `escalated:purchase_data_complete` — todos los checkpoints completos; conversación pasa a `human_handoff`.
- `state_changed:active→human_handoff` — transición aplicada.
- `transition_rejected:…` — transición inválida descartada.

**Casos especiales:**
- `strategy_version` desfasada (otro mensaje llegó entre Llamada 1 y 2): `HTTP 409 {"error": "stale_context"}`. Solución: n8n re-llama a `ingest` con el mismo mensaje.
- Conversación no encontrada: `HTTP 404`.

---

## Ejemplo concreto end-to-end

Una venta completa, tal como se refleja hoy en la DB:

```
Cliente: "Hola, me interesa café"
  ─► ingest: progress 0%, checkpoint=product_matched
  ─► LLM devuelve: "Hola, soy Sebastian de Café Arenillo. ¿En qué te puedo ayudar?"
       extracted_data: {}
  ─► agent/action: context_updated:[] (nada que guardar)

Cliente: "Quiero 3 bolsas"
  ─► ingest: progress 0%, checkpoint=product_matched
  ─► LLM: "¡Listo! Para continuar con el pedido, ¿me das tu nombre completo?"
       extracted_data: {"product_id": "<uuid>"}
  ─► agent/action: context_updated:['product_id'], progress ahora 20%

Cliente: "Juan Pérez, 3001234567"
  ─► ingest: progress 20%, checkpoint=lead_qualified
  ─► LLM: "Gracias Juan. ¿A qué ciudad y dirección enviamos?"
       extracted_data: {"full_name": "Juan Pérez", "phone": "3001234567"}
  ─► agent/action: context_updated:['full_name','phone'], progress 40%

Cliente: "Manizales, Calle 10 #5-20"
  ─► LLM: resumen + pide confirmación
       extracted_data: {"shipping_city":"Manizales", "shipping_address":"Calle 10 #5-20"}
  ─► agent/action: context_updated:['shipping_city','shipping_address'], progress 60%

Cliente: "Sí, confirmo"
  ─► LLM: comparte medios de pago
       extracted_data: {"user_confirmation": true}
  ─► agent/action:
       [DAG gate verifica: full_name ✓, phone ✓, shipping_address ✓, shipping_city ✓]
       context_updated:['user_confirmation'], progress 80%

Cliente: (envía foto del comprobante)
  ─► LLM: "Recibido, gracias. Alguien del equipo te escribe pronto."
       extracted_data: {"payment_confirmation": true}
  ─► agent/action:
       [DAG gate verifica: user_confirmation ✓, phone ✓, shipping_address ✓]
       context_updated:['payment_confirmation'], progress 100%
       auto-escalate → state: active → human_handoff
       side_effects: ["escalated:purchase_data_complete",
                      "state_changed:active→human_handoff"]
```

En cualquier punto, si el LLM intenta guardar `user_confirmation` sin los 4 datos previos, el gate lo rechaza y aparece `warning:premature_summary_missing_phone+shipping_city` en `side_effects`. El texto al usuario igual se envía — solo no se marca como confirmado.

---

## Limitaciones conocidas

Estas son las fricciones reales del sistema tal como está hoy (2026-04-20). No están "rotas", pero conviene conocerlas para planear.

### Flujo y conversación

- **Debounce frágil ante ráfagas rápidas.** El backend espera 5 s tras commitear el primer mensaje para ver si llegó otro. Si el cliente manda "Hola" + "Buena noche" con 10 s de diferencia, el debounce no los agrupa (el primero ya pasó los 5 s). Resultado: dos outbounds casi idénticos. **Fix propuesto** (pendiente): cambiar a debounce "rearmable" que espere N segundos de silencio desde el ÚLTIMO mensaje, no desde el primero.
- **Reset de `extracted_context` por inactividad.** Si la conversación estuvo idle 30+ minutos, el backend limpia `extracted_context` al siguiente mensaje. Esto evita arrastrar datos viejos pero también borra contexto legítimo si el cliente tarda en responder.
- **El LLM sigue siendo la fuente de verdad de la extracción.** Los DAG gates protegen el ORDEN pero no la PRECISIÓN. Si el LLM decide que `full_name="Sebastian"` (solo primer nombre), el backend lo acepta. La mitigación es vía prompt (regla "pide el apellido si responden con una palabra"), no por código. Lo mismo con ciudad inferida del nombre, cantidad deducida del contexto, etc.
- **Foto del producto depende del LLM.** El rail `send_image_url` funciona por convención: el LLM debe incluirlo en `extracted_data` la primera vez y nunca más. No hay memoria backend que impida que lo envíe dos veces — solo la regla en el system prompt (reforzada 2026-04-20).

### Modelo de datos

- **Leads y órdenes no se materializan en DB.** Las tablas `leads`, `orders`, `order_line_items` existen pero llevan vacías desde el inicio. Los "datos del lead" viven en `conversations.extracted_context` como JSONB. Para remarketing o reportes hay que consultar ese JSONB. Si en el futuro se necesita materialización estructurada, hay que decidir: vista materializada + CRON, o sincronización on-write en `agent_action`.
- **Pago manual.** La confirmación de pago es un flag booleano en `extracted_context` activado cuando el cliente dice "ya pagué" + envía comprobante. No hay verificación contra gateway, ni número de referencia, ni monto — un humano debe validar el comprobante en WhatsApp.
- **`agent_turn_count` y `proposed_action` en schema pero no se leen.** Columnas heredadas del diseño original con handlers de acciones. Se mantienen para no hacer migración downgrade; el código nuevo las ignora.

### Integración n8n

- **Fallo silencioso del subworkflow.** Si el master workflow de n8n se ejecuta pero el subworkflow `cafe_arenillo_v2` no dispara (ID cambiado, trigger modificado, timeout), el backend no recibe el ingest y la conversación queda sin respuesta. **No hay telemetría desde el backend sobre esto** — solo se detecta mirando `/executions` en n8n. Ver incidente 2026-04-19.
- **Timeout acoplado al debounce.** El backend introduce 5 s de latencia por el debounce. Si el HTTP timeout de n8n al backend es < 8-10 s, toda ingesta falla. Ajustar en n8n si se modifica el debounce.

### Operaciones

- **Sin dashboard.** La única forma de ver qué pasó en una conversación es `SELECT * FROM messages / conversations / audit_log`. No hay métricas agregadas, ni alertas, ni una vista de operador humano para `human_handoff`.
- **Auth service-to-service con un solo token.** Todos los callers usan el mismo `SALES_AI_SERVICE_TOKEN`. No hay rotación automática ni scopes distintos por caller.
- **Tests sin cobertura de integración.** Los 37 tests actuales son pure Python (goal_strategy, prompt_context, health/auth). No hay test end-to-end que corra contra Postgres real, así que regresiones de `ingest` o `agent_action` se detectan solo en staging/prod.
- **Multi-tenant defendido por aplicación, no por DB.** Cada tabla tiene `client_id` FK nullable-false, pero no hay RLS (Row-Level Security) de Postgres. Un bug que olvide filtrar por `client_id` en una query podría leer datos cross-tenant. Revisar al tercer cliente.

### Costos y rendimiento

- **1 LLM call por mensaje entrante.** Predecible pero no amortizable — si el cliente manda 10 mensajes seguidos, son 10 llamadas (aunque algunas se descarten por debounce tras el commit). Prompt con `business_context` + `recent_messages` + `strategy_directive` pesa ~1500-2500 tokens de entrada por turno.
- **Advisory lock + transacción por mensaje.** Aguanta bien hasta cientos de conversaciones concurrentes en Postgres Flexible, pero no está testeado con ráfagas masivas.

---

## Decisiones técnicas

### ¿Por qué async everywhere?

WhatsApp manda ráfagas. Async SQLAlchemy + asyncpg permite atender requests concurrentes sin bloquear el event loop. El `pg_advisory_xact_lock` serializa los mensajes de la misma conversación sin necesidad de Redis o colas.

### ¿Por qué advisory locks en vez de Redis?

El advisory lock es transaccional — se libera automáticamente con commit/rollback. No hay locks huérfanos si la app falla, y no agrega infra extra (ya tenemos Postgres).

### ¿Por qué ENUMs como VARCHAR + CHECK?

Agregar un valor a un `ENUM` nativo requiere `ALTER TYPE`, que bloquea la tabla. Con `VARCHAR + CHECK CONSTRAINT`, modificar el set de valores es `DROP CONSTRAINT` + `ADD CONSTRAINT`, sin downtime.

### ¿Por qué el agente no tiene tools?

El tool-calling genera loops impredecibles: el LLM llama A, interpreta, llama B, etc. Cada iteración agrega latencia y costo. Este patrón es lineal: 1 LLM call por mensaje, 2 HTTP calls al backend. El costo es constante.

### ¿Por qué `strategy_version`?

Entre Llamada 1 y Llamada 2 puede llegar otro mensaje, o n8n puede hacer retry. `strategy_version` incrementa en cada ingest. Si la Llamada 2 trae una versión vieja, el backend responde 409 y n8n vuelve a llamar a ingest. Evita guardar datos sobre un contexto obsoleto.

### ¿Por qué el precio viene del catálogo, no del agente?

El LLM puede alucinar precios. El sistema lee `products.price` cuando arma el `business_context`. Incluso si el LLM repite un precio en `response_text`, no hay mutación backend basada en ese precio — hoy no se materializan órdenes, y cuando se hagan, el precio vendrá del catálogo.

### Multi-tenant por `client_id`

Toda tabla tenant-facing tiene `client_id` FK no nulable. Toda query filtra por `client_id`. No hay RLS de Postgres — la separación es por convención en la capa de servicio.

---

## Estructura del proyecto

```
agent-backend/
│
├── sales_agent_api/              # Contexto de build de Docker
│   ├── Dockerfile                # Python 3.12-slim, puerto 8000
│   ├── requirements.txt
│   └── app/
│       ├── main.py               # App factory, auth middleware, routers
│       │
│       ├── core/
│       │   └── database.py       # Async SQLAlchemy + resolución de credenciales
│       │
│       ├── models/
│       │   └── core.py           # ORM: 6 tablas activas (Client, ClientUser,
│       │                         #   Product, Conversation, Message, AuditLog)
│       │                         # Tablas leads/orders/order_line_items existen
│       │                         # en Postgres pero sin mapeo ORM.
│       │
│       ├── api/v1/
│       │   ├── ingest.py         # POST /api/v1/ingest/message
│       │   └── agent.py          # POST /api/v1/agent/action
│       │
│       └── services/
│           ├── state_machine.py  # 3 estados + validate_transition
│           ├── goal_strategy.py  # GoalStrategyEngine (DAG navigator)
│           ├── prompt_context.py # format_business_context + conversation_summary
│           ├── ingest.py         # Servicio de ingesta (10 pasos + debounce)
│           └── agent_action.py   # Merge de extracted_data + DAG gates + auto-escalate
│
├── migrations/versions/
│   ├── 001_initial_schema.sql
│   ├── 002_peer_review_hardening.sql
│   ├── 003_enrich_cafe_demo.sql
│   ├── 004_improve_cafe_demo_prompt.sql
│   └── 005_shipping_zones_product_images.sql
│
└── tests/
    ├── test_health.py                       # 5 tests de auth/health
    └── services/
        ├── test_goal_strategy.py            # 19 tests del engine
        └── test_prompt_context.py           # 13 tests de formatters
```

Total: **37 tests**, todos pure Python — sin DB, sin red. Corren en ~0.02 s.

---

## Correr localmente

### Prerrequisitos

- Python 3.12
- Postgres 16 (local o acceso a Azure)

### Instalación

```bash
git clone https://github.com/SRam3/agent-backend.git
cd agent-backend
pip install -r sales_agent_api/requirements.txt
```

### Configuración

Crear `sales_agent_api/.env`:

```dotenv
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/sales_ai
SALES_AI_SERVICE_TOKEN=mi-token-local-de-prueba
ENV=dev
```

O resolviendo desde Azure Key Vault:

```dotenv
KEY_VAULT_URL=https://<KEY_VAULT_NAME>.vault.azure.net/
AZURE_CLIENT_ID=<client-id-de-la-managed-identity>
SALES_AI_SERVICE_TOKEN=<token>
ENV=dev
```

### Correr tests

```bash
pytest tests/ -v
```

### Arrancar la app

```bash
cd sales_agent_api
uvicorn app.main:app --reload --port 8000
```

Docs interactivas: http://localhost:8000/api/docs (solo si `ENV != production`).

### Docker

```bash
docker build -t sales-agent-api -f sales_agent_api/Dockerfile sales_agent_api

docker run -p 8000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/sales_ai" \
  -e SALES_AI_SERVICE_TOKEN="mi-token" \
  -e ENV="dev" \
  sales-agent-api
```

---

## Producción

| Componente | Detalles |
|---|---|
| **Backend URL** | `https://<CONTAINER_APP>.azurecontainerapps.io` |
| **Postgres** | `<POSTGRES_HOST>.postgres.database.azure.com` / DB `sales_ai` |
| **Key Vault** | `<KEY_VAULT_NAME>` (DBUSERNAME, DBPASSWORD, DBHOST, DBNAME, sales-ai-service-token) |
| **Managed Identity** | `<MANAGED_IDENTITY_NAME>` — accede a Key Vault y ACR sin contraseñas en código |
| **CI/CD** | Push a `main` → GitHub Actions → tests → build Docker → push ACR → rollout Container App |
| **Cliente** | Café Arenillo — ID `00000000-0000-0000-0000-000000000001` |
| **n8n** | `https://<N8N_CONTAINER_APP>.azurecontainerapps.io` (orquesta Chakra ↔ backend ↔ OpenAI) |
