# ADR-003 — strategy_version contra contexto viejo

- **Estatus**: Accepted
- **Fecha**: 2026-04-10
- **Decididores**: Sebastian + cofounder/principal architect

## Contexto

El patrón de dos llamadas (ADR-002) introduce una ventana entre `/ingest/message` (donde el backend computa la directiva) y `/agent/action` (donde el backend valida la propuesta del LLM). Entre esas dos llamadas pueden pasar varios segundos — el tiempo de la llamada al LLM más la latencia de red de n8n.

En esa ventana pueden ocurrir varias cosas que invalidan la decisión del LLM:
- Otro mensaje del usuario llega y se procesa (el cliente escribió dos veces seguidas)
- n8n hace retry de la primera llamada y se procesan dos veces el mismo mensaje
- Un operador humano modifica el estado de la conversación desde otro canal (futuro)
- Un job batch toca la conversación

Si el LLM propone "marcar `user_confirmation=true`" basándose en un contexto donde faltaba la dirección, pero en el momento de validar ya llegó la dirección, la decisión es válida — pero también puede ser al revés: el LLM extrajo basándose en un estado que ya cambió y la propuesta es incorrecta.

La revisión arquitectónica de inicios de abril (`revision_par_backend_n8n_dag.md`) marcó esto como riesgo alto.

## Decisión

Introducir un campo `strategy_version` en `conversations` que se incrementa atómicamente en cada `/ingest/message`. El backend devuelve ese número en la respuesta del ingest, y exige que `/agent/action` lo eche de regreso. Si la versión que llega en `/agent/action` no coincide con la versión actual de la conversación en DB, el backend responde **HTTP 409 Conflict** con `error: stale_context`. n8n debe entonces re-llamar a `/ingest/message` con el mismo mensaje original para regenerar la directiva.

## Alternativas consideradas

- **No hacer nada**: aceptar que algunas propuestas se ejecutarán sobre contexto viejo. Descartado — los DAG gates mitigan parte del problema pero no todo, y la falta de mecanismo explícito hace muy difícil debuggear casos que sí se cuelan.
- **Lock optimista por hash de extracted_context**: más caro de calcular y más frágil ante cambios de orden de campos en JSONB. Descartado por costo.
- **Lock pesimista durante toda la ventana del LLM**: bloquearía otras requests del mismo usuario, mata throughput. Descartado.
- **Timestamp-based**: usar `last_message_at` como token. Descartado por riesgo de colisión de timestamps en ráfagas (resolución de microsegundos no es siempre garantía suficiente bajo carga).

## Consecuencias

### Positivas
- Detección determinística de contexto viejo. Si el `/agent/action` opera sobre estado obsoleto, el backend lo rechaza explícitamente.
- Auditable: el `strategy_version` queda en logs y permite reconstruir qué versión vio el LLM.
- Simple: un solo entero, una comparación, un código de error específico.
- Compatible con retries: n8n puede hacer retry de ingest sin riesgo de duplicar efectos secundarios (la idempotencia de `chakra_message_id` cubre eso).

### Negativas
- n8n debe manejar el 409 explícitamente — agrega complejidad al workflow.
- Si la lógica de retry de n8n no está bien armada, el sistema puede quedarse en loop pidiendo nueva directiva. Mitigación: límite de retries en n8n.
- El `strategy_version` no se versiona históricamente (sólo el actual). Si quisiéramos reconstruir directivas pasadas, hay que mirar `messages.created_at` correlacionado con `audit_log`. Suficiente por ahora.

### Trade-offs explícitos
- Agregamos una columna y una verificación a cambio de garantía de consistencia. El costo es mínimo, el beneficio es grande.

## Cuándo revisar

Revisar esta decisión si:
- Aparecen patrones de uso donde el `strategy_version` se vuelve un cuello de botella (improbable, es solo un entero)
- Se necesita reconstruir histórico de directivas para analytics avanzado — entonces `strategy_snapshot` JSONB en `conversations` puede no bastar y haya que considerar tabla de history
