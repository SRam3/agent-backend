# Brief de implementación — P3: cerrar el gate permeable de payment_confirmation

> **Encuadre**: este fix garantiza la **integridad del estado que el humano hereda en
> el handoff**. El handoff humano (cliente manda comprobante → humano valida vía
> Telegram) hereda los datos que el LLM recolectó. Si el gate de pago es permeable, el
> humano podría heredar un `payment_confirmation` marcado sin precondiciones válidas.
> P3 asegura que el estado heredado sea coherente.
>
> **Esto NO es una sesión de auditoría.** Escribe código en el repo. NO toca sistemas
> vivos (Postgres/n8n), NO despliega. NO toca n8n (el corte de respuesta en
> `human_handoff` y la notificación Telegram son OTRO paso, fuera de scope).
>
> **Disciplina baby-steps**: un solo fix, su test, suite en verde, un commit. Dame el
> plan de cambios por archivo y espera mi confirmación antes de escribir código.

---

## Contexto (causa raíz confirmada en auditoría)

En `agent_action.py`, dentro de `compute_context_updates` (la función pura que P2
extrajo), `merged` se calcula UNA sola vez antes de evaluar los dos gates. Secuencia
del bug:

1. El LLM manda en el mismo turno `user_confirmation=true` Y `payment_confirmation=true`,
   con `full_name` faltante (u otra precondición de user_confirmation).
2. El gate de `user_confirmation` detecta el faltante y lo elimina de los updates
   aceptados (correcto).
3. PERO `merged` ya contiene `user_confirmation` (se calculó antes), así que el gate de
   `payment_confirmation` —que requiere `user_confirmation` presente— lo ve presente y
   **deja pasar `payment_confirmation`**, aunque el `user_confirmation` del que depende
   acaba de ser rechazado.

Resultado: se puede marcar pago confirmado sobre una confirmación de usuario que no fue
válida. En el handoff, el humano hereda ese estado inconsistente.

> Nota: en datos de producción esto NUNCA se disparó (payment jamás fue propuesto por
> el LLM en 85 turnos). Es preventivo — pero protege el paso de DINERO y el estado que
> el humano va a validar. Es código sin estrenar; mejor corregirlo en frío que
> descubrirlo el día del primer pago real.

---

## El fix

En `compute_context_updates` (`agent_action.py`):

- Recalcular el estado contra el que se evalúa el gate de `payment_confirmation`
  DESPUÉS de haber eliminado los slots rechazados por el gate de `user_confirmation`.
  Es decir: el gate de pago debe evaluar contra los updates que de verdad van a
  persistir, no contra un `merged` precomputado que todavía incluye lo ya rechazado.
- Concretamente: si `user_confirmation` fue rechazado en este turno, NO debe contar como
  presente para satisfacer la precondición de `payment_confirmation` en el mismo turno.
  (Si `user_confirmation` ya estaba en el contexto de un turno anterior, sí cuenta —
  esto solo corrige el caso de ambos en el mismo turno.)
- Mantener las reglas de gate byte-idénticas en lo demás. Preservar la asimetría actual
  de side_effects: `user_confirmation` rechazado emite `warning:premature_summary_missing_*`;
  `payment_confirmation` rechazado solo loguea (no rompas ese contrato observable).
- Añadir el side_effect de rechazo para payment si hoy falta (la auditoría notó que la
  rama de user sí lo agrega y la de payment no — alinear para que el rechazo de pago
  también sea observable).

NO toca schema. NO toca el DAG (estos no son checkpoints nuevos). NO ADR. NO migración.

---

## Tests obligatorios (puros, sin DB) — en `tests/services/test_agent_action.py`

1. **El bug exacto**: turno con `user_confirmation=true` + `payment_confirmation=true` +
   `full_name` faltante → AMBOS se rechazan. `payment_confirmation` NO debe colarse.
   Este es el test que fija el contrato roto.
2. **Regresión del caso legítimo**: si `user_confirmation` YA estaba en el contexto de
   un turno previo, y este turno trae `payment_confirmation` con phone+address presentes
   → `payment_confirmation` SÍ pasa. (No romper el flujo válido.)
3. **Regresión de P2**: los order fields (quantity/grind/roast) siguen persistiendo sin
   verse afectados por este cambio.
4. Correr la suite completa: los ~22 de goal_strategy intactos, todo verde.

---

## Cierre

- Un commit: fix + tests, en verde.
- Reportar: qué se cambió (archivo:línea), qué tests se añadieron, confirmación de que
  el caso legítimo de pago sigue pasando.
- NO desplegar. NO tocar n8n.
- Recordatorio de carriles: NO implementar el corte de respuesta en `human_handoff`
  (n8n), NO la notificación Telegram, NO P6/P7, NO north-star. Solo el gate.
