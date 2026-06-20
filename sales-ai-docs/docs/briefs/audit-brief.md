# Brief de auditoría v2 — estado actual + revisión viva + plan de trabajo (READ-ONLY)

> **Propósito**: cerrar lo que la auditoría de archivos NO pudo ver (la Postgres
> viva y el n8n desplegado), reconfirmar hallazgos clave contra el sistema real, y
> producir un **plan de trabajo priorizado para arreglar lo que está roto**,
> secuenciado de forma coherente con el north star. NO es un brief de
> implementación: este pase sigue siendo de diagnóstico + planeación, no de ejecución.
>
> ## Reglas absolutas de esta sesión (salvaguarda de PRODUCCIÓN)
>
> - **READ-ONLY contra todo sistema vivo.** La conexión a Postgres debe ser de solo
>   lectura. PROHIBIDO: `INSERT`, `UPDATE`, `DELETE`, `ALTER`, `DROP`, `CREATE`,
>   `TRUNCATE`, correr migraciones, o cualquier DDL/DML. Solo `SELECT` (y `EXPLAIN`,
>   `\d`, lectura de catálogo).
> - **NO tocar el n8n vivo** más allá de LEER la definición del workflow activo.
>   Prohibido activar/desactivar workflows, editar nodos, ejecutar el workflow.
> - **NO editar, crear ni borrar archivos** del repo. NO "arreglar de paso".
> - Si una consulta o paso pudiera mutar estado, NO lo ejecutes: descríbelo y
>   reporta que lo omitiste por la regla de solo-lectura.
> - Toda PII (teléfono, dirección, email, nombre) **enmascarada** en el reporte.
> - Cada hallazgo con evidencia: `archivo:línea`, la query `SELECT` exacta, o el
>   nombre del nodo de n8n. Si algo no es determinable, decir "no determinable",
>   no adivinar.
>
> **Entregable**: un reporte en prosa (no escribir a disco; lo guarda el humano).
> Secciones 0–5 = re-verificación; 6–7 = revisión viva nueva; 8 = plan de trabajo.

---

## Contexto que ya sabemos (de la auditoría de archivos previa)

No re-derives esto desde cero; pártelo como base y VERIFÍCALO contra el sistema vivo:

- El workflow **v2 (backend-governed) es el que corre** en producción para Café
  Arenillo (confirmado por el humano). El legacy `cafe_arenillo.json` NO es el activo.
  → Confirma esto LEYENDO el n8n vivo (sección 7), no asumiéndolo.
- El backend hace ≥2 transacciones en ingest (no "1" como dice el doc), con un
  `commit()` intermedio + `asyncio.sleep(5)` que suelta el advisory lock.
- El "reset por idle 30 min" (DEUDA #10) NO existe en código.
- `quantity`/`grind_preference` se piden en el prompt pero se filtran fuera de
  `STRATEGY_FIELDS` → no se persisten.
- Gate de `payment_confirmation`: `merged` se calcula una sola vez → un
  `user_confirmation` rechazado en el mismo turno puede colarse.
- Outbound (texto e imagen) SIN idempotencia desde que 007 dropeó `idempotency_key`.
  La imagen duplicada es un síntoma de esto.
- La lazy-compaction / resume entre conversaciones YA existe en código sin ADR.

---

## Secciones 0–5: re-verificación rápida contra el sistema vivo

(Estas ya se auditaron sobre archivos. Aquí solo CONFIRMA o REFUTA contra datos/n8n
vivos lo que antes era "no determinable". No repitas el análisis de archivos completo.)

**0. Drift** — ¿algún drift nuevo aparece al comparar el schema REAL de la DB
(catálogo de Postgres) contra las migraciones del repo? ¿La última migración aplicada
en la DB coincide con la última del repo? (query a la tabla de versiones de migración
si existe, o `\dt` + inspección de columnas).

**1–5** — solo reconfirma los puntos que dependían de datos vivos:
- shape real de `extracted_context` y `profile` (sección 6 lo cubre en detalle),
- qué modelo y qué prompt recibe de verdad el LLM en el workflow activo (sección 7),
- si hay evidencia en datos de pérdida de contexto por la ventana de 24h.

---

## 6. Revisión de la base de datos Postgres VIVA (solo SELECT)

Objetivo: cerrar los "no determinable" del primer reporte y validar que el schema y
los datos reales coinciden con lo que el código asume.

- **Inventario real**: lista las tablas que EXISTEN en la DB (`\dt`). ¿Son las 7
  activas que el doc afirma (post-007)? ¿Sobrevive alguna tabla supuestamente
  dropeada (`leads`, `orders`, `order_line_items`)? ¿Existe alguna tabla que el repo
  no documenta?
- **Schema vs. migraciones**: para cada tabla activa, ¿las columnas, tipos y CHECK
  constraints reales coinciden con la última migración del repo? Reporta diferencias.
- **Shape real del JSONB** (el "no determinable" clave): consulta una muestra pequeña
  (5–10 filas, PII enmascarada) de `conversations.extracted_context` y
  `client_users.profile`. ¿Qué claves aparecen de verdad? ¿Aparece `quantity` /
  `grind_preference` en algún `extracted_context` real (lo que confirmaría o
  refutaría que el filtro los descarta siempre)? ¿El `profile` tiene `purchases` con
  `quantity`/`total`, o llega incompleto como predice el bug?
- **Integridad multi-tenant en datos**: ¿hay alguna fila con `client_id` nulo o
  inconsistente en tablas tenant-facing? ¿Algún `conversation`/`message` huérfano?
- **Idempotencia en datos**: ¿hay mensajes outbound duplicados reales? (p.ej. dos
  filas en `messages` con `direction='outbound'`, mismo `conversation_id`, mismo
  contenido/imagen, timestamps cercanos). Esto CONFIRMARÍA el bug de imagen con
  evidencia dura, no solo por código.
- **Estados en uso**: `SELECT state, COUNT(*) FROM conversations GROUP BY state`.
  ¿Se usan de verdad solo los 3 estados (active/human_handoff/closed)?
- **Salud operativa básica**: volumen de filas por tabla, conversaciones activas,
  cuántas escalaron a `human_handoff` sin resolver. Sin tocar nada, solo contar.

> Recordatorio: solo `SELECT`. Si para responder algo necesitarías escribir, repórtalo
> como "requiere escritura, omitido".

## 7. Revisión del flujo n8n VIVO (solo lectura de la definición)

Objetivo: cerrar el hallazgo 0.1 del primer reporte con evidencia del workflow activo,
y rastrear el camino real de la imagen.

- **Cuál corre**: confirma cuál workflow está `active: true` en el n8n vivo y si de
  verdad llama a `/ingest/message` y `/agent/action`. Pega los nombres de los nodos
  HTTP y sus URLs (enmascara hosts/tokens).
- **Ensamble del prompt**: identifica el nodo donde se concatena el system prompt
  final (el "Build prompt context" o equivalente). ¿Qué piezas mete y en qué orden?
  Reconstruye el prompt final REAL que se manda al LLM (con las piezas del backend),
  no el teórico.
- **Modelo real**: ¿qué modelo usa el nodo del LLM en el workflow activo
  (gpt-4o-mini, gpt-4.1-mini, otro)? Esto resuelve la discrepancia del primer reporte.
- **Camino de la imagen** (causa raíz del bug, con evidencia de n8n): localiza el/los
  nodo(s) que envían media a Chakra. ¿Cuántos caminos pueden disparar un envío de
  imagen en un mismo turno? ¿El texto y la imagen son ramas separadas que podrían
  ejecutarse ambas? ¿Hay reintentos configurados en ese nodo sin idempotencia?
  Cruza esto con la evidencia de duplicados en datos (sección 6).
- **Manejo del 409 (strategy_version stale)**: ¿n8n maneja el 409 de `/agent/action`?
  ¿Reintenta el ingest, o falla silenciosamente? (ADR-003 depende de esto; si n8n no
  lo maneja, la protección de contexto viejo no sirve en la práctica).
- **Fallas silenciosas**: ¿hay manejo de error / alerta si el backend responde 5xx o
  no responde? (DEUDA #3). Solo reportar si existe o no, no arreglar.

## 8. Plan de trabajo — priorizado, secuenciado por el north star

Con TODO lo anterior (archivos + DB viva + n8n vivo), produce un plan de trabajo para
**arreglar y ordenar lo que está roto o desalineado**. Reglas para el plan:

- **Es un plan de remediación, no de construcción de features nuevas.** Ordena los
  fixes de bugs reales y la sincronización de drift. NO incluyas como tareas las ideas
  del north star (resume asíncrono, llm=None, escenarios-como-datos) — esas NO se
  implementan en esta fase.
- **El north star entra como LENTE DE SECUENCIACIÓN, no como backlog.** Para cada fix,
  pregunta: "¿este arreglo es coherente con hacia dónde vamos, o crea algo que después
  habrá que deshacer?". Ejemplo: el fix de la imagen debe hacerse como un caso del
  problema general de *idempotencia outbound* (alineado con el north star idea #1 de
  estado materializado), NO como un parche especial solo-para-imagen. Marca para cada
  fix si está "alineado / neutro / en tensión" con el north star, y por qué.
- **Clasifica cada ítem por costo y riesgo**:
  - 🟢 Texto puro / config (drift de docs): barato, reversible, sin riesgo de prod.
  - 🟡 Código acotado con test (bugs de datos sin tocar schema).
  - 🔴 Toca schema, auth, o el hot path: requiere su propio ADR ANTES de tocar nada.
- **Ordena por**: (1) lo que desbloquea o protege producción primero, (2) costo
  ascendente dentro del mismo nivel de riesgo, (3) dependencias (qué fix habilita cuál).
- **Para cada ítem del plan**, da: qué se arregla, evidencia (archivo:línea / query /
  nodo), tipo (🟢/🟡/🔴), alineación con north star, y si necesita ADR previo.
- **Marca explícitamente qué requiere un ADR** antes de ejecutarse (mínimo esperado:
  idempotencia outbound). NO escribas el ADR aquí; solo señala que hace falta.

El plan debe terminar con una **secuencia recomendada de sesiones de Code**: qué hacer
en la primera sesión (lo 🟢 de cero riesgo), qué en la segunda (🟡 con tests), y qué
queda bloqueado tras decisión de ADR (🔴). Una cosa a la vez, con commit y verificación
entre cada una.

---

## Cómo reportar

Evidencia siempre (archivo:línea, query SELECT, o nodo de n8n). PII enmascarada.
Distingue lo confirmado contra el sistema vivo de lo inferido desde archivos. Donde el
sistema vivo CONTRADIGA al primer reporte, dilo explícitamente — el sistema vivo gana.

Este pase termina en un PLAN, no en cambios. La ejecución es una sesión posterior,
fix por fix, con la regla de "entender y decidir antes de tocar" intacta.
