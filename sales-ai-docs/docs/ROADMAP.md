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
> Última actualización: 2026-08-01

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

### F1 · Mensajes con LID/privacidad se pierden en silencio  ← SIGUIENTE
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

### F2 · user_confirmation se fija por interpretación del LLM (hermano del bug de payment)
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
es vulnerable. Confirmados: payment (✅ resuelto), user_confirmation (F2). Los checkpoints
de *datos* (dirección, teléfono) proveen datos, no afirman hechos → probablemente no
afectados. **Decisión tomada**: resolver F2 acotado primero; si su solución se parece a la
de payment, ENTONCES evaluar extraer un mecanismo general. No generalizar por elegancia
antes de tener 2 casos resueltos.
**Empezar por**: diagnóstico read-only (cómo se fija hoy user_confirmation, por qué el
cruce lo disparó, si la solución se parece a la de payment).
Riesgo: [B] probable, a confirmar tras diagnóstico.

### F3 · Medios entrantes llegan con content vacío (sistema ciego a imágenes)
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

### F4 · Debounce: rediseño (deuda #2 + P7)
**Qué**: `asyncio.sleep(5)` DENTRO de la transacción ocupa el pool y suma 5 s a cada
`/ingest`. Además su ventana temporal se ANCLA al timestamp de WhatsApp, no al reloj del
ingest → la ventana efectiva se desplaza con la latencia de entrega (los medios llegan
más lento). Fue la causa del mensaje duplicado del 2026-08-01.
**Requisitos que absorbió este ADR**: (a) sacar la espera de la transacción; (b) el delay
humano opcional (ver F9); (c) robustez ante latencia variable de WhatsApp.
Riesgo: [ADR] + [B] — toca hot path, el north-star lo reescribiría. No tocar sin ADR.

### F5 · Idempotencia outbound (P6)
**Qué**: el sistema no garantiza que un mismo mensaje no se envíe dos veces (saludos
duplicados, imagen duplicada). La 007 dropeó `idempotency_key` y no se repuso.
**Evidencia**: el mensaje duplicado del 2026-08-01 también toca esto.
Riesgo: [ADR] + [DB] + [B] — toca schema + hot path.

### F6 · Manejo de errores 409/5xx en n8n (P5)
**Qué**: n8n no maneja el 409 (strategy_version stale) ni 5xx del backend. Un 409 hoy =
drop silencioso del turno. Relacionado con deuda #3 (fallas silenciosas).
Riesgo: [N8N].

---

## 🟢 ABIERTOS — higiene, intercalar entre fixes

### F7 · Barrido de código muerto tras el fix de venta duplicada
**Qué**: al desactivar el camino LLM→payment pudo quedar código sin uso más allá de lo ya
borrado (`_PAYMENT_CONFIRMATION_REQUIRES`, gate de pago). Barrer qué quedó colgando.
Riesgo: [B], bajo. Hacer entre fixes.

### F8 · Diagnóstico de datos legacy (pendiente desde el fix de venta duplicada)
**Qué**: SELECT read-only: cuántas conversaciones tienen `payment_confirmation` en
contexto / estado viejo, cuáles reales vs. prueba. Decide si limpiar es "borrar 1 fila" o
"reconciliar N ventas reales mal registradas". NO asumir que es solo la fila de prueba.
Riesgo: read-only, luego posible UPDATE puntual aprobado por humano.

### F8b · Mensaje engañoso de Telegram (caso legado)
**Qué**: el callback dice "ya estaba confirmada y cerrada" cuando en el caso legado la
conversación no cerró. Cosmético. Anotado en notas as-built de ADR-009.
Riesgo: [N8N], menor. Resolver junto con F8 si hay filas legacy.

### F8c · Documentación fuera de git
**Qué**: `CLAUDE.md` y la carpeta `n8n_workflow/` están gitignored. Conocimiento
operacional valioso sin versionar, acumulándose. Decidir conscientemente: ¿versionar
(sacar de .gitignore) o aceptar que es efímero y mover lo durable a docs/?
Riesgo: decisión, no código.

---

## 🔵 DISEÑO EN ESPERA — no implementar aún

### F9 · Rediseño del prompt/directive (flujo secuencial + tono)
**Qué**: cuando el `profile` ya tiene datos, el flujo re-pregunta campo por campo en vez
de confirmar en bloque ("¿tu dirección?" → "¿tu teléfono?" en turnos separados, aunque el
bot ya los tenga). El sistema no distingue "no tengo el dato, lo pido" de "lo tengo, lo
confirmo". Relacionado: tono robótico (6 migraciones sin resolverlo por prompt) y deuda #8.
**Insight de diseño**: reglas enterradas en el prompt de ~5.900 tokens se ignoran; lo que
el LLM obedece es el DIRECTIVE (corto, al frente). El rediseño debe mover reglas del muro
al directive, y colapsar checkpoints de confirmación cuando ya hay datos.
**Su propio proyecto**. Empieza por diagnóstico (qué se ignora, qué va al directive, qué se
degradó en 9 migraciones). Caso de uso más claro: la conversación del 2026-08-01.
Riesgo: [ADR] o [DB] migración de prompt. NO mezclar con fixes de comportamiento.

### F10 · Detección de conversaciones no-humanas (ADR-010, diseñado)
**Qué**: bot-a-bot accidental (caso LATAM) y abuso deliberado (scrapers que queman tokens).
Señales: velocidad de respuesta inhumana (la más barata/potente), turnos sin progreso del
DAG, repetición semántica, volumen anómalo. Modelo de SCORE que suma señales → escala a
handoff (reusa el mecanismo existente). El LLM nunca decide "es un bot"; el backend sí.
**Estado**: diseñado, no implementar aún. Excepción posible: la señal de velocidad si la
amenaza se materializa. P8 (circuit breaker por 3 idénticos) ya cubre el caso trivial.
Riesgo: [ADR] escrito, [B] cuando se implemente.

---

## ✅ CERRADO Y VERIFICADO

- **Venta duplicada** (2 caminos marcaban pago; se desactivó el camino LLM→payment en 3
  superficies: backend, prompt DB migración 011, n8n). Verificado en prod 2026-08-01:
  una venta, autor operador, cero fantasmas del LLM. Fix con verificación por mutación.
- **P8 · Circuit breaker** (3 outbounds idénticos consecutivos → human_handoff).
- **P3 · Gate de payment_confirmation permeable** (recálculo tras el gate de user_confirmation).
- **P2 · ORDER_FIELDS** (quantity/grind/roast se persisten; registro de compra con quantity/total).
- **P4 · Observabilidad de compaction** (dejó de fallar en silencio; causa raíz aún pendiente
  de leer del log de prod — sub-abierto menor).
- **ADR-008 · Multiidioma + teléfono** (detección de idioma en backend; validación E.164-laxa).
- **ADR-009 · Lazo de handoff** (endpoint confirm-payment + auth escopada + Telegram +
  corte de respuesta n8n + registro de venta + cierre a closed). Probado e2e.
- **Slot perdido / captura de ORDER_FIELDS** en el directive (oportunista, no bloqueante).
- **Infra · minReplicas 0→1** (eliminó cold starts que perdían mensajes).
- **Drift de docs** (CLAUDE.md, n8n CLAUDE.md sincronizados con la realidad).

---

## Orden sugerido de cierre (revisable)

1. **F1** (LID/privacidad) — cliente real perdiéndose HOY. Empezar por verificación Chakra
   + telemetría (protege ya).
2. **F2** (user_confirmation) — hermano del bug cerrado; diagnóstico primero.
3. Decidir si **F3** (medios ciegos) entra ya (el comprobante ciego afecta confianza) o si
   el operador viéndolo en WhatsApp basta por ahora.
4. Intercalar **F7** (código muerto) y **F8** (diagnóstico datos legacy) — cortos, bajo riesgo.
5. **F9** (rediseño prompt) como proyecto propio cuando el flujo esté endurecido.
6. Deuda con ADR (**F4** debounce, **F5** idempotencia) y **F10** (bots) — después del
   endurecimiento, cada uno con su decisión escrita.
