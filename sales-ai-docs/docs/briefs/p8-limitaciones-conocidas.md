# P8 — Limitaciones conocidas del circuit breaker (registro)

> Fecha: 2026-07-11. Contexto: P8 (`feat/p8-circuit-breaker`) implementó la parte
> backend del freno a loops conversacionales: 3 outbounds idénticos consecutivos →
> `human_handoff` + side_effect `circuit_breaker:loop_detected` + supresión de la
> respuesta (`approved=False`, `final_response_text=""`). Ver `impl-brief-P8.md`.
>
> Estas dos limitaciones quedan registradas JUNTAS y fuera del scope de P8.
> Ninguna invalida el valor de P8 (deja de quemar tokens y registra el evento),
> pero ambas impiden vender "loop resuelto".

---

## 1. El corte real de la respuesta depende de n8n (dependencia)

- El backend marca `human_handoff` y devuelve `approved=False` +
  `final_response_text=""` — pero HOY n8n manda `final_response_text` a Chakra
  **sin chequear** `approved` ni el estado de la conversación. Camino nunca
  ejercido (mismo issue que el handoff de cierre de venta).
- Nadie se entera del handoff: la notificación Telegram fue revertida (no está
  en main) y no hay vista de operador (deuda #4 en `CLAUDE.md`).
- **Cerrar el lazo es trabajo separado, compartido con el handoff de venta**:
  1. n8n debe chequear `approved === true && final_response_text` no vacío
     antes de enviar (y/o cortar cuando `conversation_state = human_handoff`).
  2. Notificación a un humano (Telegram u operador) al escalar.

Mientras tanto: el breaker suprime la respuesta también cuando la conversación
YA está en `human_handoff`, así que aunque n8n siga llamando `/agent/action`,
el backend no persiste ni aprueba más outbounds idénticos.

## 2. Loop de texto variable NO cubierto (candidato P9)

- El trigger de P8 es comparación **EXACTA** de texto (decisión confirmada del
  brief: cero falsos positivos por respuestas legítimamente parecidas, simple
  de razonar y testear).
- Un loop donde el LLM **parafrasea** — texto distinto cada turno, 0% de
  progreso en el DAG — NO dispara el breaker.
- Cubrirlo requiere otro detector (estancamiento del DAG: N turnos consecutivos
  sin progreso) con una decisión de producto pendiente (¿qué N?) y riesgo real
  de falsos positivos: conversaciones legítimas pasan turnos sin progreso
  (preguntas de producto, small talk, "déjame pensarlo").
- Registrado como **candidato P9**. No mezclar con P8.

---

## Definición de "cerrado"

Este registro se cierra cuando:
- [ ] n8n corta el envío según `approved`/`final_response_text` (limitación 1)
- [ ] existe notificación de handoff a un humano (limitación 1)
- [ ] hay decisión explícita sobre el detector de estancamiento — implementarlo
      o descartarlo con ADR (limitación 2)
