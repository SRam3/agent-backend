# Brief de implementación — P8: circuit breaker para loops conversacionales

> **Sistema REAL (no greenfield)**: backend FastAPI en prod (Azure Container Apps),
> Postgres 6 tablas post-007, migración 010, P2/P3/P4/ADR-008 mergeados. Este brief
> edita código vivo. NO rediseña. NO toca schema.
>
> **Alcance honesto**: P8 hace la parte BACKEND, determinista y testeable (detectar
> loop → marcar human_handoff → side_effect). El EFECTO del escalamiento (que el bot
> deje de responder + que un humano se entere) depende de piezas que NO existen todavía
> (corte en n8n, notificación Telegram revertida). Ver "Dependencia conocida". P8 tiene
> valor solo aunque esas no existan: deja de quemar tokens en el loop y registra el
> evento. Pero NO se vende como "loop resuelto" — es "loop detectado y marcado".
>
> **Disciplina**: plan por archivo antes de codear, espera confirmación. Un commit con
> sus tests, suite verde. Rama nueva desde main: `feat/p8-circuit-breaker`. NO desplegar.

---

## Contexto (evidencia dura de la auditoría)

Caso real capturado: un usuario fuera de contexto provocó **19 respuestas outbound
idénticas en 29 minutos (70 mensajes), 0% de progreso en el DAG, sin ningún freno**.
El sistema no tiene protección contra loops conversacionales. Quema tokens, degrada la
marca, y —crucial— nadie se entera.

No es preventivo teórico (como fue P3): es un fallo que ya ocurrió con un usuario real.

## Decisión de diseño (confirmada)

- **Disparador**: 3 respuestas outbound **consecutivas con texto idéntico** (comparación
  EXACTA, no difusa) dentro de la misma conversación. Exacta = sin falsos positivos por
  respuestas legítimamente parecidas; simple de razonar y testear.
- **Acción al dispararse**: escalar la conversación a `human_handoff` (mismo mecanismo
  que el cierre de venta) y emitir un side_effect observable
  (`circuit_breaker:loop_detected`). El bot NO genera una respuesta más en ese turno.

---

## El fix

### Detección
- En `compute_context_updates` o el tramo de `process_agent_action` que decide el
  outbound (`agent_action.py`), ANTES de persistir el nuevo outbound: comparar el
  `final_response_text` que se va a enviar contra los **2 outbounds inmediatamente
  anteriores** de la misma conversación (leídos de `messages`, `direction='outbound'`,
  orden por timestamp desc, límite 2).
- Si los 2 previos + el actual son texto idéntico → es el 3er idéntico consecutivo →
  DISPARA.
- PROPÓN cómo lees los 2 previos: lo más limpio es que el caller ya tenga `recent_messages`
  disponible (el backend los maneja) y filtrar outbounds ahí, sin query nueva. Si hace
  falta una query, que filtre por `client_id` + `conversation_id` (tenant-safe). Confírmame
  el approach antes de codear.

### Acción
- Al disparar: `conversation.state = "human_handoff"` (reusar la transición existente que
  usa el auto-escalate del DAG — NO inventar una nueva vía; state_machine.py ya valida
  active→human_handoff).
- Emitir `side_effects: ["circuit_breaker:loop_detected"]`.
- El turno NO envía una respuesta nueva idéntica. Definir el retorno: `approved=False` con
  un `final_response_text` vacío o nulo, de modo que n8n (chequeando should_respond /
  final_response_text) no mande nada. CONFIRMA conmigo cómo señalizarlo para que n8n no
  envíe — hoy n8n manda `final_response_text`; si va vacío, ¿n8n lo maneja? (ver
  dependencia).

### Guardas
- La comparación es exacta y solo sobre outbounds consecutivos: un cliente que recibe la
  misma respuesta 3 veces porque el bot está atascado ES el caso objetivo; una respuesta
  repetida no-consecutiva (con otra en medio) NO dispara.
- El texto de las respuestas nunca se loguea en claro más de lo que ya se hace; el
  side_effect lleva el conteo, no el contenido.

NO toca schema. NO toca el DAG (no es un checkpoint nuevo). NO ADR. NO migración.

---

## Tests obligatorios (puros, sin DB) — en `tests/services/test_agent_action.py`

1. **Dispara al 3ro**: 2 outbounds previos idénticos + este turno produce el mismo texto
   → estado pasa a human_handoff, side_effect `circuit_breaker:loop_detected`, no se envía
   respuesta nueva.
2. **No dispara al 2do**: 1 outbound previo idéntico + este igual → NO dispara (solo 2).
   Sigue en active, responde normal.
3. **No dispara con no-consecutivos**: outbound A, luego B, luego A otra vez → NO dispara
   (no son 3 consecutivos idénticos).
4. **No dispara con texto distinto**: 3 outbounds consecutivos pero con texto diferente →
   NO dispara (comparación exacta).
5. **Regresión**: una conversación normal (respuestas variadas) nunca dispara y el flujo
   de P2/P3/ADR-008 sigue intacto. Suite completa verde (los ~22 de goal_strategy, los de
   agent_action, language, validation).

---

## Dependencia conocida (registrar, NO implementar aquí)

El escalamiento de P8 solo es EFECTIVO si:
- **n8n corta la respuesta** cuando la conversación está en `human_handoff` (hoy no
  verificado; camino nunca ejercido — mismo issue que el handoff de venta). Si n8n no lee
  el estado, el bot podría seguir respondiendo pese al marcado.
- **Alguien se entera** del handoff (notificación Telegram fue revertida, no está en main;
  no hay vista de operador — deuda #4).

P8 marca y frena el desperdicio del lado backend. Cerrar el lazo (n8n + notificación) es
un paso SEPARADO, compartido con el handoff de venta. Registrar como dependencia; no
mezclar en este commit.

---

## Definition of done

- Flujo: 3er outbound idéntico consecutivo → detección determinista en backend →
  human_handoff + side_effect → no se genera respuesta nueva.
- Tenant isolation: la lectura de outbounds previos filtra por client_id+conversation_id.
- Sin schema, sin migración, sin dependencias nuevas, sin tocar n8n.
- Reportar: archivo:línea, tests añadidos, y confirmación explícita de la dependencia n8n
  (que el corte real de respuesta queda fuera de scope y pendiente).
