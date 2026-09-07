# Modelo de sistema — qué debe ser siempre cierto

Un archivo YAML por componente crítico. Cada uno declara qué garantiza ese
componente, por qué, y **qué prueba lo respalda**. Un verificador lo contrasta
contra el repo en cada corrida de CI.

```bash
pip install -r tools/requirements.txt
python tools/check_invariants.py --report
```

---

## Por qué existe esta capa

Este proyecto ya documenta muchísimo: 13 ADRs, 11 postmortems, un ROADMAP de
más de mil líneas, una tabla de 19 deudas. Nada de eso es el problema. El
problema es que **la prosa no falla en CI**.

P31 se cerró el 2026-09-05 dando por hecho que P29 lo cubría entero, y se
reabrió el 09-07 cuando un cliente respondió tres horas después. La afirmación
"P29 cubre esto" vivió dos días en un documento sin que nada pudiera
contradecirla. Ese es el modo de fallo que esta capa ataca, y no se arregla
escribiendo mejor: se arregla haciendo que la afirmación sea ejecutable.

La regla es una sola:

> **No se puede declarar que una invariante se cumple sin una prueba que la
> ejercite.**

Si no hay prueba, la invariante se declara `unverified` y sale marcada en el
reporte. Es incómodo a propósito.

---

## Cómo encaja con lo que ya existe

Cada tipo de documento responde una pregunta distinta, y hasta ahora faltaba
una:

| Documento | Pregunta que responde | Cuándo se escribe |
|---|---|---|
| ADR | ¿Por qué decidimos esto? | Una vez, al decidir |
| Postmortem | ¿Qué pasó ese día? | Una vez, después del incidente |
| Deuda `#n` | ¿Qué problema observamos? | Al detectarlo |
| Frente `P<n>` | ¿Qué vamos a hacer? | Al planear |
| **Invariante `INV-…`** | **¿Qué debe ser siempre cierto?** | **Y sigue siéndolo, verificado** |

Las cuatro primeras son fotos: describen un momento. La invariante es la única
afirmación permanente, y por eso es la única que se puede verificar de forma
continua.

La consecuencia práctica es que **un frente P deja de ser "un bug" y pasa a ser
"restaurar la invariante X"**. Mirá el mapeo que salió del primer barrido:

| Frente | Invariante que restaura |
|---|---|
| P6 | `INV-MSG-007` (veracidad del outbound) y `INV-MSG-008` (idempotencia) |
| P29 fase 4 | `INV-OP-006` — hoy `violated` porque n8n no despacha echoes |
| P31 | `INV-CONV-006` (la relación sobrevive al cierre) |
| P32 | `INV-MSG-009` (el historial refleja el contenido real) |
| P34 | el hueco declarado de `INV-OP-005` |
| deuda #19 | el hueco declarado de `INV-MSG-010` |

Eso es lo que convierte la tabla de deudas de una lista que crece en un estado
del sistema que se mide.

---

## El esquema

```yaml
component: MessageLog       # nombre en PascalCase
code: MSG                   # 2-6 mayúsculas, prefijo de los ids
layer: 3                    # 1 lenguaje · 2 política · 3 dominio
summary: >
  Qué es este componente y por qué merece invariantes propias.

owns:
  code:                     # rutas reales — el checker verifica que existan
    - sales_agent_api/app/services/ingest.py
  tables:
    - messages
  outside_repo:             # piezas fuera del repo que participan
    - "n8n · Process Backend Response"

invariants:
  - id: INV-MSG-002         # INV-<code>-<NNN>, único en TODO el modelo
    statement: >
      Lo que debe ser siempre cierto, en una frase.
    why: >
      Qué se rompe si deja de serlo. Con el incidente real si lo hubo — es lo
      que hace que nadie lo relaje en seis meses.
    refs: [ADR-013, P29, deuda#19, migración-015]
    status: holds
    boundary: backend
    enforcement:
      kind: test
      tests:
        - tests/services/test_x.py::test_y
```

### Los cuatro estados

| Estado | Significa | Exige |
|---|---|---|
| `holds` | Se cumple y hay prueba | `kind: test` + al menos un test que exista |
| `partial` | Se cumple a medias | lo anterior **más** `gap:` describiendo qué NO cubre |
| `unverified` | Creemos que se cumple, nada lo prueba | nada — pero sale marcado |
| `violated` | Sabemos que está rota | al menos un `refs` (`deuda#N` o `PN`) |

`partial` es el estado más útil de los cuatro, porque es el que este sistema
tiene de sobra. La deuda #14 dice literalmente "REMEDIADA EN SU MITAD DE
PRESENCIA"; ese matiz es exactamente lo que se pierde en una tabla de dos
columnas y lo que `gap` obliga a escribir.

### `boundary` — dónde vive la garantía

`backend` es el valor por defecto y el único al que el checker le exige prueba.
Los otros (`n8n`, `db`, `infra`, `cross`) marcan invariantes que el backend no
puede probar solo, y son justamente las que más han costado. `INV-OP-006` es el
caso ejemplar: **el backend la cumple, el sistema no.** La pausa por presencia
de operador está implementada y probada con quince tests, y en producción no ha
entrado un solo echo porque falta la fase 4 de n8n. En prosa esas dos cosas se
leen igual. Aquí no.

---

## Qué verifica el checker

1. Un id con la forma correcta, único en todo el modelo, con el prefijo del
   componente que lo declara.
2. Toda ruta de `owns.code` existe. Un rename de servicio rompe el modelo en
   vez de pudrirlo.
3. Todo `ADR-NNN` referenciado resuelve a un archivo de `docs/decisions/`.
4. **Todo test declarado existe en la colección de pytest.** Renombrar un test
   rompe la build en vez de dejar la invariante huérfana en silencio.
5. `holds` y `partial` en el backend exigen `kind: test` con tests reales.
6. `partial` exige `gap`.
7. `violated` exige `refs`.

---

## Cómo agregar un componente

1. Copiar uno de los tres YAML existentes y quedarse con la forma.
2. Escribir las invariantes **partiendo de los incidentes reales**, no de la
   arquitectura ideal. Las mejores de este modelo salieron todas de un
   postmortem: la llave "1234" del 08-19, la venta duplicada del 07-20, el loop
   con el bot de LATAM del 07-18.
3. Buscar en `tests/` qué prueba ya cubre cada una. La suite de este repo tiene
   337 pruebas con nombres que se leen como invariantes; la mayoría ya existe.
4. Lo que no tenga prueba: declararlo `unverified`. **No inventar una prueba
   floja para poder poner `holds`** — eso vacía el modelo de sentido en una
   tarde.
5. Correr `python tools/check_invariants.py --report`.

### Componentes que faltan

Los tres que existen (`MessageLog`, `ConversationState`, `OperatorAuthority`)
son los que más han sangrado. Quedan por escribir:

- `Identity` — BSUID scoped por WABA, unicidad `(client_id, bsuid)`, teléfono
  opcional (P14, ADR-008, migración 012)
- `LlmBoundary` — sin tools, sin loops, sin estado; el LLM propone y nunca
  decide (ADR-001, ADR-002); incluye la deuda #8 del `json_object` sin schema
- `DagGates` — orden de checkpoints, aceptación por slot, `ORDER_FIELDS`
- `TurnOrchestration` — las dos llamadas, los cortes de n8n, el debounce
  (ADR-003, deuda #2, P7)
- `Deployment` — orden entre migración y despliegue (ADR-012), las dos claves
  de OpenAI independientes (deuda #7), el export de n8n como respaldo real
  (deuda #15)
