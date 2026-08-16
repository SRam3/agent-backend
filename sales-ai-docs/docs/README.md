# Documentación — Sales AI Agent

Esta carpeta contiene documentación versionada del proyecto. **Lo que NO está aquí pero sí se usa**: `CLAUDE.md` en la raíz del repo (gitignored, contexto operacional para Claude Code).

---

## Cómo navegar

| Si buscas... | Ve a... |
|---|---|
| Estado actual del sistema (qué es esto hoy) | `../CLAUDE.md` |
| **Qué sigue / en qué se está trabajando** | **`ROADMAP.md`** |
| Por qué decidimos algo | `decisions/` (ADRs) |
| Cómo está pensada la arquitectura | `architecture/overview.md` |
| Definiciones de términos del dominio | `architecture/glossary.md` |
| Qué pasó en un incidente y qué aprendimos | `postmortems/` |
| Limitaciones conocidas de algo ya entregado | `registros/` |

---

## Filosofía de documentación

**Tres tipos de documentos, tres propósitos**:

1. **Operacional** (`CLAUDE.md`): qué es el sistema HOY. Vive, se actualiza, no tiene historia.
2. **Histórico** (`decisions/`): por qué tomamos cada decisión. Append-only, fechado, inmutable.
3. **Conceptual** (`architecture/`): cómo está pensado el sistema. Cambia raramente.

**Triggers de actualización**:

- Cada migración de DB → revisar `CLAUDE.md` (sección de schema)
- Cada drop de funcionalidad → actualizar `CLAUDE.md` + posible ADR
- Cada decisión que en 6 meses alguien preguntará "por qué" → ADR nuevo
- Cada incidente con impacto en usuario → postmortem

---

## Estándar de nomenclatura

Dos familias de documento, y la diferencia decide si el nombre lleva fecha.

### Documentos vivos — SIN fecha en el nombre

Representan el **estado actual**. Se editan cuando la realidad cambia; su historia la
guarda git, no el nombre del archivo. Poner fecha en un documento vivo lo hace parecer
caducado a los tres meses.

`ROADMAP.md` · `README.md` · `architecture/overview.md` · `architecture/glossary.md` ·
`architecture/north-star.md` · `decisions/README.md`

### Registros inmutables — CON fecha del evento

Capturan **qué pasó una vez**. No se editan (salvo arreglar un link roto). La fecha del
nombre es **la del evento**, no la del día en que se escribió el documento.

```
<tipo>-YYYY-MM-DD-<slug>.md
```

| Tipo | Para qué | Ejemplo |
|---|---|---|
| `postmortem-` | Incidente real con impacto | `postmortem-2026-07-21-n8n-scale-to-zero.md` |
| `diagnostico-` | Investigación acotada de una causa | `diagnostico-2026-07-15-slot-perdido.md` |
| `auditoria-` | Revisión amplia del estado | `auditoria-2026-06-14-estado-y-plan.md` |
| `analisis-` | Estudio de un caso (incluye éxitos) | `analisis-2026-07-15-primera-venta.md` |

### Briefs — el encargo de una sesión de trabajo

```
brief-<tipo>-<ID>-<slug>.md      tipo ∈ impl | audit | analisis
```

Llevan el **ID del frente** cuando existe (`brief-impl-P8-circuit-breaker.md`,
`brief-impl-ADR-008-idioma-telefono.md`); si el brief precede a cualquier frente, llevan
fecha en su lugar (`brief-audit-2026-06-14-estado-y-plan.md`).

### ADRs y registros

- `ADR-NNN-titulo-kebab.md` — numeración secuencial sin saltos, ≤6 palabras. Ver
  `decisions/README.md`.
- `registro-<ID>-<slug>.md` en `registros/` — hechos duraderos sobre un frente que no son
  ni decisión ni incidente (p. ej. limitaciones conocidas de algo ya entregado).

### Notación de identificadores

`P<n>` = frente de trabajo · `DEUDA #<n>` = problema observado · `ADR-<nnn>` = decisión.
Nunca intercambiables, nunca con slash (`deuda #2/P7`), números nunca reciclados,
sin zero-padding, `P` mayúscula. El registro canónico de frentes vive en `ROADMAP.md`.

---

## Estado actual de docs

```
docs/
├── README.md                   ← este archivo
├── ROADMAP.md                  ← qué sigue + registro canónico de frentes P
├── architecture/
│   ├── overview.md
│   ├── glossary.md
│   └── north-star.md           ← dirección a futuro, NO implementar aún
├── decisions/
│   ├── README.md               ← índice de ADRs + plantilla
│   ├── ADR-001-dag-over-search.md
│   ├── ADR-002-no-tools-pattern.md
│   ├── ADR-003-strategy-version.md
│   ├── ADR-004-drop-leads-orders.md
│   ├── ADR-005-persistent-profile.md
│   ├── ADR-006-varchar-check-over-enums.md
│   ├── ADR-007-state-machine-collapse.md
│   ├── ADR-008-idioma-y-telefono-e164.md
│   └── ADR-009-handoff-closure-loop.md
├── briefs/
│   ├── brief-audit-2026-06-14-estado-y-plan.md
│   ├── brief-analisis-2026-07-15-primera-venta.md
│   ├── brief-impl-P2-P4-order-fields-compaction.md
│   ├── brief-impl-P3-payment-gate.md
│   ├── brief-impl-P8-circuit-breaker.md
│   ├── brief-impl-P11-fix-venta-duplicada.md
│   ├── brief-impl-P12-order-fields-directive.md
│   └── brief-impl-ADR-008-idioma-telefono.md
├── registros/
│   └── registro-P8-limitaciones.md
└── postmortems/
    ├── auditoria-2026-06-14-estado-y-plan.md
    ├── diagnostico-2026-06-14-P4-compaction.md
    ├── postmortem-2026-06-16-stand-publico.md
    ├── analisis-2026-07-15-primera-venta.md
    ├── diagnostico-2026-07-15-slot-perdido.md
    ├── postmortem-2026-07-21-n8n-scale-to-zero.md
    └── diagnostico-2026-08-07-notacion-y-nombres.md
```

> `postmortems/` guarda los cuatro tipos de registro inmutable, no solo postmortems; el
> prefijo del archivo dice cuál es. Renombrar la carpeta se evaluó y se descartó: rompería
> más links de los que aclara.
