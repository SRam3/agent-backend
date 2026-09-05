# Architecture Decision Records (ADRs)

Registro append-only de decisiones arquitectónicas del proyecto. Cada ADR documenta una decisión tomada, su contexto, las alternativas consideradas, y sus consecuencias.

## Reglas

1. **Append-only**: un ADR es inmutable una vez `Accepted`, **salvo un bloque final «Notas as-built»** que puede añadirse para registrar lo aprendido durante la implementación (qué se desvió del plan, qué se descubrió al construirlo). El cuerpo de la decisión —contexto, decisión, alternativas, consecuencias— no se reescribe. Si la decisión cambia, se escribe un ADR nuevo que "supersede" al anterior.
2. **Numeración secuencial**: `ADR-NNN-titulo-corto.md`, sin saltos.
3. **Naming**: kebab-case, descriptivo pero corto (≤ 6 palabras).
4. **Estatus posibles**: `Accepted` (vigente), `Superseded by ADR-NNN` (reemplazado), `Deprecated` (obsoleto sin reemplazo).
5. **Fecha**: la del día en que se acepta la decisión, no la del día en que se documenta.

## Cuándo escribir un ADR

Test rápido: **¿alguien dentro de 6 meses preguntará "por qué hicimos esto así"?** Si la respuesta es sí, ADR.

Casos típicos:
- Adopción o abandono de una tecnología (librería, framework, paradigma)
- Decisión de schema con consecuencias multi-tabla
- Patrón de seguridad o auth
- Drop de tabla, columna o feature significativa
- Trade-off costo/latencia/complejidad explícitamente discutido
- Decisión que va contra la convención por una razón específica

Casos donde NO hace falta ADR:
- Refactor menor sin cambio semántico
- Bug fix
- Ajuste de prompt
- Cambio de copy en mensajes

## Plantilla

```markdown
# ADR-NNN — Título corto en sentence case

- **Estatus**: Accepted | Superseded by ADR-MMM | Deprecated
- **Fecha**: YYYY-MM-DD
- **Decididores**: nombres o roles

## Contexto

¿Qué problema estábamos resolviendo? ¿Qué fuerzas estaban en juego?
Estado del sistema en ese momento, restricciones, tensiones técnicas o de negocio.

## Decisión

Qué decidimos hacer, en una o dos oraciones claras.

## Alternativas consideradas

- **Alternativa A**: descripción + por qué la descartamos
- **Alternativa B**: descripción + por qué la descartamos
- **Alternativa C**: ...

## Consecuencias

### Positivas
- ...

### Negativas
- ...

### Neutras o trade-offs explícitos
- ...

## Cuándo revisar

Condición o métrica que dispararía revisar la decisión.
Ej: "Cuando tengamos 3+ clientes con catálogo > 50 SKUs."
```

## Índice de ADRs

| # | Título | Estatus | Fecha |
|---|--------|---------|-------|
| 001 | DAG sobre algoritmos de búsqueda | Accepted | 2026-04-03 |
| 002 | Patrón de dos llamadas sin tools | Accepted | 2026-04-03 |
| 003 | strategy_version contra contexto viejo | Accepted | 2026-04-10 |
| 004 | Drop de leads, orders, order_line_items | Accepted | 2026-04-21 |
| 005 | Perfil persistente en client_users | Accepted | 2026-04-21 |
| 006 | VARCHAR + CHECK sobre ENUMs nativos | Accepted | 2026-04-03 |
| 007 | Colapso de state machine de 7 a 3 estados | Accepted | 2026-04-19 |
| 008 | Soporte multiidioma e internacional | Accepted | 2026-06-16 |
| 009 | Cierre del lazo de handoff humano | Accepted | 2026-07-19 |
| 010 | El backend gobierna el resumen del pedido | Accepted | 2026-08-23 |
| 011 | En qué reloj vive cada decisión temporal | Propuesto | 2026-09-01 |
| 012 | Cada migración declara su orden vs. el despliegue | Accepted | 2026-09-01 |
| 013 | El operador es autor; sus echoes son contexto, nunca hecho | Accepted | 2026-09-05 |

> **El 010 pasó a `Accepted` el 2026-09-04**: la rama `feat/adr-010-backend-gobierna-resumen` se
> mergeó (PR #68) y la revisión `--0000058` lo desplegó.
>
> **El 012 pasó a `Accepted` el 2026-09-04**, ejercido con éxito en su primer caso real: la
> migración 013 se partió en DDL (antes del despliegue) y datos+prompt (014, después), y ambas
> mitades entraron en el orden que el ADR prescribe.
>
> **El 011 sigue en `Propuesto`**: aún no se ha ejercido. Su único entregable —el microfix del
> mismo segundo, `created_at >= msg_timestamp AND id != message.id`— sigue sin dueño; el sitio
> natural es la sesión de n8n de P29 fase 4, junto a P9.
>
> **El 010 ya no está reservado para P10.** Este índice lo reservaba para el frente P10 (detección de
> conversaciones no-humanas), cuyo ADR nunca se escribió. Se aplica el precedente de ADR-008: **el ADR
> que efectivamente se escribe toma el número**. P10 tomará el siguiente libre cuando su decisión se
> escriba; hoy su diseño vive en `docs/ROADMAP.md`.
>
> **Siguiente número libre: 014.**
