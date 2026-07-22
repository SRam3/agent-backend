# Arquitectura — Overview

Este documento responde a la pregunta **"¿cómo está pensado este sistema?"**. Está hecho **con muchos diagramas a propósito**: cada uno cuenta un pedazo de la historia (quién habla con quién, cómo viaja un mensaje, cómo se recolectan los datos, cómo se decide). Para el estado actual operacional, leer `../../CLAUDE.md`. Para el por qué de cada decisión, leer `../decisions/`.

---

## La idea fundamental

> **El LLM conversa, el backend gobierna.**

La mayoría de los chatbots de WhatsApp delegan al LLM no solo la generación de lenguaje sino también las decisiones de negocio: cuándo crear un lead, cuándo proponer un producto, cuándo cerrar una venta. Eso funciona hasta que no funciona — el LLM es probabilístico y las reglas de negocio no lo son.

Sales AI Agent invierte esa relación. El LLM solo entiende y produce lenguaje natural. Todas las decisiones — qué pedir, cuándo escalar, qué dato es admisible — viven en código Python testeable. El LLM **propone**; el backend **valida y ejecuta**.

---

## El sistema de un vistazo (quién habla con quién)

Antes de entrar en detalle, este es el mapa completo. Cada flecha es una comunicación real entre piezas:

```mermaid
flowchart LR
    Cliente(["📱 Cliente<br/>WhatsApp"])
    Chakra["Chakra HQ<br/>(API de WhatsApp)"]
    LLM["🧠 OpenAI<br/>gpt-4o-mini"]
    DB[("🗄️ PostgreSQL<br/>ground truth")]
    KV["🔐 Key Vault<br/>secretos"]
    Operador(["🧑‍💼 Operador<br/>Telegram"])

    subgraph Azure["☁️ Azure Container Apps (cada servicio siempre encendido)"]
        n8n["n8n<br/>orquestador del turno"]
        Backend["Backend FastAPI<br/>el que gobierna"]
    end

    Cliente <-->|mensajes| Chakra
    Chakra -->|webhook entrante| n8n
    n8n -->|respuesta| Chakra
    n8n <-->|2 llamadas por turno| Backend
    n8n <-->|1 llamada por turno| LLM
    Backend <-->|lee/escribe| DB
    Backend -.->|carga secretos| KV
    n8n -->|aviso + botón| Operador
    Operador -->|confirma pago| n8n
```

**Cómo leerlo**: el cliente nunca habla directo con nuestro sistema — Chakra HQ es el intermediario con WhatsApp. **n8n** es el director de orquesta (no piensa, coordina). El **backend** es el cerebro con las reglas. El **operador humano** entra por Telegram cuando hay que confirmar un pago o rescatar una conversación.

> **Nota de resiliencia**: n8n y el backend son dos servicios **separados** en Azure Container Apps, y **ambos** deben estar siempre encendidos. Si cualquiera de los dos se apaga por inactividad, un mensaje que llega mientras arranca en frío se pierde (ver `postmortems/n8n-scale-to-zero-2026-07-21.md`).

---

## Las tres capas

```mermaid
graph TB
    subgraph C1["Capa 1 — Lenguaje"]
        LLM["LLM · gpt-4o-mini<br/>NLU + NLG · sin tools · sin loops · sin estado<br/>─────────────────────────────────────<br/>Lee: texto del cliente · system prompt · directiva<br/>Escribe: response_text · extracted_data<br/>Decide: nada de la venta"]
    end
    subgraph C2["Capa 2 — Política"]
        POL["GoalStrategyEngine · DAG gates · state machine<br/>─────────────────────────────────────<br/>Lee: extracted_context · business_rules<br/>Escribe: directiva del turno · accept/reject por slot<br/>Decide: orden de pedidos · cuándo escalar · qué admisible"]
    end
    subgraph C3["Capa 3 — Dominio"]
        DB["PostgreSQL<br/>clients · client_users · conversations · messages<br/>─────────────────────────────────────<br/>Source of truth · tenant config · perfil · historial<br/>Decide: nada — es ground truth"]
    end
    C2 -- directiva del turno --> C1
    C1 -- extracted_data propuesto --> C2
    C3 -- load context --> C2
    C2 -- persist accepted slots --> C3
```

**Propiedad clave**: el LLM es la pieza más reemplazable del sistema. Si mañana sale un modelo mejor, se cambia el string del modelo en `clients.ai_model`. Los datos recolectados viven en Postgres, no en el contexto del modelo.

---

## El viaje de un mensaje (n8n orquesta)

Cuando llega un webhook, primero el workflow `master` decide **de qué negocio** es el mensaje (por el número de WhatsApp que lo recibió) y lo enruta al workflow de ese cliente:

```mermaid
flowchart LR
    WH["📥 Webhook de Chakra"] --> SW{"¿Qué número<br/>de negocio?"}
    SW -->|Café Arenillo| CA["workflow<br/>cafe_arenillo_v2"]
    SW -->|max_natural| MN["workflow<br/>max_natural"]
    SW -->|desconocido| X(["⚠️ sin ruta<br/>(no responde)"])
```

Dentro del workflow del cliente ocurre toda la lógica del turno. Fíjate en los tres **cortes** (rombos) donde el sistema puede decidir **no** responder — eso es deliberado, es "el backend gobierna" en acción:

```mermaid
flowchart TD
    START(["Mensaje ya enrutado<br/>al cliente"]) --> M1{"¿Trae texto?"}
    M1 -->|"no · es un status"| STOP1(["⏹️ Stop"])
    M1 -->|sí| ING["① POST /ingest/message<br/>el backend arma el contexto"]
    ING --> SR{"¿should_respond?"}
    SR -->|"no · handoff o closed"| STOP2(["⏹️ Stop — no interrumpe<br/>al humano"])
    SR -->|sí| LLMCALL["🧠 Llama al LLM<br/>1 vez, sin tools"]
    LLMCALL --> ACT["② POST /agent/action<br/>el backend valida"]
    ACT --> AP{"¿approved?"}
    AP -->|sí| SEND["📤 Responde por WhatsApp"]
    AP -->|"no · suprimido"| ESC{"¿Escalado?"}
    SEND --> IMG{"¿Manda foto<br/>del producto?"}
    IMG -->|sí| SIMG["🖼️ Envía imagen"]
    IMG -->|no| ESC
    SIMG --> ESC
    ESC -->|sí| OWN["Avisa al dueño"]
    ESC -->|no| NOT{"¿Avisar<br/>al operador?"}
    OWN --> NOT
    NOT -->|venta lista| TGB["📲 Telegram + botón<br/>Confirmar pago"]
    NOT -->|loop detectado| TGS["📲 Telegram · aviso simple"]
    NOT -->|no| ENDN(["fin del turno"])
```

**La lección de los rombos**: un chatbot ingenuo responde a *todo*. Este sistema tiene tres puntos donde frena a propósito — no es texto, el humano ya tomó la conversación, o el backend rechazó la respuesta. Callar en el momento correcto es tan importante como responder bien.

---

## El patrón de dos llamadas

Cada turno conversacional consta de exactamente 2 HTTP calls de n8n al backend, con 1 llamada al LLM en medio:

```mermaid
sequenceDiagram
    participant W as WhatsApp
    participant N as n8n
    participant B as Backend FastAPI
    participant L as LLM (gpt-4o-mini)
    participant DB as PostgreSQL

    W->>N: Webhook Chakra HQ · mensaje del cliente

    N->>B: POST /api/v1/ingest/message
    B->>DB: Validar client · idempotencia · bloqueo
    B->>DB: Upsert client_user · find/create conversación (24h)
    B->>DB: Advisory lock por conversation_id · persistir inbound
    Note over B: Debounce 5s — espera ráfagas
    B->>B: GoalStrategyEngine → directive
    B->>DB: Bump strategy_version · persistir snapshot
    B-->>N: directive · strategy_version · business_context · recent_messages

    N->>L: system prompt + directive + historial (1 sola llamada, sin tools)
    L-->>N: response_text + extracted_data

    N->>B: POST /api/v1/agent/action
    B->>B: Verificar strategy_version (409 si stale)
    B->>B: DAG gates → merge extracted_data
    B->>DB: Sync stable facts → client_users.profile
    B->>DB: Auto-escalate si all_complete · persistir outbound + audit_log
    B-->>N: approved · final_response_text · side_effects

    N->>W: final_response_text via Chakra HQ
```

**Sin loops, sin tool-calling, sin rama dinámica del LLM.** El backend sabe todo lo que el LLM necesita saber antes de la llamada, y valida todo lo que el LLM produce después de la llamada.

---

## El DAG de close_sale

```mermaid
graph TD
    A["📦 product_matched<br/>product_id"]
    B["👤 lead_qualified<br/>full_name · phone"]
    C["🏠 shipping_info_collected<br/>shipping_address · shipping_city"]
    D["✅ user_confirmed<br/>user_confirmation<br/>⚡ gate: requiere los 4 anteriores"]
    E["💳 payment_confirmed<br/>payment_confirmation<br/>⚡ gate: requiere user_confirmed + phone + addr"]
    F(["🤝 auto-escalate → human_handoff"])

    A --> B --> C --> D --> E --> F
```

Cada checkpoint del DAG representa **suficiencia de información**, no una acción del bot. La diferencia es importante: "el bot dijo X" no es lo mismo que "el sistema sabe X". El DAG opera sobre lo segundo.

**Los DAG gates** son la innovación operacional que distingue este sistema de los DAGs académicos (PPDPP, ChatSOP). Cuando el LLM propone un slot prematuramente — por ejemplo, marcar `user_confirmation=true` antes de tener dirección — el gate lo rechaza, registra un warning, pero **la respuesta del LLM al usuario igual se envía**. La conversación nunca se rompe; solo no se persiste el dato prematuro. Esa propiedad de fail-safe es lo que vuelve confiable el sistema.

---

## Cómo se recolectan los datos

Este es el corazón de "el backend gobierna". Cada dato que el cliente menciona hace este viaje antes de considerarse real:

```mermaid
flowchart LR
    MSG["🗣️ Cliente:<br/>'soy Juan Pérez,<br/>en Manizales'"] --> LLM["🧠 El LLM extrae<br/>extracted_data:<br/>full_name, shipping_city"]
    LLM --> GATE{"🚦 DAG gate<br/>¿es admisible<br/>ahora y válido?"}
    GATE -->|"rechazado<br/>(prematuro / inválido)"| WARN["⚠️ warning<br/>no se guarda —<br/>pero igual responde"]
    GATE -->|aceptado| CTX["🛒 extracted_context<br/>carrito de esta<br/>conversación · 24h"]
    CTX -->|"dato estable<br/>(nombre, dirección…)"| PROF["👤 client_users.profile<br/>perfil permanente<br/>entre conversaciones"]
```

La idea clave: **el LLM propone, el gate filtra, y solo lo aceptado se persiste**. Un teléfono con formato basura o una confirmación de pago sin dirección nunca entran a la base de datos, aunque el LLM los haya "creído".

Una conversación tiene entonces tres tipos de información distintos, cada uno con su lugar:

| Tipo | Descripción | Dónde vive | Persistencia |
|------|-------------|------------|--------------|
| **Datos del cliente** | Nombre, dirección, teléfono, preferencias | `client_users.profile` (JSONB) | Multi-conversación (siempre) |
| **Estado de venta** | Producto elegido, cantidad, confirmaciones | `conversations.extracted_context` (JSONB) | Intra-conversación (24h) |
| **Contexto efímero** | Inferencias del último turno | Transitorio en memoria del LLM | Por turno |

> **Limitación conocida**: si un cliente abandona la venta a mitad del flujo y vuelve días después, el estado de venta se pierde porque vive en `extracted_context` de la conversación expirada. El perfil persiste pero el carrito no. Resolverlo requiere una entidad nueva (`purchase_intents`) — pendiente en roadmap.

---

## El modelo de datos (el grafo de tablas)

Seis tablas, todas colgando del tenant (`clients`). Así se relacionan:

```mermaid
erDiagram
    clients      ||--o{ client_users  : "tiene clientes"
    clients      ||--o{ products      : "vende"
    clients      ||--o{ conversations : "atiende"
    client_users ||--o{ conversations : "participa en"
    conversations ||--o{ messages     : "contiene"
    clients      ||--o{ audit_log     : "registra eventos"

    clients {
        uuid id PK
        string name
        jsonb business_rules "config por cliente"
        text system_prompt_template "la voz del negocio"
    }
    client_users {
        uuid id PK
        uuid client_id FK
        string phone_number
        jsonb profile "datos permanentes del cliente"
    }
    products {
        uuid id PK
        uuid client_id FK
        string name
        int price
    }
    conversations {
        uuid id PK
        uuid client_id FK
        string state "active / human_handoff / closed"
        jsonb extracted_context "el carrito de 24h"
        int strategy_version "anti-staleness"
    }
    messages {
        uuid id PK
        uuid conversation_id FK
        string direction "inbound / outbound"
        string chakra_message_id "UNIQUE · idempotencia"
    }
    audit_log {
        uuid id PK
        uuid client_id FK
        string event "append-only"
    }
```

**Regla de oro del schema**: toda tabla que ve datos de un tenant tiene `client_id NOT NULL`, y toda query filtra por él. Los dos JSONB (`profile` y `extracted_context`) son donde vive casi toda la "memoria" del sistema.

---

## La máquina de estados de la conversación

Una conversación solo puede estar en uno de **tres** estados. Este grafo manda sobre si el bot habla o se calla:

```mermaid
stateDiagram-v2
    [*] --> active: llega el primer mensaje
    active --> human_handoff: DAG completo, o loop detectado
    human_handoff --> active: el cliente sigue y el operador no cerró
    active --> closed: operador confirma pago
    human_handoff --> closed: operador confirma pago
    closed --> [*]

    note right of active
        El bot responde normalmente
    end note
    note right of human_handoff
        El bot se calla —
        manda un humano
    end note
    note right of closed
        Terminal. Si el cliente
        vuelve a escribir, nace
        una conversación nueva
        en active
    end note
```

Toda conversación nace en `active`. Se auto-escala a `human_handoff` por dos vías: (1) el DAG de venta se completó (cliente confirmó el pedido), o (2) el **circuit breaker** detectó un loop (el bot repitió el mismo mensaje 3 veces). A `closed` solo se llega cuando el **operador humano** confirma que el pago es real.

---

## El lazo del operador humano (ADR-009)

El sistema no cierra ventas solo — un humano confirma el pago. Ese ida y vuelta ocurre por Telegram, sin que nadie tenga que abrir un panel:

```mermaid
sequenceDiagram
    participant B as Backend
    participant N as n8n
    participant T as Telegram
    participant Op as 🧑‍💼 Operador

    Note over B: El cliente confirmó el pedido<br/>(checkpoint user_confirmed)
    B-->>N: side_effect checkpoint_completed:user_confirmed
    N->>T: Aviso "venta lista" + botón [Confirmar pago]
    Op->>T: pulsa el botón
    T->>N: callback_query · confirm:conversation_id
    N->>N: Valida que quien pulsa sea el operador (fail-closed)
    N->>B: POST /api/v1/operator/confirm-payment
    B->>B: marca payment_confirmation · registra la venta · cierra
    B-->>N: 200 confirmed (idempotente)
    N->>T: "Pago confirmado ✅"
```

Dos detalles de seguridad: el endpoint del operador usa **su propio token** (el token de servicio no lo abre, y viceversa), y n8n valida que el `chat_id` de quien pulsa el botón sea el del operador autorizado — si no, no hace nada (*fail-closed*).

---

## Concurrencia y consistencia

**Problema**: WhatsApp manda ráfagas. Un cliente que escribe "hola" + "quiero comprar" + "café molido" en 3 segundos genera 3 webhooks paralelos. Si los procesamos concurrentemente, el LLM puede ver contextos inconsistentes.

```mermaid
sequenceDiagram
    participant C as Cliente
    participant B as Backend
    C->>B: "hola"  (t=0s)
    C->>B: "quiero comprar"  (t=2s)
    C->>B: "café molido"  (t=3s)
    Note over B: cada mensaje espera 5s (debounce);<br/>los dos primeros ceden el turno
    B-->>C: 1 sola respuesta que ya vio los 3 mensajes
```

**Soluciones aplicadas**:

1. **Advisory lock** por `conversation_id`: `pg_advisory_xact_lock(hash(conv_id))` serializa los mensajes de la misma conversación dentro de la transacción. Se libera automáticamente con commit/rollback.
2. **Debounce de 5 segundos**: tras commitear el primer mensaje, el backend espera 5s y verifica si llegó otro. Si sí, este turno no responde — deja que el siguiente mensaje (que ahora ve los anteriores) genere la respuesta.
3. **Idempotencia por `chakra_message_id`**: webhook que llega dos veces con el mismo ID se reconoce como duplicado.
4. **strategy_version**: protege contra que `/agent/action` opere sobre contexto que cambió entre las dos llamadas (ver ADR-003).

> **Limitación conocida**: el `asyncio.sleep(5)` durante la transacción mantiene el connection pool ocupado. Bajo carga sostenida puede saturar el pool. Es deuda técnica priorizada.

---

## Multi-tenancy

Cada tabla tenant-facing tiene `client_id UUID NOT NULL FK` que apunta a `clients`. Toda query filtra por `client_id`. La separación entre tenants es por convención en la capa de servicio — **no por Row-Level Security de Postgres**. Un bug en una query que olvide el filtro puede leer datos cross-tenant.

Esto es deuda técnica reconocida que se va a tomar al tercer cliente productivo. Para MVP con un solo cliente, está dentro del riesgo aceptado.

**Customización por cliente** vive en `clients.business_rules` (JSONB):
- `default_goal`: qué goal del DAG se activa por defecto
- `skip_lead_qualification`: omite checkpoint
- `require_id_number`: agrega campo requerido
- `currency`, `shipping_cities`, `shipping_rules`, `payment_methods`, `discount_rules`
- `agent_persona`: nombre del agente, rol

El `system_prompt_template` también es por cliente — cada negocio tiene su propia voz, persona y reglas de tono.

---

## Ubicación en el espacio de arquitecturas

Para ubicar esto frente a otros sistemas conversacionales:

```mermaid
quadrantChart
    title Espacio de arquitecturas conversacionales
    x-axis Baja especificidad de dominio --> Alta especificidad de dominio
    y-axis Baja autonomía del LLM --> Alta autonomía del LLM
    Agentes ReAct / AutoGPT: [0.18, 0.92]
    Bland AI / ElevenLabs Conversational: [0.50, 0.72]
    Sales AI Agent ★ tú estás aquí: [0.85, 0.48]
    Dialogflow / Watson / Rasa NLU: [0.82, 0.18]
```

**Lo que tenemos NO es un agente clásico** (estilo ReAct/AutoGPT). En la literatura más cercana, este patrón se conoce como **information-state dialogue manager** (Larsson & Traum, 2000) — un manejador de diálogo cuyo estado es la información acumulada — modernizado con un LLM haciendo NLU/NLG. Comercialmente, el primo más cercano es **Rasa CALM**.

---

## Filosofía de diseño

Tres principios que se aplican consistentemente:

1. **Fail-safe sobre fail-fast en la conversación.** Si una validación rechaza un slot del LLM, la respuesta al usuario igual se envía. La experiencia del cliente final no se interrumpe nunca por una decisión interna del backend.

2. **Schema-first, then code.** Cada cambio significativo empieza por entender qué datos cambian, en qué tabla, con qué constraints. El código viene después. El `CLAUDE.md` y los ADRs reflejan esta práctica: el diseño se piensa antes de implementarse.

3. **Determinístico donde se puede, probabilístico donde tiene que ser.** Lo que el negocio necesita controlar (orden, validación, persistencia) vive en código testeable. Lo que el cliente final percibe (tono, naturalidad) vive en el prompt y el modelo. La frontera es deliberada.
