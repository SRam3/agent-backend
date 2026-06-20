# Brief de implementación — Sesión P2 + P4 (cercanía por datos)

> **Esto NO es una sesión de auditoría.** Esta sesión SÍ escribe código en el repo.
> Pero NO toca sistemas vivos (ni Postgres de prod ni n8n vivo) salvo para
> diagnóstico de solo-lectura explícitamente marcado. El despliegue a prod es un paso
> posterior y separado, no parte de esta sesión.
>
> **Disciplina baby-steps (innegociable)**: una cosa a la vez. Cada cambio se
> implementa, se cubre con test, se corre la suite en verde, y se commitea ANTES de
> empezar el siguiente. NO encadenar P2 y P4 en un solo commit. Si un test queda
> rojo, se arregla o se revierte ese paso — no se avanza con deuda roja.
>
> **Orden fijo**: P2 (fix) primero. P4 (diagnóstico) después. P2 desbloquea P4.
>
> **Antes de tocar nada**: dame tu plan de cambios por archivo y espera mi
> confirmación. No quiero ver código hasta aprobar el plan.

---

## Contexto (de la auditoría viva, ya confirmado en datos)

- El LLM extrae `quantity` (12 msgs) y `grind_preference` (8 msgs), pero
  `STRATEGY_FIELDS` (agent_action.py:35-39) los descarta antes del merge a
  `extracted_context`. El bloque "ESTADO DEL PEDIDO" los vuelve a pedir cada turno
  (prompt_context.py:111-121, _ORDER_FIELDS). Efecto: el bot re-pregunta lo ya dicho.
- `client_users.profile` está VACÍO en prod: nunca se poblaron `purchases`,
  `last_conversation_summary`, etc. La lazy-compaction (ingest.py:146-167 /
  conversation_summary.py) falla en silencio: traga la excepción y devuelve None con
  un warning que nadie lee (conversation_summary.py:210-215).
- La key de OpenAI SÍ carga al boot (no es esa la causa). Causa exacta del fallo de
  compaction: aún no determinada.

---

## P2 — Persistir quantity / grind_preference / roast_preference (FIX)

**Objetivo**: que los datos que el cliente ya dio se guarden y dejen de re-pedirse.

**Diseño a confirmar conmigo antes de codear**: estos campos NO son del DAG (no son
checkpoints de cierre de venta), así que NO deben entrar a `STRATEGY_FIELDS` sin más
—eso podría alterar la lógica del DAG—. La opción más limpia es un set separado de
"order fields" no-DAG que también se mergea a `extracted_context`, en paralelo a
`STRATEGY_FIELDS`, sin que el GoalStrategyEngine los trate como checkpoints. Propón la
forma concreta (constante separada, merge explícito) y espera visto bueno.

**Alcance del cambio**:
- Persistir `quantity`, `grind_preference`, `roast_preference` a `extracted_context`
  cuando el LLM los extrae (agent_action.py, zona del filtro :35-39 y merge).
- Completar `_merge_profile` para que el registro de `purchases` lleve `quantity` y
  `total` como promete el COMMENT de 008:50-64 (hoy agent_action.py:301-304 guarda
  solo {date, product_id}).
- Verificar que `prompt_context.py` (ESTADO DEL PEDIDO) deje de marcar como faltante
  lo que ya está en contexto.

**Tests obligatorios (servicio, pure-python, sin DB viva)**:
- Dado extracted_data con `quantity` → se persiste en extracted_context.
- Dado un pedido con quantity+product → el registro de purchase lleva quantity y total.
- Regresión: que esto NO altere la evaluación del DAG ni los checkpoints existentes
  (correr los ~22 tests de goal_strategy en verde).

**NO toca schema** (JSONB flexible). NO requiere ADR. NO requiere migración.

**Commit P2 y verde antes de pasar a P4.**

---

## P4 — Diagnosticar la lazy-compaction rota (DIAGNÓSTICO, no fix aún)

**Objetivo de esta sesión**: SABER por qué falla. NO arreglarla a ciegas.

**Pasos**:
1. Reproducir `summarize_conversation` en entorno controlado (local / test), contra
   el shape de datos de la conversación del 3-may (4 msgs) o un fixture equivalente.
   Capturar la excepción real que hoy se traga el try/except
   (conversation_summary.py:210-215). Reportarla — no silenciarla.
2. Mínimo entregable de fix permitido en esta sesión: subir ese `logger.warning`
   silencioso a algo observable (logger.error + contador / métrica), para que la
   memoria del negocio deje de morir en silencio (DEUDA #3). Esto es 🟢 y seguro.
3. Si el paso 1 revela la causa raíz y el fix es acotado y de bajo riesgo, PROPONLO
   —con su clasificación 🟢/🟡— pero NO lo implementes sin confirmármelo. Si la causa
   resulta tocar el hot path o schema, se detiene y va a decisión separada.

**Diagnóstico de solo-lectura contra prod permitido SOLO si es necesario**: un SELECT
puntual para inspeccionar el shape exacto de la conversación del 3-may. Read-only,
PII enmascarada, misma regla que la auditoría. Nada de escribir.

**Dependencia explícita**: P4 se prueba DESPUÉS de P2, porque un resumen útil necesita
que quantity/preferencias ya estén en contexto. Verificar que, con P2 aplicado, un
resumen reconstruido incluiría el pedido completo.

---

## Cierre de la sesión

- Dos commits limpios: uno P2 (fix + tests), uno P4 (observabilidad del warning +
  reporte de causa raíz). Si P4 produce además un fix aprobado, va en su propio commit.
- NO desplegar a prod en esta sesión. El deploy es un paso posterior, deliberado.
- Reportar: qué se cambió, qué tests se añadieron, la causa raíz de la compaction (o
  "aún no concluyente" con lo que se encontró), y qué queda pendiente.
- Recordatorio de carriles: esto es remediación. NO implementar nada del north-star
  (resume asíncrono, sale_events, ADR-009 de memoria de relación) — esas siguen en
  pausa. Si al tocar el perfil te dan ganas de "ya que estoy" construir la memoria de
  relación, NO: ese es otro carril y necesita su propia decisión.
