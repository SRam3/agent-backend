# ADR-006 — VARCHAR + CHECK sobre ENUMs nativos

- **Estatus**: Accepted
- **Fecha**: 2026-04-03
- **Decididores**: Sebastian + cofounder/principal architect

## Contexto

PostgreSQL ofrece dos formas de modelar columnas con conjunto fijo de valores válidos:

1. **ENUM nativo**: `CREATE TYPE conversation_state AS ENUM ('active', 'human_handoff', 'closed')`. Type-safe a nivel DB.
2. **VARCHAR + CHECK constraint**: `state VARCHAR(30) CHECK (state IN ('active', 'human_handoff', 'closed'))`.

Para un sistema en evolución activa donde los conjuntos de valores cambian (hemos colapsado 7 estados a 3 — ver ADR-007), la operación de modificar el set importa.

Con ENUMs nativos, agregar un valor requiere `ALTER TYPE ... ADD VALUE`. Eliminar valores es prácticamente imposible sin renombrar el tipo, migrar datos, eliminar el tipo viejo. Cualquier cambio bloquea operaciones sobre las tablas que usan el tipo durante la migración.

Con VARCHAR + CHECK, modificar el set es `DROP CONSTRAINT` + `ADD CONSTRAINT` — operaciones rápidas, sin lock prolongado.

## Decisión

Usar `VARCHAR + CHECK CONSTRAINT` para todas las columnas con conjunto fijo de valores: `conversations.state`, `messages.direction`, etc.

## Alternativas consideradas

- **ENUMs nativos**: descartado por costo de migraciones futuras.
- **TEXT sin constraint, validación solo en aplicación**: descartado — perdemos la garantía a nivel DB. Un bug en el ORM que escribe "invalid_state" no se detecta hasta que alguien lee.
- **Tabla de lookup separada con FK**: overkill para sets pequeños y estables como estados. Reservado para sets grandes/dinámicos (ej. catálogo de productos) donde tiene sentido.

## Consecuencias

### Positivas
- Migraciones de schema sin downtime al modificar conjuntos válidos.
- Misma garantía de integridad que ENUMs (ningún valor inválido entra a DB).
- Compatible directo con SQLAlchemy `String(30)` — no requiere mapeo especial de tipos.

### Negativas
- Performance: comparaciones de string son marginalmente más lentas que de enum (negligible en práctica).
- Storage: VARCHAR(30) ocupa más bytes que un ENUM (4 bytes). Para tablas con millones de filas podría sumar — irrelevante a nuestra escala.
- Menos auto-documentación: un `\d+ conversations` en psql muestra el constraint, pero no es tan visualmente prominente como un tipo ENUM.

### Trade-offs explícitos
- Ganamos flexibilidad operacional a un costo de performance/storage marginal.

## Cuándo revisar

Revisar esta decisión si:
- Llegamos a tablas de >100M filas donde el storage extra es material (improbable en horizonte cercano)
- Aparecen requirements de tipo-strictness fuerte (compliance, validación cross-DB)
- El conjunto de valores se estabiliza y deja de cambiar — entonces ENUMs serían marginalmente mejores pero la migración inicial cuesta más que el beneficio
