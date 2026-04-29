# Documentación — Sales AI Agent

Esta carpeta contiene documentación versionada del proyecto. **Lo que NO está aquí pero sí se usa**: `CLAUDE.md` en la raíz del repo (gitignored, contexto operacional para Claude Code).

---

## Cómo navegar

| Si buscas... | Ve a... |
|---|---|
| Estado actual del sistema (qué es esto hoy) | `../CLAUDE.md` |
| Por qué decidimos algo | `decisions/` (ADRs) |
| Cómo está pensada la arquitectura | `architecture/overview.md` |
| Definiciones de términos del dominio | `architecture/glossary.md` |
| Cómo desplegar, hacer rollback, debugear | `runbooks/` |
| Qué pasó en un incidente y qué aprendimos | `postmortems/` |

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

## Estado actual de docs

```
docs/
├── README.md                   ← este archivo
├── architecture/
│   ├── overview.md             ← (pendiente)
│   └── glossary.md             ← (pendiente)
├── decisions/
│   ├── README.md               ← índice de ADRs + plantilla
│   ├── ADR-001-dag-over-search.md
│   ├── ADR-002-no-tools-pattern.md
│   ├── ADR-003-strategy-version.md
│   ├── ADR-004-drop-leads-orders.md
│   ├── ADR-005-persistent-profile.md
│   ├── ADR-006-varchar-check-over-enums.md
│   └── ADR-007-state-machine-collapse.md
├── runbooks/                   ← vacío hoy, llenar cuando aparezca necesidad
└── postmortems/                ← vacío hoy
```
