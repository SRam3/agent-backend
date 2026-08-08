# ROADMAP — Sales AI Agent

> Estado de todos los frentes abiertos. Fuente de verdad del "qué sigue". Se actualiza
> conforme se cierra cada frente. Vive en el repo (versionado) a propósito: no crear una
> segunda fuente de verdad fuera de git.
>
> **Objetivo actual**: endurecer el flujo de venta para que clientes reales lo usen con
> confianza. Todo se prioriza contra ese objetivo.
>
> **Disciplina**: cerrar punto a punto. No se abre el siguiente frente hasta cerrar el
> actual (diagnóstico → decisión → fix → verificación). Cada fix, su test; baby-steps.
>
> Última actualización: 2026-08-08

---

## Notación

Tres ejes distintos. **Nunca son intercambiables**:

| Eje | Qué identifica | Dónde vive |
|---|---|---|
| `P<n>` | **Frente de trabajo** — algo que alguien va a hacer | este archivo (registro canónico al final) |
| `DEUDA #<n>` | **Problema observado** — algo que está mal hoy | tabla "Deuda técnica visible" en `CLAUDE.md` |
| `ADR-<nnn>` | **Decisión** tomada y argumentada | `docs/decisions/` |

Un frente P puede remediar una deuda, y puede exigir un ADR previo. Se escribe
**`P7 (remedia deuda #2)`**, nunca `deuda #2/P7` con slash: el slash sugiere que son
el mismo objeto y fue justo lo que hizo perder la pista de qué estaba hecho y qué no.

**Reglas de numeración**:

- Los números **no se reciclan**. Un frente cerrado conserva su P para siempre; una deuda
  retirada conserva su `#` y nadie más lo usa. Reutilizar un número rompe todo documento
  anterior que lo mencione — ya pasó dos veces (ver "Notas históricas").
- **Sin zero-padding**: `P10`, no `P010`. El orden alfabético de `ls` se rompe; se acepta
  a cambio de que el ID en prosa y en nombre de archivo sea idéntico.
- **`P` mayúscula** en prosa y en nombres de archivo. Las ramas git van en minúscula
  (`feat/p14-lid-privacidad`) como excepción deliberada: es convención git y no genera
  ambigüedad.
- Frentes nuevos toman el siguiente número libre — hoy, **P22**.

---

## Leyenda

- 🔴 Bloquea el objetivo (cliente real afectado) — prioridad alta
- 🟡 Deuda/mejora real, no bloquea hoy — prioridad media
- 🟢 Higiene / bajo riesgo — se intercala entre fixes
- 🔵 Diseño en espera — no implementar aún, esperar evidencia/decisión
- ✅ Cerrado y verificado

Riesgo de implementación: [B]ackend acotado · [DB] migración · [N8N] workflow vivo ·
[ADR] requiere decisión escrita antes de tocar código.

---

## 🔴 ABIERTOS — bloquean "clientes reales con confianza"

### P14 · Mensajes con LID/privacidad se pierden en silencio  ← SIGUIENTE
**Qué**: WhatsApp está desplegando privacidad de número (usernames/LID). Para clientes
con privacidad activada, Meta manda solo un `user_id` opaco (`CO.1034…`), sin `from` ni
`wa_id`. El workflow valida `from` con `typeValidation: strict` → rama false → Stop,
marcado "success" en 15 ms. **Drop 100% silencioso**: sin error, sin alerta, sin registro.
**Evidencia**: 2026-08-01, clienta real "Cielo Pineda" escribió "Hola" y se perdió. Único
mensaje real de cliente ese día. Crecerá conforme más usuarios activen privacidad.
**Por qué es #1**: pierde clientes reales ANTES de que entren al flujo. Peor que un bug
dentro del flujo, porque ni te enteras de que existieron.
**El bloqueador real no es detectar, es responder**: el envío usa `"to": phone_number`,
que no existe para estos contactos. Hay que verificar si Chakra/Meta acepta enviar contra
`user_id`. Esa verificación (docs o soporte de Chakra) bifurca la solución.
**Sub-tareas**:
- [ ] Verificar con Chakra si `to` acepta `user_id` (investigación, NO producción — decide todo)
- [ ] Telemetría/alerta: que estos mensajes disparen aviso a Telegram en vez de morir en
      silencio (protege YA, independiente de lo que diga Chakra) — cubre deuda #3 parcial
- [ ] Si Chakra soporta user_id → flujo completo (respuesta automática)
- [ ] Si no → piso mínimo: entran + alertan + operador responde a mano
**Bomba latente asociada**: `phone` es VARCHAR corto (`core.py:74`); el `user_id` de Cielo
mide 19 chars, entra por 1. Uno más largo revienta el insert. Más profundo: la identidad
del cliente ya NO es el teléfono → evaluar campo de identidad separado en `client_user`.
Riesgo: [N8N] + [B] + posible [DB]. Verificación Chakra primero.

### P15 · user_confirmation se fija por interpretación del LLM (hermano del bug de payment)
**Qué**: el LLM marca `user_confirmation: true` interpretando un mensaje del cliente, y un
mensaje cruzado en vuelo lo dispara sin que haya confirmación real.
**Evidencia**: 2026-08-01, "envíame una foto del producto" llegó 0.8 s antes de que el bot
terminara "¿todo bien?", el LLM lo leyó como "sí" y marcó user_confirmation → convocó al
operador con un mensaje que no confirmaba nada. Daño nulo hoy (confirmaste de verdad 6 s
después), pero la puerta está abierta.
**Por qué importa**: es la MISMA clase del bug de payment que acabamos de cerrar (el LLM
afirmando un hecho que no le consta), sobre otro checkpoint que dispara una acción real
(convocar operador). Incoherente dejarlo abierto justo tras cerrar su gemelo.
**Patrón sistémico a vigilar**: cualquier checkpoint que el LLM marca por interpretación
es vulnerable. Confirmados: payment (✅ resuelto), user_confirmation (P15). Los checkpoints
de *datos* (dirección, teléfono) proveen datos, no afirman hechos → probablemente no
afectados. **Decisión tomada**: resolver P15 acotado primero; si su solución se parece a la
de payment, ENTONCES evaluar extraer un mecanismo general. No generalizar por elegancia
antes de tener 2 casos resueltos.
**Empezar por**: diagnóstico read-only (cómo se fija hoy user_confirmation, por qué el
cruce lo disparó, si la solución se parece a la de payment).
Riesgo: [B] probable, a confirmar tras diagnóstico.

### P16 · Medios entrantes llegan con content vacío (sistema ciego a imágenes)
**Qué**: los mensajes con imagen se guardan con `content` vacío — no hay manejo de medios.
El sistema no puede razonar sobre nada visual.
**Evidencia**: 2026-08-01, el comprobante de pago (imagen) entró como mensaje vacío; el
bot "vio" un mensaje en blanco y repitió su despedida (causó el mensaje duplicado). La
foto de producto pedida tampoco se envió.
**Por qué importa**: el comprobante de pago —el artefacto MÁS importante de la venta— es
una imagen, y el sistema es ciego a él. Hoy lo salva que el operador lo ve en WhatsApp.
Es brecha de CAPACIDAD transversal (entrante: comprobante; saliente: foto producto), no un
bug puntual.
**Alcance**: grande. Probablemente [ADR] para decidir hasta dónde (¿solo registrar que
llegó media?, ¿pasarla al LLM?, ¿persistir el comprobante?). Decidir por separado.
Riesgo: [ADR] + [B] + [N8N].

---

## 🟡 ABIERTOS — deuda real, no bloquea hoy

### P7 · Debounce: rediseño (remedia deuda #2)
**Qué**: `asyncio.sleep(5)` DENTRO de la transacción ocupa el pool y suma 5 s a cada
`/ingest`. Además su ventana temporal se ANCLA al timestamp de WhatsApp, no al reloj del
ingest → la ventana efectiva se desplaza con la latencia de entrega (los medios llegan
más lento). Fue la causa del mensaje duplicado del 2026-08-01.
**Requisitos que absorbió este ADR**: (a) sacar la espera de la transacción; (b) el delay
humano opcional (ver P21); (c) robustez ante latencia variable de WhatsApp.
Riesgo: [ADR] + [B] — toca hot path, el north-star lo reescribiría. No tocar sin ADR.

### P6 · Idempotencia outbound (remedia deuda #11)
**Qué**: el sistema no garantiza que un mismo mensaje no se envíe dos veces (saludos
duplicados, imagen duplicada). La 007 dropeó `idempotency_key` y no se repuso.
**Evidencia**: el mensaje duplicado del 2026-08-01 también toca esto.
Riesgo: [ADR] + [DB] + [B] — toca schema + hot path.

### P5 · Manejo de errores 409/5xx en n8n (remedia deuda #12)
**Qué**: n8n no maneja el 409 (strategy_version stale) ni 5xx del backend. Un 409 hoy =
drop silencioso del turno. Relacionado con deuda #3 (fallas silenciosas).
Riesgo: [N8N].

---

## 🟢 ABIERTOS — higiene, intercalar entre fixes

### P17 · Barrido de código muerto tras el fix de venta duplicada
**Qué**: al desactivar el camino LLM→payment pudo quedar código sin uso más allá de lo ya
borrado (`_PAYMENT_CONFIRMATION_REQUIRES`, gate de pago). Barrer qué quedó colgando.
Riesgo: [B], bajo. Hacer entre fixes.

### P18 · Diagnóstico de datos legacy (pendiente desde el fix de venta duplicada)
**Qué**: SELECT read-only: cuántas conversaciones tienen `payment_confirmation` en
contexto / estado viejo, cuáles reales vs. prueba. Decide si limpiar es "borrar 1 fila" o
"reconciliar N ventas reales mal registradas". NO asumir que es solo la fila de prueba.
Riesgo: read-only, luego posible UPDATE puntual aprobado por humano.

### P19 · Mensaje engañoso de Telegram (caso legado)
**Qué**: el callback dice "ya estaba confirmada y cerrada" cuando en el caso legado la
conversación no cerró. Cosmético. Anotado en notas as-built de ADR-009.
Riesgo: [N8N], menor. Resolver junto con P18 si hay filas legacy.

### P9 · Microfixes n8n
**Qué**: `latency_ms` real en "Validate and Prepare Action" (hoy 0 hardcodeado); evaluar
subir el `slice(-10)` a los 20 mensajes que el backend ya manda (hoy descarta la mitad del
historial disponible).
**Se despacha junto con P5** para tocar el workflow vivo una sola vez, con export antes y
después al repo.
Riesgo: [N8N], bajo.

### P20 · Documentación fuera de git
**Qué**: `CLAUDE.md` y la carpeta `n8n_workflow/` están gitignored. Conocimiento
operacional valioso sin versionar, acumulándose. Decidir conscientemente: ¿versionar
(sacar de .gitignore) o aceptar que es efímero y mover lo durable a docs/?
**Avance parcial (2026-08-08)**: el registro canónico de frentes P se movió de `CLAUDE.md`
a este archivo justo por esta razón — la lista maestra no puede vivir fuera de git. Lo que
queda en `CLAUDE.md` es su espejo operativo. El resto de la decisión sigue abierto.
Riesgo: decisión, no código.

---

## 🔵 DISEÑO EN ESPERA — no implementar aún

### P21 · Rediseño del prompt/directive (flujo secuencial + tono)
**Qué**: cuando el `profile` ya tiene datos, el flujo re-pregunta campo por campo en vez
de confirmar en bloque ("¿tu dirección?" → "¿tu teléfono?" en turnos separados, aunque el
bot ya los tenga). El sistema no distingue "no tengo el dato, lo pido" de "lo tengo, lo
confirmo". Relacionado: tono robótico (6 migraciones sin resolverlo por prompt) y deuda #8.
**Insight de diseño**: reglas enterradas en el prompt de ~5.900 tokens se ignoran; lo que
el LLM obedece es el DIRECTIVE (corto, al frente). El rediseño debe mover reglas del muro
al directive, y colapsar checkpoints de confirmación cuando ya hay datos.
**Su propio proyecto**. Empieza por diagnóstico (qué se ignora, qué va al directive, qué se
degradó en 9 migraciones). Caso de uso más claro: la conversación del 2026-08-01.
**Frente hermano, deliberadamente separado**: P13 (conocimiento curado de café). P21 es
ESTRUCTURA del prompt (qué va al directive, cuándo confirmar en bloque); P13 es CONTENIDO
de dominio (qué sabe el bot sobre café). Se tocan en el mismo archivo de prompt y conviene
coordinarlos, pero son decisiones distintas y no se cierran juntos.
Riesgo: [ADR] o [DB] migración de prompt. NO mezclar con fixes de comportamiento.

### P13 · Conocimiento curado de café en el prompt
**Qué**: el bot no tiene conocimiento de dominio curado sobre café (origen, tueste, molienda,
maridaje). Responde con lo que el modelo base sabe, sin criterio del negocio.
**Origen**: pendiente sin cerrar del stand público 2026-06-16 — ver
`docs/postmortems/postmortem-2026-06-16-stand-publico.md`. Usuarios reales preguntaron cosas
que el bot contestó de forma genérica o incorrecta.
**Bloqueado por una decisión de producto, no técnica**: hay que decidir qué debe saber el bot
y con qué voz, y eso lo define el negocio, no el código.
**Frente hermano**: P21 (rediseño del prompt/directive) — ver la nota de separación allí.
Riesgo: [DB] migración de prompt cuando se decida el contenido.

### P10 · Detección de conversaciones no-humanas + estancamiento del DAG
**Qué**: bot-a-bot accidental (caso LATAM) y abuso deliberado (scrapers que queman tokens).
Señales: velocidad de respuesta inhumana (la más barata/potente), turnos sin progreso del
DAG, repetición semántica, volumen anómalo. Modelo de SCORE que suma señales → escala a
handoff (reusa el mecanismo existente). El LLM nunca decide "es un bot"; el backend sí.
**Absorbe el detector de loop de texto VARIABLE** — el que P8 no cubre, porque su trigger es
comparación exacta de texto. Ese detector se registró en su momento como "candidato P9"
(número ya ocupado por los microfixes n8n) y también como el remanente de `deuda #10`: un
solo frente con dos identificadores, ninguno válido. Aquí queda unificado como P10, porque
"turnos sin progreso del DAG" y "repetición semántica" ya eran dos de sus señales. Detalle
del caso no cubierto: `docs/registros/registro-P8-limitaciones.md`.
**Estado**: diseñado, no implementar aún. Excepción posible: la señal de velocidad si la
amenaza se materializa. P8 (circuit breaker por 3 idénticos) ya cubre el caso trivial.
**ADR-010 pendiente de escribir**: este roadmap lo daba por "diseñado", pero no existe
ningún `ADR-010-*.md` en `docs/decisions/` (el índice llega hasta 009). El diseño vive en
esta entrada; falta convertirlo en decisión escrita antes de implementar.
Riesgo: [ADR] **por escribir**, [B] cuando se implemente.

---

## ✅ CERRADO Y VERIFICADO

- **P11 · Venta duplicada** (2 caminos marcaban pago; se desactivó el camino LLM→payment en 3
  superficies: backend, prompt DB migración 011, n8n). Verificado en prod 2026-08-01:
  una venta, autor operador, cero fantasmas del LLM. Fix con verificación por mutación.
- **P8 · Circuit breaker** (3 outbounds idénticos consecutivos → human_handoff).
- **P3 · Gate de payment_confirmation permeable** (recálculo tras el gate de user_confirmation).
  **Superseded by ADR-009**: hoy el `payment_confirmation` del LLM se descarta siempre
  (`OPERATOR_ONLY_FIELDS`), así que el escenario del gate ya no puede existir. Los tests se
  conservan como regresión.
- **P2 · ORDER_FIELDS** (quantity/grind/roast se persisten; registro de compra con quantity/total).
- **P4 · Observabilidad de compaction** (dejó de fallar en silencio; causa raíz aún pendiente
  de leer del log de prod — sub-abierto menor).
- **ADR-008 · Multiidioma + teléfono** (detección de idioma en backend; validación E.164-laxa).
- **ADR-009 · Lazo de handoff** (endpoint confirm-payment + auth escopada + Telegram +
  corte de respuesta n8n + registro de venta + cierre a closed). Probado e2e.
- **P12 · Slot perdido / captura de ORDER_FIELDS** en el directive (oportunista, no bloqueante).
- **Infra · minReplicas 0→1** (eliminó cold starts que perdían mensajes).
- **P1 · Drift de docs** (CLAUDE.md, n8n CLAUDE.md sincronizados con la realidad).

---

## Orden sugerido de cierre (revisable)

1. **P14** (LID/privacidad) — cliente real perdiéndose HOY. Empezar por verificación Chakra
   + telemetría (protege ya).
2. **P15** (user_confirmation) — hermano del bug cerrado; diagnóstico primero.
3. Decidir si **P16** (medios ciegos) entra ya (el comprobante ciego afecta confianza) o si
   el operador viéndolo en WhatsApp basta por ahora.
4. Intercalar **P17** (código muerto) y **P18** (diagnóstico datos legacy) — cortos, bajo riesgo.
5. **P21** (rediseño prompt) como proyecto propio cuando el flujo esté endurecido.
6. Deuda con ADR (**P7** debounce, **P6** idempotencia) y **P10** (bots) — después del
   endurecimiento, cada uno con su decisión escrita.

---

## Registro canónico de frentes P

**Esta tabla es la fuente de verdad de qué número está tomado.** Antes de asignar un P nuevo,
mirar aquí. La tabla de `CLAUDE.md` es un espejo operativo, no la autoridad.

| P | Frente | Estado | Remedia |
|---|---|---|---|
| P1 | Sincronizar documentación con la realidad | ✅ | — |
| P2 | Persistir `quantity`/`grind`/`roast` (ORDER_FIELDS) | ✅ | — |
| P3 | Cerrar gate permeable de `payment_confirmation` | ✅ *Superseded by ADR-009* | — |
| P4 | Resucitar la lazy-compaction + hacerla ruidosa | 🟡 causa raíz pendiente | deuda #3, #7 |
| P5 | Manejo de errores 409/5xx en n8n | ⬜ | deuda #12 |
| P6 | Idempotencia outbound (texto e imagen) | ⬜ [ADR] | deuda #11 |
| P7 | Debounce: race + conexión ocupada | ⬜ [ADR] | deuda #2 |
| P8 | Circuit breaker para loops conversacionales | ✅ | — |
| P9 | Microfixes n8n (`latency_ms`, `slice(-10)`) | ⬜ | — |
| P10 | Detección de conversaciones no-humanas + estancamiento del DAG | 🔵 [ADR-010 por escribir] | deuda #10 (remanente) |
| P11 | Fix venta duplicada — operador única autoridad del pago | ✅ | — |
| P12 | Captura oportunista de ORDER_FIELDS en el directive | ✅ | — |
| P13 | Conocimiento curado de café en el prompt | 🔵 decisión de producto | — |
| P14 | Mensajes con LID/privacidad se pierden en silencio | 🔴 **SIGUIENTE** | deuda #3 (parcial) |
| P15 | `user_confirmation` por interpretación del LLM | 🔴 | — |
| P16 | Medios entrantes con content vacío | 🔴 [ADR] | — |
| P17 | Barrido de código muerto post-P11 | 🟢 | — |
| P18 | Diagnóstico de datos legacy | 🟢 | — |
| P19 | Mensaje engañoso de Telegram (caso legado) | 🟢 | — |
| P20 | Documentación fuera de git | 🟢 | — |
| P21 | Rediseño del prompt/directive (flujo + tono) | 🔵 | deuda #8 |

**Siguiente número libre: P22.**

---

## Notas históricas de numeración

Dos números se reutilizaron antes de que existiera esta regla. **No se renumeran** — los
documentos que los mencionan son registros inmutables y reescribirlos falsearía el archivo.
Se anotan aquí para que quien lea un documento viejo sepa interpretarlo.

**`P4` en abril de 2026 ≠ P4 (compaction).** La migración
`migrations/versions/006_p4_reduce_robotic_tone.sql`, la rama `prompt/p4-reduce-robotic-tone`
y el commit `prompt(006): reduce robotic tone — P4 surgical edits` (2026-04-20) usan una
numeración anterior, sin relación con el P4 de este registro. La migración está aplicada en
producción; renombrarla sería riesgo sin beneficio.

**`DEUDA #10` cambió de significado el 2026-06-14.** Antes designaba el "reset por idle
30 min" que `CLAUDE.md` documentaba pero que nunca existió en código; eso se cerró con P1.
Después de esa fecha, `#10` designa el corte de n8n post-handoff (hoy resuelto por ADR-009,
con el loop de texto variable como remanente, absorbido por P10). Los documentos de junio
—`brief-audit-2026-06-14-estado-y-plan.md` y `auditoria-2026-06-14-estado-y-plan.md`— usan
el sentido viejo.

**Dónde nacieron P1–P9.** El plan original está en
`docs/postmortems/auditoria-2026-06-14-estado-y-plan.md` §8. Ese documento es el registro
histórico de cómo se priorizó en junio; **ya no es la fuente definicional**. Tenerla enterrada
en un postmortem inmutable fue justamente lo que permitió que P9 se asignara dos veces.
