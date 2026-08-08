# Brief de análisis — Primera venta cerrada (postmortem de un ÉXITO) · READ-ONLY

> **Tipo distinto de análisis.** Todos los briefs previos buscaban qué está ROTO. Este
> busca qué FUNCIONÓ y por qué, y —más importante— si es REPRODUCIBLE o fue afortunado.
> Un solo éxito invita a conclusiones sobre-optimistas; el trabajo de este análisis es
> resistir esa tentación y separar "diseño funcionando" de "condiciones favorables que
> no controlamos".
>
> ## Reglas (producción, PII)
> - READ-ONLY total. Solo SELECT contra Postgres, solo lectura de la conversación.
>   Nada de escribir, migrar, tocar n8n.
> - PII enmascarada en todo el reporte (nombre, teléfono, dirección).
> - Evidencia por afirmación: message_id / timestamp / campo. Si algo no es
>   determinable, decir "no determinable", no inferir una narrativa bonita.
> - Entregable: reporte en prosa. NO escribir a disco (lo guarda el humano como el
>   primer postmortem de éxito del repo).
>
> **Dato de partida ya confirmado por el humano**: los datos que el bot recolectó
> estaban completos y correctos; el operador no ajustó nada al tomar el handoff. NO
> re-verifiques eso como si fuera dudoso — es un hecho. Úsalo como base: el core de
> recolección+validación funcionó. El análisis se enfoca en TODO LO DEMÁS.

---

## Identificar la conversación

Localiza la conversación de la venta cerrada: la que pasó a `human_handoff` por
auto-escalate del DAG completo (no por circuit breaker), con un cliente real, en la
ventana reciente. Confírmame cuál es (id enmascarado + timestamp) antes de analizar en
profundidad, por si hay más de una candidata.

## Pregunta central: ¿reproducible o afortunada?

Todo el análisis sirve para responder esto. Se descompone en condiciones verificables:

### 1. ¿En cuántas sesiones ocurrió? (la condición más importante)
- ¿La venta se cerró en UNA sola conversación continua, o cruzó la ventana de 24h
  (varias filas de `conversations` para el mismo `client_user`)?
- Esto importa muchísimo: si cruzó la ventana, la lazy-compaction (que sabemos ROTA en
  prod, deuda #7/P4) tuvo que haber funcionado o haberse esquivado. Si fue una sola
  sesión, la venta EVITÓ el problema no resuelto más grande que tienes — lo cual es
  suerte estructural, no diseño. Determínalo con evidencia (timestamps entre mensajes,
  número de filas de conversación).

### 2. ¿Cuántos turnos y cuánto tiempo?
- Número de mensajes inbound/outbound hasta el cierre. Tiempo total del primer mensaje
  al handoff.
- ¿Hubo turnos "desperdiciados" (el bot re-preguntó algo ya dado, ambigüedad, corrección)?
  Aunque la venta cerró, esos son fricción que en un cliente menos paciente la rompería.

### 3. ¿El cliente llegó "fácil"?
- ¿El cliente ya sabía qué quería (pidió el producto directo) o el bot tuvo que
  guiarlo/venderle? Un cliente decidido cierra a pesar de fricciones que hundirían a uno
  indeciso. Lee el arco real de la conversación.
- ¿Hubo preguntas del cliente que el bot esquivó o respondió flojo pero el cliente no
  insistió? (posibles alucinaciones toleradas por un cliente motivado).

### 4. ¿El tono se sintió natural EN UN CASO QUE GANÓ?
- Evaluar el tono aquí es más valioso que en las conversaciones fallidas: ¿sonó humano o
  robótico en un intercambio que progresó? ¿El cliente reaccionó al tono?
- ¿Se violó la regla anti-chatbot (preguntas de permiso "¿te gustaría que...?") aunque
  la venta avanzara? Si sí, confirma que el tono robótico NO impidió esta venta — pero
  no concluyas que "entonces no importa"; un cliente decidido lo tolera, uno frío no.

### 5. ¿Qué gates/checkpoints se ejercieron y cómo?
- Recorrido real del DAG: product_matched → lead_qualified → shipping → user_confirmed →
  payment_confirmed → handoff. ¿Algún gate rechazó algo en el camino (side_effects en
  audit_log)? ¿El recálculo de P3 actuó? ¿La validación de teléfono E.164 (ADR-008)
  dejó pasar o rechazó algo?
- ¿El corte de respuesta post-handoff funcionó de verdad? El humano confirmó que el bot
  no volvió a responder — busca evidencia en `messages`: ¿hay outbounds después del
  timestamp del handoff? (Debería NO haberlos. Confírmalo con datos — es la primera
  evidencia en prod de que el corte funciona.)

## Cierre del reporte: reproducibilidad honesta

Termina con un veredicto separado en dos columnas:
- **Diseño funcionando** (lo que se repetirá porque es el sistema): recolección,
  validación, gates, corte post-handoff, etc.
- **Condiciones favorables** (lo que quizás NO se repita): una sola sesión, cliente
  decidido, tolerancia a fricción, que el operador estuviera mirando, etc.

Y una lista corta de: qué fricción, aunque no rompió ESTA venta, rompería la próxima con
un cliente menos paciente. Esas son las prioridades reales que salen del éxito.

NO propongas implementaciones. Este análisis produce entendimiento, no tareas.
