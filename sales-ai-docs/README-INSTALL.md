# Documentación de Sales AI Agent — paquete inicial

Este paquete contiene la documentación inicial del proyecto. Está organizado para copiarse directamente sobre tu repositorio local manteniendo la estructura de directorios.

---

## Contenido

```
.
├── README-INSTALL.md                ← este archivo (no copiar al repo)
├── .gitignore-additions             ← líneas a agregar al .gitignore (no copiar tal cual)
│
├── CLAUDE.md                        ← copiar a la raíz del repo (gitignored)
│
└── docs/                            ← copiar tal cual al repo (committed)
    ├── README.md
    ├── architecture/
    │   ├── overview.md
    │   └── glossary.md
    └── decisions/
        ├── README.md
        ├── ADR-001-dag-over-search.md
        ├── ADR-002-no-tools-pattern.md
        ├── ADR-003-strategy-version.md
        ├── ADR-004-drop-leads-orders.md
        ├── ADR-005-persistent-profile.md
        ├── ADR-006-varchar-check-over-enums.md
        └── ADR-007-state-machine-collapse.md
```

---

## Instalación en tu repo local

Suponiendo que descomprimiste este paquete en `~/Downloads/sales-ai-docs/` y tu repo está en `~/code/agent-backend/`:

```bash
cd ~/code/agent-backend

# 1. Copiar CLAUDE.md a la raíz del repo
cp ~/Downloads/sales-ai-docs/CLAUDE.md .

# 2. Copiar la carpeta docs/ tal cual
cp -r ~/Downloads/sales-ai-docs/docs .

# 3. Agregar líneas al .gitignore (NO reemplazar — solo añadir)
cat ~/Downloads/sales-ai-docs/.gitignore-additions >> .gitignore

# 4. Verificar que git NO ve CLAUDE.md
git status
# debe mostrar:
#   modified:   .gitignore
#   new file:   docs/README.md
#   new file:   docs/architecture/...
#   ... (pero NO CLAUDE.md)

# 5. Commitear todo MENOS CLAUDE.md
git add .gitignore docs/
git commit -m "docs: initial documentation package — ADRs, overview, glossary"

# 6. Confirmar que CLAUDE.md vive solo en local
ls -la CLAUDE.md   # debe existir
git log -- CLAUDE.md   # debe estar vacío (nunca committed)
```

---

## Cómo usar esto día a día

**Cuando Claude Code arranque una sesión**, va a leer `CLAUDE.md` automáticamente. Es contexto operacional vivo: reflejará siempre el estado actual del sistema.

**Cuando escribas código nuevo o tomes decisiones**, segui estos triggers:

1. **Migración de DB nueva** → revisar `CLAUDE.md`, sección de schema, actualizar si hace falta.
2. **Decisión arquitectónica importante** → escribir un ADR nuevo. Numerado secuencial.
3. **Drop de funcionalidad o tabla** → actualizar `CLAUDE.md` + posible ADR.
4. **Incidente con impacto a usuario** → escribir postmortem en `docs/postmortems/`.

**Cuando alguien nuevo entre al proyecto**:
1. Leer `docs/architecture/overview.md` (15 minutos)
2. Hojear `docs/decisions/` por orden cronológico (30-60 minutos)
3. Trabajar con `CLAUDE.md` abierto al lado del código

---

## Plantilla para ADR nuevo

Cuando tomes una decisión nueva, copia el template de `docs/decisions/README.md`. El próximo ADR sería **ADR-008**.

Casos esperados pronto:
- ADR-008: tabla `purchase_intents` para preservar venta entre conversaciones
- ADR-009: structured output con Pydantic + response_format
- ADR-010: front de operador como producto separado

---

## Si algo se siente mal

La documentación es viva. Si encontrás que CLAUDE.md tiene algo desactualizado, arreglalo en el momento. Si un ADR vieje quedó superado, **NO lo borres** — escribí uno nuevo que lo "supersede". El historial inmutable es parte del valor.
