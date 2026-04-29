# Glosario

Términos y conceptos del dominio Sales AI Agent. Cuando aparece un término en código, prompt, ADR o discusión, esta es la definición canónica.

---

## Términos del modelo de datos

**client**
Tenant. Un negocio que usa la plataforma Sales AI Agent. Cada `client` tiene su propia configuración (`system_prompt_template`, `business_rules`, `ai_model`), su propio catálogo de productos, sus propios usuarios finales. Ejemplo en producción hoy: Café Arenillo (UUID de seed data en migración 001).

**client_user**
End customer. La persona que escribe por WhatsApp al negocio del cliente. Identificado únicamente por la combinación `(client_id, phone_number)` — la misma persona puede ser cliente de dos negocios distintos en la plataforma sin colisión.

**conversation**
Sesión de chat entre un `client_user` y el bot. Tiene una ventana de validez de 24 horas: si pasaron 24h sin mensajes, una nueva conversación se crea. Si pasaron 30+ minutos sin mensajes pero menos de 24h, se mantiene la conversación pero se resetea `extracted_context` (decisión defensiva contra contexto contaminado de hace mucho).

**message**
Cada interacción individual. `direction` puede ser `inbound` (cliente → bot) u `outbound` (bot → cliente). Tiene `chakra_message_id` único para idempotencia.

**product**
Item del catálogo del cliente. Tiene precio que el LLM **nunca puede modificar** — el precio que el bot menciona en respuestas viene del prompt (que el backend arma con el catálogo), pero ninguna mutación de DB usa precios propuestos por el LLM.

---

## Términos del dominio conversacional

**extracted_context**
Campo JSONB en `conversations`. Contiene los slots de datos recolectados durante la conversación actual. Estructura típica:
```json
{
  "product_id": "<uuid>",
  "full_name": "Juan Pérez",
  "phone": "3001234567",
  "shipping_city": "Manizales",
  "shipping_address": "Calle 10 #5-20",
  "user_confirmation": true,
  "payment_confirmation": true
}
```
Vive durante la conversación; se pierde si la conversación expira (limitación conocida).

**profile**
Campo JSONB en `client_users`. Contiene datos persistentes del cliente entre conversaciones: nombre, dirección, ciudad, preferencias, historial de compras. Se sincroniza desde `extracted_context` cuando se aceptan slots estables. Ver ADR-005.

**directive (strategy_directive)**
Bloque de texto generado por el `GoalStrategyEngine` que se inyecta en el system prompt del LLM. Contiene: progreso del DAG, próximo dato a recolectar, hint conversacional. No es una orden — es una guía suave que el LLM debe respetar pero sin sacrificar la conversación natural.

**strategy_version**
Entero monotónicamente creciente en `conversations`. Se incrementa en cada `/ingest/message`. Sirve para detectar contexto viejo entre las dos llamadas del turno. Ver ADR-003.

**DAG gate**
Validación previa al merge de un slot en `extracted_context`. Implementada en `agent_action.py`. Verifica precondiciones de orden lógico (no se puede confirmar pago sin haber confirmado pedido, no se puede confirmar pedido sin tener nombre+teléfono+dirección+ciudad). Si rechaza, el slot no se persiste pero la conversación continúa.

**side_effects**
Lista de strings descriptivos que `/agent/action` devuelve a n8n. Permiten que el orquestador conozca consecuencias de la transacción sin tener que parsear estado. Ejemplos: `context_updated:['full_name','phone']`, `escalated:purchase_data_complete`, `warning:premature_summary_missing_phone+shipping_city`.

---

## Términos del flujo

**ingest call (Llamada 1)**
`POST /api/v1/ingest/message`. Procesa mensaje entrante, computa estrategia, devuelve contexto para que n8n llame al LLM.

**agent action call (Llamada 2)**
`POST /api/v1/agent/action`. Recibe lo que el LLM produjo, valida, persiste, devuelve respuesta final aprobada.

**checkpoint**
Nodo del DAG. Representa "información suficiente sobre algo". Cada checkpoint tiene `required_fields` (qué slots de `extracted_context` deben estar presentes) y `blocked_by` (qué otros checkpoints deben estar completos antes).

**status de checkpoint**
Cuatro estados posibles: `complete` (todos los fields presentes), `blocked` (dependencias no completas), `in_progress` (algunos fields presentes), `pending` (sin fields, sin bloqueo).

**all_complete**
Booleano. Verdadero cuando todos los checkpoints del DAG actual están en estado `complete`. Dispara auto-escalate a `human_handoff`.

**auto-escalate**
Transición automática de `active → human_handoff` cuando `all_complete` se cumple. No requiere intervención del LLM — es una consecuencia determinística del estado del DAG.

**debounce**
Mecanismo en `ingest.py` que espera 5 segundos tras commitear un mensaje para verificar si llegó otro. Si llegó uno nuevo, este turno no responde (`should_respond=false`). Mitiga ráfagas rápidas de mensajes del mismo cliente.

**advisory lock**
`pg_advisory_xact_lock(hash(conversation_id))`. Lock de PostgreSQL a nivel de transacción que serializa procesamiento de mensajes de la misma conversación. Se libera con commit/rollback.

---

## Términos de integración

**Chakra HQ**
Proveedor de WhatsApp Business API. Maneja la complejidad de Meta Business, plantillas, opt-in, etc. Recibe webhooks y los manda a n8n.

**n8n**
Orquestador de workflows. Recibe webhook de Chakra, llama `/ingest`, llama LLM, llama `/agent/action`, manda respuesta final a Chakra. No tiene lógica de negocio — solo enrutamiento.

**Llamada al LLM**
Hoy: OpenAI gpt-4o-mini. Configurable por cliente vía `clients.ai_model` y `clients.ai_temperature`.

---

## Términos de operación

**human_handoff**
Estado de la conversación cuando el bot ya no responde y se espera intervención humana. Hoy se llega ahí solo por auto-escalate (DAG completo). En el futuro podrá llegarse también por timeout, por escalamiento explícito del LLM, o por acción del operador.

**purchase intent (futuro)**
Entidad pendiente de implementación. Va a representar una venta en curso que persiste entre conversaciones para resolver el bug de pérdida de carrito. Pendiente de ADR-008.

**operator (futuro)**
Persona del lado del cliente (negocio) que atiende las conversaciones escaladas. Hoy es típicamente el dueño del negocio o un empleado de ventas. La vista de operador es prerequisito de venta del producto — pendiente en roadmap.
