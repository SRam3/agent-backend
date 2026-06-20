# Norte conceptual — hacia dónde apuntamos (NO implementar todavía)

> **Qué es esto**: contexto de DIRECCIÓN para entender hacia dónde queremos llevar el
> Sales AI Agent. Son ideas validadas externamente por un accelerator de Databricks
> (banking-agent-accelerator) que llegó de forma independiente a la misma tesis que
> nosotros.
>
> **Qué NO es esto**: un plan de implementación. NADA de este archivo se construye
> ahora. Estamos en fase de **entender el estado actual** (ver `docs/audit-brief.md`).
> Este archivo existe para que, al auditar, se entienda el norte — y opcionalmente se
> note qué tan lejos/cerca está el código actual de él, SIN cambiarlo.
>
> **Regla para Claude Code**: tratar este archivo como solo-lectura-de-contexto. No
> implementar, no refactorizar hacia esto, no crear migraciones. Si al auditar notas
> brechas respecto a este norte, repórtalas como observación, no como tarea.

---

## La tesis que ya compartimos (validación, no novedad)

Un equipo de Databricks construyó un "Deterministic Stateful Agent with Async
Human-in-the-Loop" para banca, y llegó a la MISMA frontera que nosotros:

- el LLM hace SOLO lenguaje (clasificar intención, extraer campos, redactar)
- una máquina determinista controla TODO el flujo (el routing nunca lo decide el LLM)
- Postgres es el checkpoint persistente / fuente de verdad
- el human-in-the-loop es ciudadano de primera clase

Esto es palabra por palabra nuestro "el LLM conversa, el backend gobierna" (ADR-001,
ADR-002). La conclusión importante: **nuestra arquitectura está validada de forma
independiente.** No necesitamos rediseñarla. El norte es endurecer uniones, no
reconstruir.

---

## Las 3 ideas del accelerator que SÍ queremos adoptar (a futuro)

### 1. Resume asíncrono basado en checkpoint (la más valiosa)

**Qué hacen ellos**: cuando un paso lento bloquea (un background check), el grafo se
PAUSA en un estado de espera explícito (`WAITING_FOR_BACKGROUND_CHECK`), sale a END, y
NO mantiene nada abierto. El resultado entra después por un endpoint que escribe el
checkpoint en Postgres (`graph.aupdate_state(...)`) y **retorna sin llamar al LLM**.
El siguiente mensaje del usuario reanuda transparentemente.

**Por qué nos importa**: ataca de raíz nuestro riesgo #1 — contexto viejo entre Call 1
y Call 2 (ADR-003). Hoy lo mitigamos REACTIVAMENTE con `strategy_version` (409 si
stale). El patrón de ellos es estructuralmente inmune: no hay ventana sostenida por una
llamada en vuelo; el estado se materializa en el checkpoint y cualquier actor (usuario,
operador, sistema externo) converge sobre la misma verdad sin coordinación temporal
frágil.

**Cómo encaja en lo nuestro**: nuestro `human_handoff` (hoy casi terminal) se
convertiría en un estado de espera con resume por checkpoint, análogo a su
`WAITING_FOR_BACKGROUND_CHECK`. Mantendríamos `strategy_version` como red de seguridad,
pero dejaríamos de depender de que Call 1 y Call 2 estén "cerca" en el tiempo. Encaja
especialmente bien porque WhatsApp YA es turn-based y desconectado — el modelo
"pausar y reanudar en el siguiente mensaje" le queda natural.

> Esto, si se decide, sería su propio ADR. NO ahora.

### 2. Modo `llm=None` — el grafo entero corre sin LLM (objetivo de testeo)

**Qué hacen ellos**: `build_graph(llm=None)` corre el grafo COMPLETO con stubs
deterministas, sin red, sin API key, sin costo. Sus smoke tests ejercen el flujo
entero así.

**Por qué nos importa**: es exactamente lo que nos falta (DEUDA #1: cero tests de
integración). Hoy tenemos tests del DAG aislado; el norte es un motor end-to-end
ejecutable sin LLM, donde la clasificación de intención y la extracción de campos
tengan stubs deterministas (substring/regex/`key=value`) como fallback. Eso da tests
de la máquina completa (ingest → estrategia → validación → side effects) sin costo.

### 3. Escenarios de error como DATOS, no como ramas ad-hoc

**Qué hacen ellos**: cada stub recibe un `stub_scenario` (`happy_path`,
`ambiguous_email`, `send_failure`, `low_confidence`, `missing_fields`...). Forzar un
camino de error es pasar un dato, no escribir una rama especial.

**Por qué nos importa**: nosotros tenemos los equivalentes latentes (producto inválido
→ line item omitido, confirmación faltante → campos bloqueantes, gate rechaza slot).
El norte es CATALOGAR esos escenarios como datos de prueba, para ejercer cada rama de
error sin mocks. Endurece nuestra explicabilidad propuesta/aprobación.

---

## Lo que NO copiamos (diferencias deliberadas, ya decididas)

Para que Code no confunda "validado" con "idéntico":

- **NO migramos a LangGraph.** Ellos usan ese framework; nosotros tenemos nuestro
  two-call pattern sobre n8n (ADR-002) y nuestro DAG propio. Robamos el *vocabulario*
  de estado explícito, no el runtime. Nuestro DAG de dependencias no lineales
  (`blocked_by`) es más expresivo que su flujo lineal con re-ask.
- **Nuestro multi-tenant es real** (`client_id` en cada tabla, `business_rules` por
  cliente). El accelerator es single-tenant con stubs sintéticos. Aquí estamos
  ADELANTE, no atrás.
- **Nuestro canal es WhatsApp asíncrono** (vía Chakra), no web con streaming. Esto
  refuerza el punto 1: el resume por checkpoint encaja MEJOR en nuestro canal que en
  el de ellos.

---

## Resumen del norte en una frase

La arquitectura está validada; el trabajo a futuro es **endurecer las uniones
críticas** (resume por checkpoint en vez de solo `strategy_version` defensivo),
**poder testear el motor completo sin LLM**, y **catalogar los escenarios de error
como datos**. Todo aditivo, nada de rediseño. Pero primero: entender el estado actual.
