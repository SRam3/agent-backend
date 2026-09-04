# ADR-002 — Patrón de dos llamadas sin tools

- **Estatus**: Accepted
- **Fecha**: 2026-04-03
- **Decididores**: Sebastian + cofounder/principal architect

## Contexto

La arquitectura típica de un "agente" LLM moderno (LangChain, OpenAI Assistants, AutoGPT) le da al modelo un set de herramientas (tools/functions) que puede llamar en un loop: el modelo decide qué tool usar, observa el resultado, decide la siguiente, hasta que cree haber terminado el objetivo del usuario.

Este patrón funciona bien para tareas open-ended donde el espacio de acciones es grande y el objetivo es ambiguo (asistente personal, agente de investigación). Para nuestro caso — venta por WhatsApp con flujo conocido y reglas estrictas — introduce más problemas que soluciones:

- Costo variable e impredecible: 5-15 llamadas LLM por turno son comunes.
- Latencia acumulativa: cada iteración suma 1-3 segundos.
- Debugabilidad pobre: si el agente toma una mala decisión, hay que reconstruir la cadena de razonamiento.
- Inseguridad: el modelo puede llamar tools en órdenes inesperados, encadenar acciones que vistas individualmente son válidas pero combinadas son destructivas.
- Las CRUD APIs no son buenas interfaces para LLMs — los endpoints típicos del backend exponen demasiada superficie y demasiado bajo nivel.

## Decisión

Eliminar el concepto de "tools" del agente. Cada turno conversacional consiste en:

1. **n8n → POST /api/v1/ingest/message** (backend prepara contexto + directiva)
2. **n8n → LLM** (una sola llamada, sin function calling)
3. **n8n → POST /api/v1/agent/action** (backend valida y persiste lo que el LLM extrajo)

El backend sabe todo lo que el LLM necesita saber antes de la llamada. El LLM no descubre nada que el backend no le haya dicho. El backend valida todo lo que el LLM propone después de la llamada.

## Alternativas consideradas

- **Tools restringidos (3-5 funciones acotadas)**: menor superficie que un agente abierto, pero sigue introduciendo loops y costo variable. Descartado por la misma razón general.
- **OpenAI Assistants API con function calling**: lock-in con OpenAI, costo por threads persistentes, complejidad operacional alta. Descartado.
- **Function calling solo para extracción estructurada**: aquí hay un punto válido — usar `response_format` o function calling como mecanismo de structured output (no como agente de acciones). Esto sí lo vamos a adoptar pero como técnica de output, no como patrón de agente. Pendiente de ADR cuando se implemente.

## Consecuencias

### Positivas
- Costo y latencia constantes por turno. Capacidad de planeación lineal.
- El sistema es debuggeable: 2 HTTP calls + 1 LLM call, todas inspeccionables.
- El backend mantiene control total sobre qué se persiste, en qué orden, con qué validaciones. El LLM nunca toca la DB directamente.
- Fail-safe: si el backend rechaza la propuesta del LLM, el texto de respuesta igual va al usuario. La conversación no se rompe.

### Negativas
- Si un cliente requiere comportamiento que necesita ramificación condicional dinámica (consultar API externa para decidir flujo), hay que modelarlo en el backend, no delegarlo al LLM.
- No aprovechamos las capacidades nativas de tool-calling de modelos modernos (parallel function calling, etc.). Si el ecosistema evoluciona en esa dirección y los modelos se vuelven mucho mejores con tools, podríamos quedarnos atrás.

### Trade-offs explícitos
- Ganamos confiabilidad operacional a cambio de capacidad emergente. Para venta repetible, vale la pena. Para soporte técnico complejo, posiblemente no.

## Notas as-built (2026-09-01) — el fail-safe tiene dos excepciones, y ninguna estaba escrita aquí

La consecuencia positiva redactada el 2026-04-03 dice: *«Fail-safe: si el backend rechaza la propuesta
del LLM, el texto de respuesta igual va al usuario. La conversación no se rompe.»* Eso ya no describe
el comportamiento, y las dos excepciones se introdujeron sin registrarse en este ADR:

1. **P8 · circuit breaker** (2026-07-11). Ante el tercer outbound idéntico consecutivo el backend
   devuelve `approved=False` y `final_response_text=""`: **suprime** el texto del LLM y el cliente no
   recibe nada. Ha disparado cuatro veces en producción (07-18 ×3, 08-15 ×1).
2. **ADR-010 §1 · el resumen del pedido** (2026-08-23, pendiente de despliegue al escribirse esta
   nota). Cuando el pedido está completo y el resumen guardado ya no coincide, el backend renderiza el
   suyo y su texto **reemplaza** el del LLM en ese turno.

Ambas son coherentes con la tesis de este ADR — el backend gobierna — y ninguna ha roto una
conversación: la supresión de P8 va acompañada de escalamiento y aviso al operador, y el reemplazo de
ADR-010 entrega un mensaje mejor que el que descarta. Lo que quedó desactualizado es la redacción de
la consecuencia, no la decisión.

**Formulación vigente**: el backend nunca deja al cliente sin respuesta **por un rechazo de slot** —
un gate que descarta un dato no interrumpe la conversación, y eso sigue siendo el corazón del
fail-safe. Pero el backend **sí** puede suprimir o reemplazar el texto saliente cuando la decisión es
suya: un loop detectado, o un dato operativo que no se le confía al NLG.

## Cuándo revisar

Revisar esta decisión si:
- Aparece un caso de uso (mismo dominio o adyacente) donde el flujo no es modelable como DAG — requiere razonamiento dinámico no anticipable
- Los modelos LLM se vuelven dramáticamente mejores en tool-use con costo competitivo a una sola llamada
- Encontramos que el patrón nos limita en una dirección de producto importante
