# Brief — Fix venta duplicada: el operador como única autoridad del pago (A+B+C)

> **Sistema REAL (no greenfield)**: backend FastAPI en prod (Azure Container Apps,
> imagen = origin/main HEAD, PR #56, 2026-07-21), Postgres 6 tablas post-007, ADR-009
> (lazo de handoff) mergeado y probado e2e. Edita código vivo. Toca DB (migración) y
> n8n vivo (un nodo). NO rediseña.
>
> **Disciplina**: se ejecuta en DOS FASES separadas (ver abajo). Cada fase: plan por
> archivo antes de codear, espera confirmación del humano, un commit con su test, suite
> verde. Rama nueva desde main. NO desplegar sin OK explícito.
>
> ⚠️ **Nota de working copy**: la copia local puede estar en una rama vieja
> (`docs/overview-arquitectura-diagramas`, pre-ADR-009, sin `operator.py`). NO cites
> líneas de ahí. Trabaja sobre `origin/main` actualizado (haz pull/fetch primero).

---

## Contexto: qué pasó y por qué

**El síntoma**: en el e2e del lazo de handoff (2026-07-20, conversación 9635…bce7), la
venta se registró DOS VECES en el profile del cliente. `purchase_count: 2`, dos
registros idénticos (quantity: 2, total: 80000, mismo product_id, mismo
conversation_id), separados 67 segundos.

**La causa raíz**: existen DOS caminos vivos que registran una venta como pagada, y
ADR-009 solo construyó el nuevo sin desactivar el viejo.

- **Camino nuevo (correcto, ADR-009)**: operador revisa el comprobante → pulsa botón en
  Telegram → `POST /operator/confirm-payment` → marca pago, registra venta, cierra.
- **Camino viejo (legado, debió morir con ADR-009 pero sigue armado)**: el cliente
  escribe "ya pagué" (texto, sin comprobante) → el LLM propone `payment_confirmation` en
  `extracted_data` → el gate lo acepta (solo valida precondiciones, NO el origen) →
  `agent_action` ejecuta su camino legado y registra la venta.

**La secuencia exacta del bug** (del audit_log, e2e 20-jul):

| hora UTC | evento | efecto |
|----------|--------|--------|
| 22:43:22 | agent_turn · checkpoint_completed:user_confirmed | dispara aviso Telegram |
| 22:45:38 | agent_turn · context_updated:[payment_confirmation] + escalated | **venta #1 (LLM)** |
| 22:46:45 | sale_closed · actor_type=operator | **venta #2 (operador)** |

**Por qué la idempotencia no lo atrapó**: el guard del endpoint es
`state == "closed" and payment_confirmation` (`confirm_payment.py:66`). Está atado al
estado `closed`. Pero el camino viejo del LLM deja la conversación en `human_handoff`,
NO en `closed`. Así que cuando el operador pulsa el botón, el endpoint ve estado ≠
closed, concluye "no confirmada aún" y escribe la segunda venta. La idempotencia mira el
estado equivocado.

**El principio violado**: esto es exactamente la Alternativa B que ADR-009 rechazó
("el LLM no puede marcar el pago; 'ya pagué' no es prueba"). El docstring de
`confirm_payment.py:6` ya dice "The LLM must never propose it". El ADR movió la VERDAD al
operador pero nunca RETIRÓ la autoridad del camino viejo. Hoy coexisten dos autoridades
para el mismo hecho, y ambas escriben. El objetivo del fix: **hacer que el operador sea
la ÚNICA autoridad del pago, en código, no solo en el documento.**

## Ubicaciones confirmadas (citadas sobre origin/main)

- `agent_action.py:43` — `payment_confirmation` es un STRATEGY_FIELD normal.
- `agent_action.py:177-181` — gate P3 valida precondiciones, no origen.
- `agent_action.py:373` — `payment_just_confirmed = "payment_confirmation" in strategy_accepted`
- `agent_action.py:380-387, 561-572, 391` — el camino que registra la venta y sube
  lifecycle cuando el LLM propuso el pago.
- `confirm_payment.py:66` — guard de idempotencia atado a `state == "closed"`.
- `confirm_payment.py:6` — docstring que ya declara el invariante correcto.
- **Prompt, lugar 1 (DB)**: `clients.system_prompt_template` (texto de
  `009_humanize_prompt_after_apr30_review.sql:217`): instruye
  "`payment_confirmation`: true cuando el cliente envíe el comprobante".
- **Prompt, lugar 2 (n8n vivo)**: workflow `cafe_arenillo_v2` (xKtfVQsyYWkwQta9), nodo
  "Build LLM Prompt": lista `payment_confirmation` como key válida de extracción. Mismo
  texto en el export `n8n_workflow/cafe_arenillo_v2.json:403`.

---

## El fix: A + B + C

**A — desactivar el camino LLM→venta en el backend.** En `agent_action.py`, hacer que
una propuesta de `payment_confirmation` proveniente del LLM NO sea tratada como venta
consumada. El pago solo se registra vía el endpoint del operador. Concretamente: sacar
`payment_confirmation` de la vía que dispara `_merge_profile(payment_just_confirmed=True)`
/ el registro de compra / el auto-escalate por pago. Decide con el humano el mecanismo
exacto (¿excluir la key del STRATEGY_FIELD?, ¿ignorarla en el punto :373?) y propónlo
antes de codear — pero el invariante es: **el LLM proponiendo payment_confirmation NO
registra venta ni sube lifecycle.**

**B — limpiar el prompt en sus DOS lugares.**
- B1 (DB): migración `011_remove_payment_confirmation_from_prompt.sql` que edita
  `clients.system_prompt_template` para quitar la instrucción que le pide al LLM emitir
  `payment_confirmation`. (Migración nueva, secuencial, no se modifica ninguna aplicada.)
- B2 (n8n vivo): editar el nodo "Build LLM Prompt" de `cafe_arenillo_v2` para quitar
  `payment_confirmation` de las "Valid extracted_data keys". **FASE 2 — toca n8n vivo.**

**C — desacoplar la idempotencia del endpoint del estado.** En `confirm_payment.py`, el
guard de idempotencia debe detectar "pago ya registrado / ya en contexto" SIN depender de
que la conversación esté en `closed`. Así, si por cualquier razón el pago ya está en el
contexto, el endpoint no lo duplica. C deja de ser parche del síntoma y pasa a ser el
cinturón de seguridad del invariante "una sola venta por confirmación".

---

## Secuencia en DOS FASES (por riesgo)

**FASE 1 — Backend (terreno fuerte, testeable, NO toca n8n):** A + C + migración B1.
Esto solo ya corta la mayoría del bug: con A, aunque el prompt aún pida el campo, el
backend ignora la propuesta del LLM y no registra la venta. Un commit. Deja el fix de
fondo a salvo en main antes de tocar el workflow vivo.

**FASE 2 — n8n (mayor riesgo, sesión aparte):** B2 (editar el nodo del workflow).
Limpieza defensiva. Requiere **export del workflow ANTES y DESPUÉS**, y verificación con
conversación de prueba. Es lo último porque toca producción viva y el fix de Fase 1 ya
protege el invariante sin esto.

> Partir así da una victoria verificable en backend antes de tocar n8n. Si algo sale mal
> en n8n, el fix de fondo ya está en main.

---

## Tests obligatorios (el contrato que evita que el bug reviva)

En `tests/services/test_agent_action.py` y `tests/services/test_confirm_payment.py`:

1. **Invariante central (A)**: un turno donde el LLM propone `payment_confirmation` con
   todas las precondiciones presentes → NO se registra venta en el profile, NO sube
   lifecycle, NO se dispara auto-escalate por pago. (Fija que el LLM no cierra ventas.)
2. **El operador sí registra (no romper el camino bueno)**: llamada al endpoint
   `confirm-payment` autorizada → SÍ registra la venta, sube lifecycle, cierra. Sin
   cambios respecto a ADR-009.
3. **Idempotencia desacoplada (C)**: con el pago ya en contexto (simulando el caso viejo)
   y la conversación en `human_handoff` (NO closed) → el endpoint NO escribe una segunda
   venta. Este es el test que fija el fix C.
4. **No duplicación e2e-lógico**: simular la secuencia del bug (LLM propone pago, luego
   operador confirma) → resultado final = UNA sola venta en el profile, purchase_count=1.
5. **Regresión**: los tests de P2/P3/P8/ADR-009 siguen verdes. El shape de purchases
   (quantity/total, contrato P2) intacto.

---

## Limpieza de datos (PASO SEPARADO, después del fix, en manos del humano)

NO es parte del fix de código. Es un UPDATE deliberado a producción sobre una fila
conocida: `client_user 15d89710-…` ("Sebastian", tel ***8477 = número de prueba del
humano). Estado sucio actual: `purchase_count: 2`, dos registros duplicados. Objetivo:
`purchase_count: 1` con el registro legítimo (el del operador, 22:46:45.509); eliminar el
registro del LLM (22:45:38.117).

- Se hace DESPUÉS de mergear el fix (si se limpia antes y se vuelve a probar, se re-ensucia).
- Es un UPDATE puntual, no un cambio amplio. El humano lo ejecuta o lo aprueba
  explícitamente. Code NO lo hace sin luz verde.

---

## Cierre y registro

- Fase 1: un commit (A+C+B1) con sus tests, verde. Fase 2: edición n8n con export
  antes/después. Reportar archivo:línea de cada cambio.
- Actualizar las **notas as-built de ADR-009** (el PR sigue editable / o nota nueva si ya
  se mergeó): registrar que el camino legado LLM→payment se desactivó, cerrando la brecha
  entre la decisión del ADR (Alternativa B rechazada) y el código.
- NO desplegar sin OK. NO tocar north-star (delay humano, ADR-010 detección de bots
  siguen en diseño, no se implementan aquí).

## Definition of done

- El LLM proponiendo `payment_confirmation` NO registra venta (test 1 verde).
- El operador sigue siendo la única vía que registra venta (test 2 verde).
- El endpoint no duplica aunque el pago esté en contexto en estado no-closed (test 3).
- El prompt ya no pide `payment_confirmation` en NINGUNO de sus dos lugares (DB + n8n).
- Datos de prueba limpios (paso separado, aprobado por el humano).
- Regresión completa verde; contrato P2 del shape de purchases intacto.
