# Sales AI Agent — Backend

Un backend de ventas por WhatsApp donde **la inteligencia artificial conversa, pero el backend gobierna**. El agente decide las palabras; el backend decide la estrategia, valida cada acción y protege las reglas de negocio.

---

## Tabla de contenidos

1. [¿Qué problema resuelve?](#qué-problema-resuelve)
2. [La innovación: tres capas de control](#la-innovación-tres-capas-de-control)
3. [Arquitectura: el patrón de dos llamadas](#arquitectura-el-patrón-de-dos-llamadas)
4. [Flujo de una conversación completa](#flujo-de-una-conversación-completa)
5. [Los endpoints en detalle](#los-endpoints-en-detalle)
6. [Ejemplo concreto: vender un café](#ejemplo-concreto-vender-un-café)
7. [El GoalStrategyEngine explicado](#el-goalstrategyengine-explicado)
8. [La máquina de estados](#la-máquina-de-estados)
9. [Decisiones técnicas](#decisiones-técnicas)
10. [Estructura del proyecto](#estructura-del-proyecto)
11. [Cómo correr el proyecto localmente](#cómo-correr-el-proyecto-localmente)

---

## ¿Qué problema resuelve?

La mayoría de los chatbots de ventas funcionan así:

```
WhatsApp → LLM → respuesta
```

Simple, pero frágil. El LLM puede inventar precios, saltarse pasos del proceso de venta, crear pedidos sin datos de envío, o simplemente divagar. No hay forma de garantizar que el agente siga las reglas del negocio porque esas reglas viven en el prompt, y los prompts no son código.

Este backend introduce una capa intermedia:

```
WhatsApp → Backend → LLM → Backend → WhatsApp
```

El backend le dice al LLM **qué perseguir** en cada turno (no solo qué pasó). Luego valida **cada propuesta** del LLM antes de ejecutarla. El LLM nunca puede crear un pedido con precios inventados, saltar el paso de recolección de datos, ni confirmar una orden sin dirección de envío — no porque se lo prohibamos en el prompt, sino porque esas acciones no existen en el backend a menos que se cumplan las condiciones.

---

## La innovación: tres capas de control

La mayoría de los sistemas confunden estas tres capas. Aquí están separadas y son independientes:

### Capa 1: ¿Qué puede hacer el agente? — Máquina de estados

Controla qué **acciones están permitidas** según la fase de la conversación.

```
Estado ACTIVE  → puede: preguntar, buscar productos, escalar
Estado SELLING → puede: presentar producto, proponer orden, preguntar
Estado ORDERING → puede: recopilar envío, confirmar orden, cancelar
```

Un agente en estado `active` **no puede** crear un pedido. No es una instrucción en el prompt — es que esa acción simplemente no está disponible. Esto se llama restricción arquitectónica.

### Capa 2: ¿Qué debe hacer el agente ahora? — GoalStrategyEngine

Controla qué **acción es óptima** dado el progreso de la conversación.

El engine evalúa un DAG (grafo de dependencias) de checkpoints y responde: "Tienes intent e producto identificados, pero falta el nombre del cliente. Tu tarea esta ronda: preguntar el nombre."

Esto es lo que recibe el LLM en su system prompt — no un listado de instrucciones genéricas, sino una directiva precisa basada en el estado real de la conversación.

### Capa 3: ¿Es válida esta mutación de negocio? — Validación de acciones

Controla qué **cambios en la base de datos son correctos**.

Cuando el agente propone "crear pedido", el backend verifica:
- ¿Existen los productos en el catálogo del cliente?
- ¿El precio viene de la tabla `products`, no del agente?
- ¿Hay ítems en el pedido?
- ¿El usuario confirmó explícitamente?

Si algo falla, el pedido no se crea — pero la respuesta de texto del agente **sí llega al usuario**. La conversación no se interrumpe.

> **Resultado:** El sistema tiene un 100% de predictibilidad en las mutaciones de negocio y mantiene la naturalidad del lenguaje del LLM. Las investigaciones académicas (PPDPP, ICLR 2024; ChatSOP, ACL 2025) reportan mejoras de 27–91% en controllability con este patrón.

---

## Arquitectura: el patrón de dos llamadas

Cada mensaje de WhatsApp genera exactamente dos llamadas HTTP desde n8n hacia este backend, y una llamada al LLM. Sin herramientas, sin loops, sin llamadas extra.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         FLUJO POR MENSAJE                           │
│                                                                     │
│  WhatsApp ──► n8n ──► LLAMADA 1: POST /api/v1/ingest/message       │
│                           │                                         │
│                           │  Backend hace (en 1 transacción):       │
│                           │  1. Validar cliente (tenant)            │
│                           │  2. Verificar idempotencia              │
│                           │  3. Upsert del usuario                  │
│                           │  4. Verificar si está bloqueado         │
│                           │  5. Encontrar/crear conversación (24h)  │
│                           │  6. Advisory lock (concurrencia)        │
│                           │  7. Guardar mensaje entrante            │
│                           │  8. Actualizar contadores               │
│                           │  9. Calcular estrategia (DAG)           │
│                           │  10. Persistir versión de estrategia    │
│                           │                                         │
│                           ▼                                         │
│                    Respuesta con:                                   │
│                    - strategy_directive (para el system prompt)     │
│                    - available_actions (qué puede proponer)         │
│                    - client_config (prompt base del cliente)        │
│                    - strategy_version (número de versión)           │
│                    - recent_messages (historial reciente)           │
│                           │                                         │
│                           ▼                                         │
│               n8n construye system prompt:                          │
│               client_config.system_prompt_template                  │
│               + strategy_directive                                  │
│               + available_actions                                   │
│                           │                                         │
│                           ▼                                         │
│               n8n llama al LLM (OpenAI)                            │
│               LLM produce: response_text + proposed_action          │
│                           │                                         │
│                           ▼                                         │
│              LLAMADA 2: POST /api/v1/agent/action                  │
│                           │                                         │
│                           │  Backend hace:                          │
│                           │  1. Verificar strategy_version          │
│                           │     (¿cambió la conversación?)          │
│                           │  2. Validar acción vs máquina estados   │
│                           │  3. Ejecutar handler de negocio         │
│                           │  4. Validar transición de estado        │
│                           │  5. Guardar mensaje saliente            │
│                           │  6. Escribir audit log                  │
│                           │                                         │
│                           ▼                                         │
│                    Respuesta con:                                   │
│                    - approved: true/false                           │
│                    - final_response_text                            │
│                    - new_state                                      │
│                    - side_effects                                   │
│                           │                                         │
│                           ▼                                         │
│               n8n envía final_response_text → WhatsApp             │
└─────────────────────────────────────────────────────────────────────┘
```

**Propiedad clave:** Si la llamada 2 rechaza la acción de negocio, el texto de respuesta igual llega al usuario. La conversación nunca se interrumpe por un fallo del backend.

---

## Flujo de una conversación completa

Este es un ejemplo real de cómo progresa una venta de principio a fin:

```
Cliente: "Hola, quiero pedir café"
                    │
                    ▼
           Backend → GoalStrategy
           goal: close_sale
           checkpoint actual: intent_identified
           directiva: "Pregunta qué está buscando el cliente"
           available_actions: [classify_intent, ask_question, ...]
                    │
                    ▼
           LLM: "¡Hola Carlos! ¿Qué tipo de café te interesa?"
           proposed_action: classify_intent
           proposed_transition: qualifying
                    │
                    ▼
           Backend valida:
           ✓ classify_intent permitida en estado active
           ✓ transición active→qualifying válida
           Estado: active → qualifying

─────────────────────────────────────────────

Cliente: "El café molido premium"
                    │
                    ▼
           Backend → GoalStrategy
           checkpoint actual: product_matched ✓, lead_qualified
           directiva: "Pregunta el nombre completo del cliente"
           available_actions: [ask_question, create_lead, ...]
                    │
                    ▼
           LLM: "¡Excelente elección! ¿Me puedes dar tu nombre completo?"
           proposed_action: update_lead_data
           extracted_data: {intent: "café molido", product_id: "uuid-cafe-molido"}
                    │
                    ▼
           Backend:
           ✓ update_lead_data permitida en estado qualifying
           ✓ qualification_data actualizada en base de datos

─────────────────────────────────────────────

Cliente: "Me llamo Carlos Pérez"
                    │
                    ▼
           Backend → GoalStrategy
           checkpoint actual: shipping_info_collected
           directiva: "Pide la dirección de envío"
                    │
                    ▼
           LLM: "Perfecto Carlos, ¿a qué dirección enviamos?"
           proposed_action: update_lead_data
           extracted_data: {full_name: "Carlos Pérez"}

─────────────────────────────────────────────

Cliente: "Calle 10 #5-20, Manizales"
                    │
                    ▼
           Backend → GoalStrategy
           checkpoints completos: intent, product, lead_qualified, shipping
           checkpoint actual: order_created
           directiva: "Presenta resumen del pedido y pide confirmación"
                    │
                    ▼
           LLM: "Resumen: Café Molido Premium $25.000 + envío..."
           proposed_action: propose_order
           extracted_data: {items: [{product_id: "...", quantity: 1}]}
                    │
                    ▼
           Backend:
           ✓ propose_order permitida en estado selling
           ✓ precio tomado de products.price (NO del agente)
           ✓ ORDER creada con status=draft
           side_effects: ["order_created:uuid"]

─────────────────────────────────────────────

Cliente: "Sí, confirmo"
                    │
                    ▼
           Backend → GoalStrategy
           checkpoint actual: user_confirmed
                    │
                    ▼
           LLM: "¡Pedido confirmado! Te llegará en 2-3 días."
           proposed_action: confirm_order
           extracted_data: {user_confirmation: true, shipping_name: "Carlos Pérez", ...}
                    │
                    ▼
           Backend valida:
           ✓ confirm_order válida en estado ordering
           ✓ order tiene line_items
           ✓ tiene shipping_address
           ✓ user_confirmation = true
           ORDER: draft → confirmed
           side_effects: ["order_confirmed:uuid"]
```

---

## Los endpoints en detalle

### Autenticación

Todos los endpoints (excepto `/health`) requieren:

```
Authorization: Bearer <SALES_AI_SERVICE_TOKEN>
X-Client-ID: <UUID del cliente/tenant>
```

El token se valida con `hmac.compare_digest` para prevenir timing attacks. El `X-Client-ID` identifica el tenant — todas las queries filtran por este ID.

---

### GET /health

Verificar que el backend está corriendo. No requiere autenticación.

```bash
curl https://<CONTAINER_APP>.azurecontainerapps.io/health
```

```json
{"status": "ok"}
```

---

### POST /api/v1/ingest/message

**Llamada 1 del patrón de dos llamadas.** Procesa un mensaje entrante de WhatsApp.

#### Request

```bash
curl -X POST https://<CONTAINER_APP>.azurecontainerapps.io/api/v1/ingest/message \
  -H "Authorization: Bearer <TOKEN>" \
  -H "X-Client-ID: 00000000-0000-0000-0000-000000000001" \
  -H "Content-Type: application/json" \
  -d '{
    "chakra_message_id": "msg-whatsapp-abc123",
    "phone_number": "+573001234567",
    "content": "Hola, quiero comprar café",
    "display_name": "Carlos",
    "message_type": "text"
  }'
```

| Campo | Tipo | Descripción |
|---|---|---|
| `chakra_message_id` | string | ID único del mensaje en Chakra. Garantiza idempotencia — el mismo ID nunca se procesa dos veces. |
| `phone_number` | string | Número de WhatsApp del cliente. |
| `content` | string | Texto del mensaje. |
| `display_name` | string? | Nombre del cliente (opcional, se actualiza si ya existe). |
| `message_type` | string | `"text"` por defecto. |

#### Response

```json
{
  "should_respond": true,
  "conversation_id": "c420db40-1249-4fe7-8cdf-70805cb7759f",
  "conversation_state": "active",

  "strategy_directive": "CURRENT GOAL: close_sale\nPROGRESS: [░░░░░░░░░░] 0%\nCURRENT STEP: Intent identified\nYOUR TASK THIS TURN: Ask what the customer is looking for today.\nINFORMATION STILL NEEDED:\n  • intent\nRULES:\n- Focus on collecting the missing information listed above\n- Do NOT skip ahead to later steps\n- Do NOT ask for multiple pieces of information at once\n- NEVER invent or assume information the customer hasn't provided",

  "strategy_meta": {
    "goal": "close_sale",
    "progress_pct": 0,
    "current_checkpoint": "intent_identified",
    "next_action": "Ask what the customer is looking for today.",
    "missing_fields": ["intent"]
  },

  "strategy_version": 1,

  "available_actions": [
    "classify_intent",
    "ask_question",
    "search_products",
    "escalate"
  ],

  "client_config": {
    "system_prompt_template": "Eres un asistente de ventas para Café Demo...",
    "ai_model": "gpt-4o-mini",
    "ai_temperature": 0.3,
    "business_rules": {
      "currency": "COP",
      "default_goal": "close_sale",
      "shipping_cities": ["Manizales", "Pereira", "Armenia"]
    }
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

  "recent_messages": [
    {
      "id": "...",
      "direction": "inbound",
      "content": "Hola, quiero comprar café",
      "message_type": "text",
      "created_at": "2026-04-04T20:23:18Z"
    }
  ]
}
```

| Campo | Cómo usarlo |
|---|---|
| `should_respond` | Si es `false`, es un mensaje duplicado o el usuario está bloqueado. No llamar al LLM. |
| `strategy_directive` | **Inyectar directamente en el system prompt del LLM.** Es el texto que le dice al agente qué perseguir. |
| `available_actions` | Incluir en el system prompt como lista de acciones disponibles. |
| `strategy_version` | **Guardar y enviar en la Llamada 2.** Si la conversación cambia entre llamadas, el backend detecta la inconsistencia. |
| `client_config.system_prompt_template` | El prompt base del cliente. Concatenar con `strategy_directive` y `available_actions`. |
| `recent_messages` | Historial para el contexto del LLM. |

#### Cómo construir el system prompt para el LLM

```
{client_config.system_prompt_template}

---
{strategy_directive}

---
ACCIONES DISPONIBLES EN ESTE TURNO:
{available_actions}
Para proponer una acción, incluye en tu respuesta:
ACTION: <nombre_accion>
DATA: <json con datos extraídos>
TRANSITION: <nuevo_estado_si_corresponde>
```

#### Casos especiales

**Mensaje duplicado** (mismo `chakra_message_id`):
```json
{"should_respond": false, ...}
```

**Cliente inactivo:**
```
HTTP 404 — "Client not found or inactive"
```

**Usuario bloqueado:**
```
HTTP 403 — "User is blocked"
```

---

### POST /api/v1/agent/action

**Llamada 2 del patrón de dos llamadas.** Valida la respuesta del LLM y ejecuta acciones de negocio.

#### Request

```bash
curl -X POST https://<CONTAINER_APP>.azurecontainerapps.io/api/v1/agent/action \
  -H "Authorization: Bearer <TOKEN>" \
  -H "X-Client-ID: 00000000-0000-0000-0000-000000000001" \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "c420db40-1249-4fe7-8cdf-70805cb7759f",
    "strategy_version": 1,
    "response_text": "¡Hola Carlos! ¿Qué tipo de café te interesa hoy?",
    "proposed_action": "classify_intent",
    "proposed_transition": "qualifying",
    "extracted_data": {
      "intent": "comprar café molido"
    },
    "ai_model": "gpt-4o-mini",
    "prompt_tokens": 245,
    "completion_tokens": 38,
    "latency_ms": 820
  }'
```

| Campo | Tipo | Descripción |
|---|---|---|
| `conversation_id` | UUID | De la respuesta de Llamada 1. |
| `strategy_version` | int | **Obligatorio.** De la respuesta de Llamada 1. Detecta contextos desactualizados. |
| `response_text` | string | El texto que generó el LLM. Llega al usuario independientemente del resultado. |
| `proposed_action` | string? | Acción de negocio que propone el agente (ver lista abajo). |
| `proposed_transition` | string? | Nuevo estado de conversación que propone el agente. |
| `extracted_data` | dict? | Datos extraídos por el LLM de la conversación. |
| `ai_model` | string? | Modelo usado (para auditoría). |
| `prompt_tokens` | int? | Tokens del prompt (para control de costos). |
| `completion_tokens` | int? | Tokens de la respuesta (para control de costos). |
| `latency_ms` | int? | Latencia del LLM en ms (para monitoreo). |

#### Acciones de negocio disponibles

| Acción | Estado requerido | Qué hace |
|---|---|---|
| `classify_intent` | active | Informacional. Sin efecto en DB. |
| `ask_question` | cualquiera | Informacional. Sin efecto en DB. |
| `search_products` | active, selling | Informacional. Sin efecto en DB. |
| `create_lead` | qualifying | Crea un lead vinculado a la conversación. Requiere `intent` en `extracted_data`. |
| `update_lead_data` | qualifying | Merge de datos de calificación en el lead existente. |
| `propose_order` | selling | Crea un pedido DRAFT. Precios tomados del catálogo, nunca del agente. |
| `confirm_order` | ordering | Confirma el pedido. Requiere `shipping_address` + `user_confirmation: true`. |
| `cancel_order` | ordering | Cancela el pedido. Solo posible desde estado draft o confirmed. |
| `escalate` | cualquiera | Pasa a `human_handoff`. |

#### Response

```json
{
  "approved": true,
  "final_response_text": "¡Hola Carlos! ¿Qué tipo de café te interesa hoy?",
  "new_state": "qualifying",
  "side_effects": [
    "state_changed:active→qualifying"
  ],
  "rejection_reason": null
}
```

| Campo | Descripción |
|---|---|
| `approved` | `true` si la acción de negocio se ejecutó. `false` si fue rechazada por reglas. |
| `final_response_text` | El texto a enviar al usuario. **Siempre presente**, incluso si `approved = false`. |
| `new_state` | Estado actual de la conversación después de este turno. |
| `side_effects` | Lista de efectos: `lead_created:uuid`, `order_created:uuid`, `state_changed:X→Y`, etc. |
| `rejection_reason` | Si `approved = false`, explica por qué. El usuario no necesita saberlo. |

#### Casos especiales

**Contexto desactualizado** (otro mensaje llegó entre Llamada 1 y Llamada 2):
```
HTTP 409 — {"error": "stale_context", "message": "strategy_version mismatch"}
```
Solución: volver a llamar a Llamada 1 con el mismo mensaje para obtener el contexto actualizado.

**Acción rechazada pero respuesta enviada:**
```json
{
  "approved": false,
  "final_response_text": "¡Por supuesto, aquí está tu pedido!",
  "new_state": "active",
  "side_effects": [],
  "rejection_reason": "Action 'propose_order' is not allowed in state 'active'"
}
```
El texto del LLM llega al usuario. El pedido NO se creó. La conversación continúa.

---

## Ejemplo concreto: vender un café

A continuación el ciclo completo de una venta usando `curl`. Sirve para entender exactamente qué hace el backend en cada paso.

### Paso 1 — Cliente dice "Hola"

```bash
# LLAMADA 1
curl -X POST .../api/v1/ingest/message \
  -H "Authorization: Bearer <TOKEN>" \
  -H "X-Client-ID: 00000000-0000-0000-0000-000000000001" \
  -d '{
    "chakra_message_id": "wa-001",
    "phone_number": "+573001234567",
    "content": "Hola, quiero café"
  }'

# Backend responde:
# strategy_directive: "Tu tarea: pregunta qué está buscando"
# available_actions: [classify_intent, ask_question, search_products, escalate]
# strategy_version: 1

# LLM genera: "¡Hola! ¿Qué tipo de café te interesa?"
# proposed_action: classify_intent
# extracted_data: {}

# LLAMADA 2
curl -X POST .../api/v1/agent/action \
  -d '{
    "conversation_id": "<uuid>",
    "strategy_version": 1,
    "response_text": "¡Hola! ¿Qué tipo de café te interesa?",
    "proposed_action": "classify_intent",
    "proposed_transition": "qualifying",
    "extracted_data": {}
  }'

# Backend responde:
# approved: true, new_state: "qualifying"
# side_effects: ["state_changed:active→qualifying"]
```

### Paso 2 — Cliente dice el producto

```bash
# LLAMADA 1 — nuevo mensaje
curl -X POST .../api/v1/ingest/message \
  -d '{
    "chakra_message_id": "wa-002",
    "phone_number": "+573001234567",
    "content": "El café molido premium"
  }'

# Backend ahora sabe: estamos en qualifying, falta el nombre
# strategy_directive: "Tu tarea: pregunta el nombre completo"
# strategy_version: 2

# LLM genera: "¡Excelente! ¿Me das tu nombre completo?"
# proposed_action: update_lead_data
# extracted_data: {intent: "café molido", product_id: "91916748-..."}

# LLAMADA 2
curl -X POST .../api/v1/agent/action \
  -d '{
    "strategy_version": 2,
    "response_text": "¡Excelente! ¿Me das tu nombre completo?",
    "proposed_action": "update_lead_data",
    "extracted_data": {"intent": "café molido", "product_id": "91916748-7827-45a5-b9fb-f46d50c19be7"}
  }'
```

### Paso 3 — Cliente da nombre y dirección

```bash
# (mensajes wa-003, wa-004 similares — backend acumula datos en extracted_context)
# Cuando tiene: intent, product_id, full_name, shipping_address, shipping_city
# strategy_directive: "Tu tarea: presenta el resumen del pedido"
# available_actions ahora incluye: propose_order (estamos en selling)
```

### Paso 4 — Crear el pedido

```bash
# LLAMADA 2 con acción de negocio real
curl -X POST .../api/v1/agent/action \
  -d '{
    "strategy_version": 5,
    "response_text": "Tu pedido: Café Molido Premium × 1 = $25.000 COP. ¿Confirmamos?",
    "proposed_action": "propose_order",
    "proposed_transition": "ordering",
    "extracted_data": {
      "items": [
        {"product_id": "91916748-7827-45a5-b9fb-f46d50c19be7", "quantity": 1}
      ]
    }
  }'

# Backend:
# 1. Verifica que propose_order está permitida en estado "selling" ✓
# 2. Busca el producto en la tabla products para Café Demo ✓
# 3. Toma el precio de la DB ($25.000) — ignora cualquier precio del agente
# 4. Crea ORDER con status=draft + ORDER_LINE_ITEMS
# 5. Retorna:
# {
#   "approved": true,
#   "new_state": "ordering",
#   "side_effects": ["order_created:uuid-del-pedido", "state_changed:selling→ordering"]
# }
```

### Paso 5 — Cliente confirma

```bash
# LLAMADA 2 final
curl -X POST .../api/v1/agent/action \
  -d '{
    "strategy_version": 6,
    "response_text": "¡Pedido confirmado! Llega en 2-3 días hábiles.",
    "proposed_action": "confirm_order",
    "extracted_data": {
      "user_confirmation": true,
      "shipping_name": "Carlos Pérez",
      "shipping_address": "Calle 10 #5-20",
      "shipping_city": "Manizales"
    }
  }'

# Backend verifica:
# ✓ confirm_order permitida en estado "ordering"
# ✓ order tiene line_items
# ✓ tiene shipping_address
# ✓ user_confirmation = true
# ORDER: draft → confirmed
# {
#   "approved": true,
#   "new_state": "ordering",
#   "side_effects": ["order_confirmed:uuid-del-pedido"]
# }
```

---

## El GoalStrategyEngine explicado

El engine es una función pura: `(goal, datos_recolectados, reglas_negocio) → directiva`. Sin base de datos, sin LLM, sin red. Corre en microsegundos.

### El DAG del goal `close_sale`

```
intent_identified ──────────────────────────┐
        │                                    │
        ▼                                    ▼
product_matched                      lead_qualified
        │                                    │
        │                                    ▼
        │                         shipping_info_collected
        │                                    │
        └────────────────────────────────────┘
                                             │
                                             ▼
                                      order_created
                                             │
                                             ▼
                                      user_confirmed
```

Cada checkpoint tiene `required_fields` y `blocked_by`. El engine evalúa cuáles están completos, cuáles están bloqueados por dependencias, y cuál es el primero accionable.

### Ejemplo de directiva generada

```
CURRENT GOAL: close_sale
PROGRESS: [████░░░░░░] 33%
CURRENT STEP: Lead qualified
YOUR TASK THIS TURN: Ask for the customer's full name naturally.
INFORMATION STILL NEEDED:
  • full_name
  • shipping_address
  • shipping_city
COMPLETED:
  ✓ intent_identified
  ✓ product_matched
RULES:
- Focus on collecting the missing information listed above
- Do NOT skip ahead to later steps
- Do NOT ask for multiple pieces of information at once
- NEVER invent or assume information the customer hasn't provided
```

### Reglas de negocio que modifican el DAG

En la tabla `clients.business_rules` (JSONB) se configuran por cliente:

| Regla | Efecto |
|---|---|
| `"skip_lead_qualification": true` | Elimina el checkpoint `lead_qualified`. Útil para ventas rápidas. |
| `"require_id_number": true` | Agrega `identification_number` como campo requerido en lead. |
| `"require_email": true` | Agrega `email` como campo requerido. |

---

## La máquina de estados

```
                    ┌─────────┐
                    │  IDLE   │
                    └────┬────┘
                         │ mensaje entrante
                         ▼
                    ┌─────────┐
              ┌────►│ ACTIVE  │◄────────────────────┐
              │     └────┬────┘                     │
              │          │                          │
         ┌────┘     ┌────┼────┐                     │
         │          ▼    ▼    ▼                     │
         │    ┌──────┐ ┌──────┐ ┌──────────────┐   │
         │    │QUALIF│ │SELL. │ │HUMAN_HANDOFF │   │
         │    └──┬───┘ └──┬───┘ └──────────────┘   │
         │       │        │                         │
         │       ▼        ▼                         │
         │    ┌──────────────┐                      │
         └────│   ORDERING   │──────────────────────┘
              └──────┬───────┘
                     │ confirm/cancel
                     ▼
                ┌─────────┐
                │ CLOSED  │
                └─────────┘
```

| Estado | Acciones disponibles |
|---|---|
| `idle` | greet, classify_intent |
| `active` | classify_intent, ask_question, search_products, escalate |
| `qualifying` | ask_question, create_lead, update_lead_data, escalate |
| `selling` | search_products, present_product, propose_order, ask_question, escalate |
| `ordering` | collect_shipping_info, confirm_order, modify_order, cancel_order, escalate |
| `human_handoff` | notify_human |
| `closed` | (ninguna) |

---

## Decisiones técnicas

### ¿Por qué async everywhere?

WhatsApp puede enviar mensajes en ráfagas rápidas. Un mensaje nuevo puede llegar mientras se procesa el anterior. La combinación de async SQLAlchemy + asyncpg permite atender múltiples requests concurrentes sin bloquear el event loop. El advisory lock de PostgreSQL (`pg_advisory_xact_lock`) serializa los mensajes de la misma conversación sin necesidad de colas.

### ¿Por qué PostgreSQL advisory locks en vez de Redis?

El advisory lock es transaccional — se libera automáticamente cuando la transacción hace commit o rollback. No hay que preocuparse por locks huérfanos si la aplicación falla. Y no agrega infraestructura extra (ya tenemos Postgres).

### ¿Por qué enums como VARCHAR + CHECK en vez de tipos nativos?

PostgreSQL tiene un tipo `ENUM` nativo, pero para agregar un valor nuevo hay que correr `ALTER TYPE` que puede bloquear la tabla. Con `VARCHAR + CHECK CONSTRAINT`, agregar un estado es solo `ALTER TABLE ... DROP CONSTRAINT ... ADD CONSTRAINT ...` — no bloquea, no requiere downtime.

### ¿Por qué el agente no tiene herramientas (tools)?

El patrón de tool-calling de LLMs crea loops impredecibles: el agente llama herramienta A, interpreta el resultado, llama herramienta B, etc. Cada llamada agrega latencia y costo. Nuestro patrón es lineal y determinista: exactamente 1 LLM call por mensaje, exactamente 2 HTTP calls al backend. El costo es constante y predecible.

### ¿Por qué `strategy_version`?

Entre la Llamada 1 (ingest) y la Llamada 2 (agent/action), puede ocurrir algo inesperado: el cliente envía otro mensaje, n8n hace un retry, hay un timeout. La `strategy_version` es un número entero que incrementa en cada ingest. Si entre las dos llamadas llegó otro mensaje y la versión cambió, el backend detecta la inconsistencia (HTTP 409) y n8n puede volver a llamar a ingest para obtener el contexto actualizado.

### ¿Por qué los precios vienen del backend y no del agente?

El LLM puede alucinar precios. Si el agente propone "crear pedido con precio $5.000" para un producto que vale $25.000, el backend ignora el precio del agente y busca el producto en la tabla `products`. La prevención no está en el prompt — está en el código.

### Multi-tenant con `client_id`

Cada tabla tiene `client_id` como FK no nulable. Cada query filtra por `client_id`. Esto garantiza que los datos de un cliente nunca se mezclen con los de otro, incluso en el mismo pool de conexiones.

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
│       │   └── database.py       # Async SQLAlchemy, resolución de credenciales
│       │
│       ├── models/
│       │   └── core.py           # ORM: 9 tablas mapeadas al schema desplegado
│       │
│       ├── api/v1/
│       │   ├── ingest.py         # POST /api/v1/ingest/message
│       │   └── agent.py          # POST /api/v1/agent/action
│       │
│       └── services/
│           ├── state_machine.py  # 7 estados, validadores, excepciones tipadas
│           ├── goal_strategy.py  # GoalStrategyEngine: DAG navigator
│           ├── ingest.py         # Servicio de ingesta (10 pasos, 1 transacción)
│           └── agent_action.py   # Validación y ejecución de acciones
│
├── migrations/versions/
│   ├── 001_initial_schema.sql    # Schema completo (ya desplegado)
│   └── 002_peer_review_hardening.sql # Columnas de strategy + triggers
│
└── tests/
    ├── test_health.py            # Tests de auth y health (5 tests)
    └── services/
        └── test_goal_strategy.py # Tests del engine (18 tests, pure Python)
```

---

## Cómo correr el proyecto localmente

### Prerrequisitos

- Python 3.12
- PostgreSQL 16 (o conexión a Azure)

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

O con Azure Key Vault:

```dotenv
KEY_VAULT_URL=https://<KEY_VAULT_NAME>.vault.azure.net/
AZURE_CLIENT_ID=<client-id-de-la-managed-identity>
SALES_AI_SERVICE_TOKEN=<token>
ENV=dev
```

### Correr tests

```bash
# Desde agent-backend/
pytest tests/ -v
```

Todos los tests de servicios son pure Python — no necesitan base de datos ni red.

### Correr la aplicación

```bash
cd sales_agent_api
uvicorn app.main:app --reload --port 8000
```

Documentación interactiva en: http://localhost:8000/api/docs

### Docker

```bash
# Build
docker build -t sales-agent-api -f sales_agent_api/Dockerfile sales_agent_api

# Run
docker run -p 8000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/sales_ai" \
  -e SALES_AI_SERVICE_TOKEN="mi-token" \
  -e ENV="dev" \
  sales-agent-api
```

### CI/CD

Cada push a `main` ejecuta automáticamente:
1. Tests (23 tests, ~1 segundo)
2. Build y push de imagen Docker a Docker Hub
3. Actualización del Container App en Azure (`ca-backend`, `rg-backend`)

---

## Entorno de producción

| Componente | Detalles |
|---|---|
| **Backend URL** | `https://<CONTAINER_APP>.azurecontainerapps.io` |
| **Base de datos** | `<POSTGRES_HOST>.postgres.database.azure.com` / `sales_ai` |
| **Key Vault** | `<KEY_VAULT_NAME>` (DBUSERNAME, DBPASSWORD, DBHOST, DBNAME, sales-ai-service-token) |
| **Managed Identity** | `<MANAGED_IDENTITY_NAME>` — accede a Key Vault y ACR sin contraseñas en el código |
| **CI/CD** | GitHub Actions → Docker Hub → Container App |
| **Cliente demo** | Café Demo — ID `00000000-0000-0000-0000-000000000001` |
