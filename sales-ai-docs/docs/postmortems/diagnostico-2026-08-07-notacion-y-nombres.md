# Diagnóstico — Notación P y convenciones de nombres de archivo

**Fecha**: 2026-08-07 · **Modo**: READ-ONLY estricto (inventario, cero renombres, cero ediciones)
**Objetivo**: entender el estado real de la notación `P` y de los nombres de archivo en `docs/`
antes de unificar, para que "P7" o el nombre de un archivo no tengan dos lecturas posibles.
**Estatus de la propuesta (§4)**: **NO aplicada**. Requiere decisión explícita.

**Convención de evidencia**: `archivo:línea` = leído del repo. Rutas relativas a `agent-backend/`.

> **Nota de método**: el `grep` del shell de trabajo respeta `.gitignore`, y `CLAUDE.md` está
> gitignored. Los barridos se rehicieron forzando `/usr/bin/grep` sobre todos los archivos
> (`*.md`, `*.py`, `*.sql`, `*.json`, `*.yml`), así que los conteos de abajo sí lo incluyen.
> Cualquier auditoría futura de este repo debe tener el mismo cuidado.

---

## 1. Notación P — lista canónica

**Fuente definicional única**: `docs/postmortems/audit-2026-06-14.md` §8 "Plan de trabajo
priorizado" (líneas 106–146). El bloque "Estado del plan de remediación" de `CLAUDE.md:266-279`
es el **espejo de estado**, no la definición.

**Existen P1–P9. No hay huecos. No existe P10 ni superior.** (Barrido completo: `P1`×4, `P2`×35,
`P3`×21, `P4`×28, `P5`×6, `P6`×6, `P7`×5, `P8`×28, `P9`×6.)

| P | Significado | Estado hoy | Definido en |
|---|---|---|---|
| **P1** | Sincronizar documentación con la realidad (4 archivos + exports n8n) | ✅ | `audit-2026-06-14.md:106` |
| **P2** | Persistir `quantity`/`grind_preference`/`roast_preference` | ✅ | `audit-2026-06-14.md:111` |
| **P3** | Cerrar el gate permeable de `payment_confirmation` | ✅ → **superado** por ADR-009 | `audit-2026-06-14.md:116` |
| **P4** | Resucitar la lazy-compaction + hacerla ruidosa | 🟡 causa raíz pendiente | `audit-2026-06-14.md:121` |
| **P5** | Manejo de errores en n8n: 409, 5xx y alerta | ⬜ | `audit-2026-06-14.md:126` |
| **P6** | Idempotencia outbound (texto e imagen) — requiere ADR | ⬜ | `audit-2026-06-14.md:131` |
| **P7** | Debounce: race + conexión ocupada — requiere ADR | ⬜ | `audit-2026-06-14.md:136` |
| **P8** | Circuit breaker para loops conversacionales | ✅ | `audit-2026-06-14.md:141` |
| **P9** | Microfixes n8n (`latency_ms` real, `slice(-10)`→20) | ⬜ | `audit-2026-06-14.md:146` |

Sí hay P1, y está cerrado. Sin huecos en la numeración.

### 1.1 Colisiones confirmadas

#### 🔴 P9 designa dos cosas distintas

- **P9(a) = microfixes n8n** — definición original en `audit-2026-06-14.md:146`; sigue pendiente
  en `CLAUDE.md:276` (`⬜ P5 + P9`).
- **P9(b) = detector de loop de texto variable** — `docs/briefs/p8-limitaciones-conocidas.md:31`
  (`## 2. Loop de texto variable NO cubierto (candidato P9)`) y `:42`
  (`Registrado como candidato P9. No mezclar con P8.`).

Quien escribió P9(b) no vio que P9 ya estaba tomado. Además **ese mismo frente tiene un segundo
número**: `CLAUDE.md:262` y `:272` lo trackean como `deuda #10`. Un frente, dos números, y uno de
esos números ya ocupado por otra cosa.

**Es el único hallazgo con daño potencial inmediato**: hay trabajo pendiente bajo "P9" en
`CLAUDE.md` y trabajo pendiente *distinto* bajo "candidato P9" en el registro de P8. Decir
"hagamos P9" hoy es ambiguo.

#### 🟠 P4 designa dos cosas, en dos generaciones de numeración

- **P4(abril)** = edits quirúrgicos al prompt para reducir tono robótico.
  `migrations/versions/006_p4_reduce_robotic_tone.sql:1`, rama `prompt/p4-reduce-robotic-tone`,
  commit `prompt(006): reduce robotic tone — P4 surgical edits`, **2026-04-20**.
- **P4(audit)** = lazy-compaction rota, **2026-06-14**.

Dos meses de distancia, cero relación semántica, y nada en el repo marca que son numeraciones
distintas.

#### 🟢 P3 superado — el único caso bien marcado

`CLAUDE.md:269`, `sales_agent_api/app/services/agent_action.py:168`,
`docs/decisions/ADR-009-handoff-closure-loop.md:171` y `tests/services/test_agent_action.py:172-174`
dicen todos lo mismo y son coherentes entre sí. Es el modelo a imitar para futuras supersesiones.

---

## 2. Consistencia de notación

### 2.0 No existe esquema F

El único `F<número>` del repo es `F811` (código de flake8) en `tests/test_health.py:24`.
**No hay nada que migrar desde una notación F.**

### 2.1 `DEUDA #N` (1–12) — registro paralelo

Definido en `CLAUDE.md:249-264`. 29 referencias en docs, tests y código de aplicación.

La relación conceptual con P **está bien pensada y conviene conservarla**: DEUDA = problema
observado; P = frente de trabajo que lo remedia. `CLAUDE.md` incluso hace el mapeo explícito
(`Fix = P7` en #2, `Fix = P6` en #11, `Fix = P5 (+P9)` en #12).

Los problemas son de escritura, no de concepto:

- `docs/briefs/analisis-primera-venta-brief.md:40` escribe **`deuda #7/P4`**, con slash, como si
  fueran sinónimos intercambiables. No lo son: #7 es el síntoma, P4 es el trabajo.
- **DEUDA #10 también está reusado**, igual que P9:
  - Sentido viejo: *"reset por idle 30 min — describe código ficticio"* →
    `docs/briefs/audit-brief.md:39`, `docs/postmortems/audit-2026-06-14.md:24`.
  - Sentido nuevo: *"corte n8n / loop de texto variable"* → `CLAUDE.md:262`,
    `docs/postmortems/hallazgos-primera-venta.md:48`.

  La tabla se renumeró en algún punto sin dejar marca. Un lector de los docs de junio y uno de los
  de julio entienden cosas distintas por "#10".

### 2.2 `ADR-NNN` usado como ID de frente de trabajo

La lista de remediación de `CLAUDE.md:267-279` mezcla **cuatro tipos de identificador en un solo eje**:

```
✅ P1 — docs backend sincronizados          ← P
✅ ADR-008 — idioma en vivo + phone gate     ← ID de decisión usado como ID de trabajo
🟡 Fix venta duplicada (2026-07-31, ...)     ← nombre largo, sin ID
🟡 Diagnóstico slot perdido (PR #53)         ← nombre largo, sin ID
⬜ Stand 2026-06-16 sin cerrar               ← nombre de evento
```

### 2.3 Frentes vivos sin número

Tres frentes se referencian solo por nombre largo, y cada uno tiene brief propio:
**Fix venta duplicada**, **Diagnóstico slot perdido / ORDER_FIELDS directive**, y
**conocimiento curado de café (stand 2026-06-16)**.

### 2.4 Mayúscula vs minúscula

La prosa siempre escribe `P8`. Los artefactos siempre `p8`:
`docs/briefs/p8-limitaciones-conocidas.md`, `docs/postmortems/p4-compaction-diagnosis-2026-06-14.md`,
`migrations/versions/006_p4_reduce_robotic_tone.sql`, ramas `feat/p8-circuit-breaker` y
`prompt/p4-reduce-robotic-tone`.

---

## 3. Nombres de archivo

### 3.1 ADRs — la convención existe y casi se cumple

Regla declarada en `docs/decisions/README.md`: `ADR-NNN-titulo-corto.md`, kebab-case, ≤6 palabras,
numeración secuencial sin saltos.

✅ **Numeración correcta**: 001–009, sin saltos, y **el número del archivo coincide con el H1 de su
contenido en los 9 casos** (verificado uno por uno).

| Problema | Detalle |
|---|---|
| 🔴 `ADR-008.md` sin slug | Único que rompe `ADR-NNN-titulo`. Su H1 es "Soporte multiidioma e internacional (clientes extranjeros)" |
| 🔴 Índice desactualizado | La tabla de `docs/decisions/README.md` lista **solo 001–007**. Faltan 008 y 009 |
| 🟠 Vocabulario de estatus mixto | Regla 4 declara `Accepted`/`Superseded by ADR-NNN`/`Deprecated`. ADR-001..007 usan `Accepted`; **ADR-008 y ADR-009 usan `Aceptado`** |
| 🟠 Estatus con carga extra | ADR-009: `Aceptado — implementado (PR #54, e2e 2026-07-20). Ver "Notas as-built"` — mete estado de implementación en el campo de estatus |
| 🟠 Regla 1 (append-only) rota en la práctica | ADR-008: `Fecha: 2026-06-16 (revisado 2026-06-24)`; ADR-009 tiene "Notas as-built" añadidas. La regla dice inmutable; la práctica es editar |
| 🟠 Ningún ADR con `Superseded by` | El gate P3 fue superado por ADR-009, pero ningún ADR lo registra en su estatus |

### 3.2 Briefs — sin convención declarada; 4 patrones conviviendo

| Archivo | Patrón |
|---|---|
| `impl-brief-P2-P4.md`, `impl-brief-P3.md`, `impl-brief-P8.md` | `impl-brief-<P mayúscula>` |
| `impl-brief-adr008.md` | `impl-brief-<adr minúscula, sin guion>` — no coincide con `ADR-008` |
| `impl-brief-fix-venta-duplicada.md`, `impl-brief-order-fields-directive.md` | `impl-brief-<slug>`, **sin ID** |
| `analisis-primera-venta-brief.md` | sufijo `-brief` en vez de prefijo |
| `audit-brief.md` | sin tipo ni fecha |
| `p8-limitaciones-conocidas.md` | **no es un brief** — es un registro de limitaciones. Además `p8` minúscula |

### 3.3 Postmortems — sin convención, y mezcla tipos de documento

| Archivo | Tipo real | La fecha del nombre significa |
|---|---|---|
| `audit-2026-06-14.md` | **auditoría** | fecha de la sesión |
| `stand-2026-06-16.md` | postmortem | fecha del evento |
| `n8n-scale-to-zero-2026-07-21.md` | postmortem | fecha del evento |
| `p4-compaction-diagnosis-2026-06-14.md` | **diagnóstico** | fecha de la sesión |
| `diagnostico-slot-perdido-2026-07-15.md` | diagnóstico | fecha del **turno analizado** (el doc se fechó el 07-19) |
| `hallazgos-primera-venta.md` | **análisis de éxito** | **sin fecha** |

Encima: el idioma se mezcla (`diagnostico` vs `diagnosis`), `p4` va en minúscula, y la carpeta
contiene cuatro tipos de documento bajo un nombre que promete uno solo.

### 3.4 Otros hallazgos de integridad

- 🔴 **Referencia rota**: `docs/postmortems/audit-2026-06-14.md:3` dice
  `**Brief**: docs/audit-brief.md`. La ruta real es `docs/briefs/audit-brief.md`.
  Es el único path `.md` roto en todo `docs/`.
- 🔴 **Árbol de docs obsoleto**: la sección "Estado actual de docs" de `docs/README.md` lista solo
  ADR-001..007, dice `postmortems/ ← vacío hoy` (tiene 6 archivos), **no menciona `briefs/`**, y
  lista `runbooks/` que **no existe en disco**.
- 🟢 Sin duplicados de contenido ni archivos huérfanos con nombres viejos.
- 🟢 Migraciones 001–011: numeración limpia, sin saltos.

---

## 4. Propuesta (NO aplicada — requiere decisión)

### 4.1 Notación P

**Regla única de tres ejes.** `P<n>` = **frente de trabajo**. `DEUDA #<n>` = **problema observado**.
`ADR-<nnn>` = **decisión**. Nunca intercambiables. Se escribe `P4 (remedia deuda #7)`,
**nunca** `deuda #7/P4`.

**Resolver P9**: conservar **P9 = microfixes n8n** (definición original, en la fuente canónica, y
sigue pendiente). Reasignar el detector de loop de texto variable a **P10**.

**P4 de abril**: *no renombrar*. La migración 006 está aplicada en prod y el commit está mergeado;
renombrar es riesgo por cero beneficio. En su lugar, una línea en el registro canónico:
> `006_p4_reduce_robotic_tone.sql` y la rama `prompt/p4-reduce-robotic-tone` (abril 2026) usan una
> numeración anterior; ese P4 no tiene relación con P4 (compaction).

**DEUDA #10**: no renumerar (los docs de junio son inmutables). Añadir nota histórica en la fila #10:
> Antes de 2026-06-14, #10 designaba el "reset por idle 30 min" documentado pero inexistente;
> cerrado por P1.

Y regla nueva: **un número de deuda retirado nunca se reutiliza**.

**Asignar P a los frentes huérfanos**, para que la lista de estado tenga un solo eje de ID:

| Nuevo | Frente | Cómo se llama hoy |
|---|---|---|
| **P10** | Detector de estancamiento del DAG (loop de texto variable) | "candidato P9" + `deuda #10` |
| **P11** | Fix venta duplicada — operador única autoridad del pago | nombre largo |
| **P12** | Captura oportunista de ORDER_FIELDS en el directive | nombre largo |
| **P13** | Conocimiento curado de café en el prompt | "Stand 2026-06-16 sin cerrar" |

**Frentes nuevos empiezan en P14.**

**Mover el registro canónico a `CLAUDE.md`** (que ya es el espejo de estado), dejando el §8 del
audit como lo que es: el documento histórico donde nacieron P1–P9. Hoy la fuente definicional está
enterrada en un postmortem inmutable — que es exactamente por qué P9 se duplicó.

**Sin zero-padding** (`P4`, `P10`; no `P04`). La prosa de los docs inmutables ya dice `P2`..`P9`;
padear obligaría a editarlos o a crear una segunda inconsistencia. Costo aceptado: `ls` ordenará
`P10` antes de `P2`.

**Mayúscula `P`** en prosa y nombres de archivo. Las ramas git siguen en minúscula
(`feat/p10-dag-stall-detector`) como excepción documentada: es convención git y no genera ambigüedad.

### 4.2 ADRs

| Renombrar | A |
|---|---|
| `ADR-008.md` | `ADR-008-idioma-y-telefono-e164.md` |

Sin renombrar, además:
- Normalizar `Aceptado` → `Accepted` en ADR-008 y ADR-009.
- Mover el detalle de implementación de ADR-009 a una línea `**Implementación**:` aparte.
- **Añadir 008 y 009 al índice** de `docs/decisions/README.md`.
- Decidir explícitamente si la regla 1 (append-only) se relaja a *"append-only salvo un bloque final
  «Notas as-built»"* — hoy la práctica ya la contradice.

### 4.3 Briefs

Esquema `brief-<tipo>-<ID>-<slug>.md`, con tipo ∈ `impl` | `audit` | `analisis`:

| Actual | Propuesto |
|---|---|
| `impl-brief-P2-P4.md` | `brief-impl-P2-P4-order-fields-compaction.md` |
| `impl-brief-P3.md` | `brief-impl-P3-payment-gate.md` |
| `impl-brief-P8.md` | `brief-impl-P8-circuit-breaker.md` |
| `impl-brief-adr008.md` | `brief-impl-ADR-008-idioma-telefono.md` |
| `impl-brief-fix-venta-duplicada.md` | `brief-impl-P11-fix-venta-duplicada.md` |
| `impl-brief-order-fields-directive.md` | `brief-impl-P12-order-fields-directive.md` |
| `audit-brief.md` | `brief-audit-2026-06-14-estado-y-plan.md` |
| `analisis-primera-venta-brief.md` | `brief-analisis-2026-07-15-primera-venta.md` |
| `p8-limitaciones-conocidas.md` | **sacar de `briefs/`** → `docs/registros/registro-P8-limitaciones.md` |

### 4.4 Postmortems

Esquema `<tipo>-YYYY-MM-DD-<slug>.md`, fecha = **siempre la del evento**,
tipo ∈ `postmortem` | `diagnostico` | `auditoria` | `analisis`:

| Actual | Propuesto |
|---|---|
| `audit-2026-06-14.md` | `auditoria-2026-06-14-estado-y-plan.md` |
| `stand-2026-06-16.md` | `postmortem-2026-06-16-stand-publico.md` |
| `n8n-scale-to-zero-2026-07-21.md` | `postmortem-2026-07-21-n8n-scale-to-zero.md` |
| `p4-compaction-diagnosis-2026-06-14.md` | `diagnostico-2026-06-14-P4-compaction.md` |
| `diagnostico-slot-perdido-2026-07-15.md` | `diagnostico-2026-07-15-slot-perdido.md` |
| `hallazgos-primera-venta.md` | `analisis-2026-07-15-primera-venta.md` |
| *(este documento)* | `diagnostico-2026-08-07-notacion-y-nombres.md` |

Con el prefijo de tipo, la carpeta puede seguir llamándose `postmortems/` sin mentir, o renombrarse
a `docs/registros/`. Recomendación: dejarla como está y que el prefijo haga el trabajo.

### 4.5 Costo de los renombres — bajo

Referencias entrantes medidas por archivo (la mayoría 0–2). Habría que actualizar:
`audit-brief.md` (2), `stand-2026-06-16.md` (2), y una cada uno en `impl-brief-P8.md`,
`impl-brief-fix-venta-duplicada.md`, `impl-brief-order-fields-directive.md`,
`n8n-scale-to-zero-2026-07-21.md`, `hallazgos-primera-venta.md`, `analisis-primera-venta-brief.md`.

Renombrar archivos no viola la regla de inmutabilidad (que es sobre contenido), pero sí obliga a
tocar docs marcados "no se edita" para arreglar sus links. Decidirlo explícitamente antes de empezar.

### 4.6 Dos arreglos independientes, aplicables ya

Son bugs puros, no dependen de aprobar el esquema:

1. Path roto: `docs/audit-brief.md` → `docs/briefs/audit-brief.md` en `audit-2026-06-14.md:3`.
2. Árbol obsoleto de `docs/README.md`: ADRs faltantes, `postmortems/` marcado vacío, `briefs/`
   ausente, `runbooks/` fantasma.

---

## 5. Si solo se arregla una cosa

**La colisión de P9.** Es el único hallazgo que puede causar daño mañana: hay trabajo pendiente
bajo `P9` en `CLAUDE.md` y trabajo pendiente distinto bajo "candidato P9" en
`p8-limitaciones-conocidas.md`. Se arregla con una línea. Todo lo demás es higiene.
