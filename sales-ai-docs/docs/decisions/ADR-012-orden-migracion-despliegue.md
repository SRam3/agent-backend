# ADR-012 — Cada migración declara si va antes o después del despliegue

- **Estatus**: Accepted (2026-09-04 — ejercido en su primer caso real: la 013 partida en DDL y la 014 de datos+prompt)
- **Fecha**: 2026-09-01
- **Decididores**: Sebastian + cofounder/principal architect
- **Origen**: auditoría del 2026-09-01 (`docs/postmortems/auditoria-2026-09-01-roadmap-y-planeacion.md` §2.2)
- **Frente**: P33

---

## Contexto

Las migraciones se aplican **a mano** (convención `-- Applied:` en cada `.sql`, sin tabla de
versiones en la DB) y el CI **despliega solo**: un push a `main` que toque `sales_agent_api/**`
construye la imagen y actualiza el Container App sin intervención. Entre esas dos cosas hay un
intervalo humano de duración arbitraria, y **ningún documento dice cuál va primero**.

Hasta hoy salió bien por disciplina, no por diseño. La migración `012` (BSUID) se diseñó
retrocompatible **a propósito** y lo dejó escrito en su encabezado: conservó `uq_client_user_phone`
justamente para poder aplicarse con el código viejo desplegado, «sin ventana de rotura y sin acoplar
el orden migración↔deploy». Esa nota es el precedente, pero es una nota de una migración, no una
regla.

### El caso que lo destapa

La migración `013` (ADR-010) **no** es retrocompatible, y sus tres secciones tienen restricciones
**opuestas entre sí**:

| Sección | Contenido | Cuándo debe aplicarse | Qué pasa si se invierte |
|---|---|---|---|
| 1 · DDL | `order_summary_fingerprint`, `order_summary_sent_at` | **antes** del despliegue | el ORM ya declara las columnas (`models/core.py`), así que cada `select(Conversation)` falla: **100 % de los turnos devuelven 500** |
| 2 · `business_rules` | reglas de envío y presentación | indiferente | el resumen cotiza con tarifas viejas un rato |
| 3 · `system_prompt_template` | quita RESUMEN DE CONFIRMACIÓN y las conversiones | **después** del despliegue | el prompt deja de enseñar a redactar el resumen y el código todavía no lo renderiza: **nadie lo manda** |

El modo de falla de la sección 1 es especialmente malo porque es **silencioso**: n8n traga el 500 por
el `continueOnFail: true` de `POST Ingest Message` y **todas las ejecuciones se marcan `success`**
(deuda #12). Un despliegue roto se vería igual que un día tranquilo.

El brief de ADR-010 dice «aplicar 013 en prod y desplegar», en ese orden y sin distinguir secciones.
Con esa instrucción, la sección 3 entra antes de tiempo.

## Decisión

**Toda migración declara su orden respecto del despliegue en el encabezado, y una migración nunca
mezcla secciones con órdenes distintos.**

### 1. Campo obligatorio en el encabezado

Junto a `-- Applied:`, cada migración lleva:

```sql
-- Orden: ANTES del despliegue | DESPUÉS del despliegue | INDIFERENTE (retrocompatible)
-- Por qué: <una línea>
```

### 2. La regla que decide el valor

- **ANTES**: la migración añade algo que el código nuevo va a leer — típicamente una columna que el
  ORM declara. Añadir es seguro para el código viejo si es aditivo y nullable.
- **DESPUÉS**: la migración retira o cambia algo de lo que el código viejo todavía depende —
  típicamente prompt o `business_rules`.
- **INDIFERENTE**: la migración es retrocompatible en ambos sentidos, como la `012`. **Es el objetivo
  por defecto**: si se puede diseñar así, se diseña así.

### 3. Una migración, un orden

Si un cambio necesita las dos cosas, **se parte en dos migraciones** con numeración secuencial
normal. Concretamente, la `013` se separa: el DDL en una, los datos y el prompt en la siguiente.

### 4. El CI no cambia

No se bloquea el despliegue ni se añade una tabla de versiones. La disciplina es documental porque el
volumen lo permite (trece migraciones en cinco meses, todas aplicadas a mano por la misma persona).
Cuando el volumen o el número de manos crezca, esta decisión se revisa.

## Alternativas consideradas

- **A. Exigir que toda migración sea retrocompatible** (siempre INDIFERENTE). Rechazada como regla
  dura: es el ideal y por eso queda como default, pero retirar una sección del prompt no tiene versión
  retrocompatible. Forzarlo llevaría a prompts con dos redacciones conviviendo.
- **B. Tabla de versiones + migrador automático en el arranque del contenedor.** Rechazada por ahora:
  resuelve el orden a cambio de que un arranque pueda mutar producción sin supervisión, que es peor
  para una base donde cada migración de prompt es un cambio visible para el cliente.
- **C. Bloquear el deploy del CI y volverlo manual.** Rechazada: el despliegue automático en push a
  `main` ha funcionado y quitarlo introduce un paso humano nuevo para resolver un problema que se
  resuelve declarando el orden.
- **D. Dejarlo en el brief de cada implementación.** Rechazada: es lo que hay hoy, y el brief de
  ADR-010 lo dijo mal. La restricción es de la migración, no del brief que la acompaña.

## Consecuencias

**Positivas**
- El despliegue de ADR-010 deja de depender de que alguien recuerde el orden correcto.
- El default explícito (retrocompatible) empuja el diseño hacia donde ya lo llevó la `012`.
- Dos líneas por archivo, cero infraestructura.

**Negativas / costos**
- Partir la `013` en dos significa dos ventanas de aplicación manual en vez de una.
- La regla no se puede verificar automáticamente: si alguien escribe el campo mal, nada lo atrapa.

**Neutras**
- Las doce migraciones ya aplicadas no se tocan. El campo se exige a partir de la próxima.

## Cuándo revisar

- Cuando exista un segundo entorno (staging) donde probar el orden antes de producción.
- Cuando las migraciones dejen de aplicarse a mano.
- Si aparece una migración cuya sección de datos deba ir antes y cuyo DDL deba ir después: sería señal
  de que la regla de "una migración, un orden" no basta.
