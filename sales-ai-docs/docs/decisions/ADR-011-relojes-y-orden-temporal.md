# ADR-011 — En qué reloj vive cada decisión temporal

- **Estatus**: Propuesto
- **Fecha**: 2026-09-01
- **Decididores**: Sebastian + cofounder/principal architect
- **Origen**: auditoría del 2026-09-01 (`docs/postmortems/auditoria-2026-09-01-roadmap-y-planeacion.md` §5)

> ⚠️ Nota de numeración: el 010 lo tomó `ADR-010-backend-gobierna-resumen.md` (frente P15), escrito el
> 2026-08-23 y pendiente de merge al escribirse este ADR. Si por alguna razón ese ADR no entrara, este
> conserva el 011 igualmente: los números no se reciclan.

---

## Contexto

El sistema compara instantes que vienen de **cuatro relojes distintos**, y ninguna decisión escrita
dice cuál manda en cada caso:

1. **El timestamp de WhatsApp**, que pone Meta y viaja en el webhook. Es el único que sabe cuándo
   habló el cliente. Llega **pisado al segundo**.
2. **El reloj de pared del backend** (`datetime.now(timezone.utc)` en Azure), que sella los outbound,
   el `order_summary_sent_at` y los eventos de `audit_log`.
3. **El vencimiento absoluto de las URLs de medios** de `lookaside.fbsbx.com`, que Meta calcula sobre
   su propio reloj y expira 301–302 s después del timestamp del mensaje.
4. **El intervalo humano** entre desplegar código y aplicar una migración a mano.

Una fracción desproporcionada de los fallos difíciles del sistema vive en esos desfases: el falso
positivo de confirmación del 2026-08-01, la ventana efectiva del debounce, y el riesgo de despliegue
que motiva ADR-012. **No todos**: el drop por LID, la alucinación de la llave, los echoes del
operador, la compaction rota y el scale-to-zero no tienen nada que ver con relojes. Aproximadamente un
tercio, concentrado en dos costuras.

### Lo medido (2026-09-01, `[DB]` sobre 359 inbound)

| Magnitud | Valor |
|---|---|
| Resolución del timestamp de Meta | **1 s** — 359 de 359 inbound caen en segundo entero |
| Retraso de entrega, texto: mediana / p90 / máximo | **3,5 s / 6,8 s / 9,1 s** (n=108, desde 08-01) |
| Retraso de entrega, imagen / audio (mediana) | **7,9 s / 8,2 s** |
| Diferencia media−texto | **≈ 4,4 s** |
| Cota superior del adelanto del reloj de Meta | **≤ 0,2 s** |
| Entregas tardías extremas | **20, 22 y 80 minutos**, todas el 06-12 |
| Vida útil de la URL firmada de medios | **301–302 s** desde el timestamp |

La cota de 0,2 s no es una medida limpia: el `asyncio.sleep(5)` no es exacto y el timestamp está
pisado. Es cota superior, y para lo que se decide con ella basta.

### El caso que obliga a escribirlo

El falso positivo del 2026-08-01 se documentó como «el inbound entró **795 ms antes** de que el
resumen se persistiera». La medición es correcta pero la lectura no: el inbound tiene
`timestamp 17:51:35` (segundo entero) y el resumen se persistió a las `17:51:35.795216`. El envío real
pudo ocurrir en cualquier punto de ese segundo, es decir entre 795 ms **antes** y 205 ms **después**.
**El orden sub-segundo no es determinable**, y ningún diseño futuro debe apoyarse en que lo sea.

## Decisión

### 1. Cada decisión temporal declara su reloj

| Decisión | Reloj que manda | Por qué |
|---|---|---|
| Ventana de conversación (24 h) | pared del backend (`last_message_at`) | mide la sesión del sistema, no la del cliente |
| Debounce (¿llegó otro mensaje?) | **Meta**, comparado contra una espera de pared | el orden entre mensajes del cliente es suyo, no nuestro |
| `trigger_message_at` (ADR-010 cond. 3) | **Meta**, escrito por el ingest en `strategy_snapshot` | ata el veredicto al mensaje que el LLM estaba contestando |
| `order_summary_sent_at` | pared del backend | es un hecho propiedad del backend |
| Orden del historial (`recent_messages`) | mezcla deliberada: Meta para inbound, pared para outbound | reproduce el orden real de los cruces, que es lo que el LLM necesita leer |
| Expiración de un medio | **Meta**, absoluto | no se puede renovar; se descarga o se pierde |
| Migración ↔ despliegue | humano | ver ADR-012 |

### 2. Dos invariantes, escritos

- **El timestamp de Meta está pisado al segundo.** Ninguna lógica puede depender del orden
  sub-segundo entre un evento de Meta y uno nuestro.
- **El reloj de Meta puede ir hasta ~0,2 s adelantado** respecto del nuestro. Cualquier comparación
  entre relojes distintos tolera ese margen o falla hacia el lado seguro.

### 3. Cruzar relojes exige declarar el lado seguro

Cuando una comparación entre relojes distintos es inevitable —hoy solo la condición 3 de ADR-010— la
decisión debe **fallar cerrada** y emitir su propio side effect distinguible, para poder medir falsos
negativos en producción. ADR-010 ya lo hace (`warning:confirmation_rejected_inbound_predates_summary`);
esta decisión lo generaliza y explica por qué el piso al segundo empuja hacia el rechazo, que es el
lado correcto: el costo de rechazar es un turno de fricción, el de aceptar de más es una venta
registrada en falso.

### 4. Un fix concreto, y solo uno

El lookahead del debounce (`services/ingest.py`) busca `Message.created_at > msg_timestamp`. Con el
timestamp pisado al segundo, **dos mensajes del mismo segundo no se ven entre sí**: ocurrió el
2026-07-18 y produjo tres respuestas. Pasa a comparar `>=` excluyendo el propio
mensaje por `id`. Es una línea, con su test, y no espera al rediseño de P7.

## Alternativas consideradas

- **A. Normalizar todo al reloj de pared del backend** (usar la hora de recepción y descartar la de
  Meta). Rechazada: se pierde el orden real de los cruces, que es justo lo que el debounce y la
  condición 3 necesitan. Con retrasos de 3,5 s medianos y hasta 80 minutos observados, la hora de
  recepción no dice cuándo habló el cliente.
- **B. Pedir un timestamp de alta resolución.** Rechazada: Meta entrega segundos. Lo único que n8n
  podría añadir es cuándo lo recibió, que ya es el reloj de pared.
- **C. Abrir un frente P propio de "ordenamiento temporal".** Rechazada: acumular diseño sobre un
  problema mayormente mitigado es el sesgo que la auditoría señala. Lo que hay que implementar cabe en
  el punto 4.
- **D. No escribir nada.** Rechazada: el sistema ya tomó tres decisiones temporales correctas (el
  `trigger_message_at` del ingest, el `<=` de la condición 3, el filtro por contenido del lookahead) y
  ninguna consta como decisión. La próxima sesión las puede deshacer sin saber que lo hace.

## Consecuencias

**Positivas**
- El desfase deja de ser folclore y pasa a ser dos números escritos.
- La condición 3 de ADR-010 queda respaldada: su `<=` no es conservadurismo, es la consecuencia del
  piso al segundo.
- Cualquier diseño futuro que quiera comparar instantes tiene dónde mirar antes de inventar.

**Negativas / costos**
- Una tabla más que mantener. Si aparece un quinto reloj (una pasarela de pago, un transportador),
  hay que añadirlo o el documento miente.
- El fix del punto 4 hace el debounce marginalmente más agresivo: dos mensajes del mismo segundo
  pasan a coalescer. Es lo que el debounce quiere, pero es un cambio de comportamiento observable.

**Neutras**
- No toca n8n, no toca el schema, no cambia ninguna dependencia.

## Cuándo revisar

- Si aparece un quinto reloj en el sistema.
- Si Meta pasa a entregar timestamps con resolución sub-segundo: el invariante 1 caería y varias
  decisiones podrían endurecerse.
- Si el side effect `confirmation_rejected_inbound_predates_summary` aparece en producción sobre
  confirmaciones legítimas: sería el primer falso negativo atribuible al cruce de relojes.
