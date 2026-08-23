# Brief de implementación — Allowlist de contenido ilegible (mitad barata de P16) + contrato de respuesta (deuda #13)

> **Autónomo**: todo el contexto necesario está aquí.
>
> **Sistema REAL**: FastAPI en prod, Postgres post-012, n8n vivo (`master`, `cafe_arenillo_v2`),
> Chakra HQ transporte. P14 cerrado y verificado con clienta real.
>
> **Alcance**: que el bot deje de responder a mensajes que no puede leer. **NO** es P16
> completo (descarga de medios, visión, ASR) — eso sigue siendo su ADR. Aquí solo el guard.
>
> **⚠️ Dependencia estructural**: el guard suprime el turno devolviendo `should_respond:
> false`, que es el mismo camino que hoy revienta con 500 (deuda #13). Por eso este brief
> arregla PRIMERO el contrato de respuesta y DESPUÉS añade el allowlist encima. Dos commits,
> en ese orden. No se puede invertir.
>
> **Disciplina**: plan por archivo antes de codear, espera confirmación. Cada commit su test,
> suite verde. Rama nueva desde origin/main (fetch primero). NO desplegar sin OK.
>
> **NO toca n8n.** El workflow ya maneja `should_respond: false` (rama false de `IF Should
> Respond` → Stop). Verificarlo, no cambiarlo.

---

## Contexto: la evidencia

**El patrón, en una frase**: el sistema *descarta en silencio lo que no reconoce, y responde
en serio a lo que no puede leer*. Este brief ataca la segunda mitad.

Medido contra prod (diagnóstico 2026-08-22, retención de 4 días — cotas inferiores):
- De 79 inbound que pasaron el filtro, **20 no tenían contenido legible** (16 `unsupported`,
  3 `audio`, 1 `edit`) y **el bot les respondió igual**. Una de cada cuatro respuestas del
  bot en esos días fue a ciegas.
- Medición histórica anterior: 50 inbound con `content=''` = **15,6% de 321 inbound**
  (35 `unsupported`, 12 `image`, 2 `audio`, 1 `edit`).

**Los dos daños concretos, ambos en ventas reales**:
1. **2026-08-19, evento `edit`** (la clienta corrigió un typo): entró como inbound vacío con
   wamid nuevo → el LLM vio un mensaje en blanco y **se re-presentó desde cero en mitad de la
   venta** ("Hola, soy Sebastian…"). La clienta respondió "🤔" y el bot se re-presentó otra
   vez. Costó 2 turnos y el disfraz de humano.
2. **2026-08-19 22:15 UTC, audio con `content=''`**: la venta ya había cerrado la conversación,
   así que el ingest abrió una nueva (`d7c70f32`, v1, sin historial) y el bot soltó
   "Hola, ¿cómo estás? Aquí estoy para ayudarte con lo que necesites" — **5.739 tokens para
   saludar como desconocida a la clienta que acababa de comprar**, mientras el dueño cerraba
   la logística a mano. Cerrar la venta EMPEORÓ el bug en vez de contenerlo.

**Por qué el guard va en el backend y no en n8n**: el mensaje DEBE persistir (así el turno
siguiente lo recupera del historial — es exactamente como sobrevivió la venta del 19 a los
500 del debounce). Si el guard vive en n8n, `POST Ingest Message` nunca dispara y el mensaje
no queda registrado. Además, "¿respondo o no?" es decisión de negocio → backend gobierna.

---

## COMMIT 1 — Contrato de respuesta válido (deuda #13)

### El bug
`services/ingest.py:228` retorna `{"should_respond": False, "reason": "debounce"}`. El
endpoint hace `IngestMessageResponse(**result)` → faltan 8 campos requeridos → **500 en CADA
coalescencia** (4 veces en la venta real del 08-19). n8n traga el AxiosError por la rama
false de `IF Should Respond` → Stop, ejecución "success". **Este camino nunca ha devuelto una
respuesta válida.**

El contraste que lo delata: el path de `DuplicateMessageError` (`api/v1/ingest.py:99-110`)
**sí** construye la respuesta dummy completa. Al de debounce se le olvidó.

### El fix
- Extraer un helper (p. ej. `_build_suppressed_response(conversation, reason)`) que construya
  un `IngestMessageResponse` **completo y válido** con `should_respond=False` y el `reason`
  dado. Modelarlo sobre lo que ya hace el path de `DuplicateMessageError` — no inventar forma
  nueva; reusar la que ya funciona.
- El camino de debounce (`ingest.py:228`) devuelve ese helper con `reason="debounce"`.
- **PROPÓN** dónde vive el helper (servicio vs. endpoint) según dónde esté hoy la construcción
  del dummy de duplicados, y confírmamelo antes de codear.

### Tests
1. Dos llamadas a `/ingest` con <5 s de diferencia → **ambas 200**, la segunda con
   `should_respond: false, reason: "debounce"`. (Este test falsifica el bug: hoy la segunda
   da 500.)
2. El objeto devuelto **valida** contra `IngestMessageResponse` (todos los campos requeridos).
3. Regresión: el path de `DuplicateMessageError` sigue devolviendo lo mismo, byte-idéntico.

### Verificación de contrato con n8n (leer, NO cambiar)
Confirmar que la rama false de `IF Should Respond` maneja un 200 con `should_respond: false`
igual que hoy maneja el AxiosError → Stop. Es el comportamiento esperado y no requiere tocar
el workflow; solo confirmar que no hay un nodo que asuma el error.

---

## COMMIT 2 — Allowlist de contenido ilegible

### La regla
**Basada en CONTENIDO, no en enumeración de tipos.** Si tras normalizar no hay contenido
legible, no se genera turno. Razón: enumerar tipos (`{'text'}`) es frágil — Meta inventa
tipos nuevos (`edit`, `unsupported`, y los que vengan) y cada uno sería un bug nuevo. La
pregunta correcta no es "¿qué tipo es?" sino "¿hay algo a lo que responder?".

- Si `content` es vacío o solo espacios → **suprimir el turno**: devolver el helper del
  Commit 1 con `reason="unreadable_content"`.
- **El mensaje SÍ se persiste** (con su `message_type` real) antes de suprimir. Orden
  innegociable: persistir → suprimir. El historial debe conservarlo.

### Ubicación
En `services/ingest.py`, después de que el mensaje persiste y **antes** de computar el
GoalStrategyEngine / la directiva (no gastar el cómputo ni la llamada al LLM). Misma forma
que el early-return del debounce.

### Observabilidad sin construir telemetría
El `reason="unreadable_content"` en la respuesta + el `message_type` ya persistido en
`messages` bastan: un SELECT cuenta cuántas veces pasó, por tipo, cuando quieras. **No
construir logging ni eventos nuevos** (misma decisión que se tomó para el fallback de P14).

### ⚠️ Decisión que necesito confirmada ANTES de codear
Hoy, cuando llega un medio ilegible, el bot responde algo (a veces por accidente útil —
repitió "Recibido, muchas gracias" cuando llegó una imagen de comprobante; a veces desastroso
— saludó como desconocida a la clienta que acababa de comprar). Con este fix, **el bot queda
en silencio** ante un medio.

Trade-off: un cliente que manda solo una foto y no recibe nada puede sentirse ignorado. A
favor del silencio: hoy recibe algo peor (saludo genérico o repetición), y en el punto del
flujo donde llegan comprobantes el operador ya fue avisado por Telegram
(`checkpoint_completed:user_confirmed`).

**Recomendación**: silencio + registro. Un acuse determinista ("recibí tu imagen") pertenece
a P16 propiamente, cuando el sistema sepa QUÉ es el medio. Confirmar antes de implementar.

### Tests (puros, sin DB donde se pueda)
1. **El caso del `edit`**: inbound `type=edit`, `content=''` → `should_respond: false`,
   `reason: "unreadable_content"`, mensaje persistido, **cero llamadas al LLM**.
2. **El caso del audio**: `type=audio`, `content=''` → igual.
3. **`unsupported`** → igual.
4. **`image` sin caption** → igual.
5. **Whitespace-only** (`content='   '`) → igual (normalizar antes de evaluar).
6. **Regresión crítica**: texto normal con contenido → `should_respond: true`, flujo intacto.
7. **Regresión**: el mensaje suprimido **sigue en `messages`** y es legible por el turno
   siguiente vía `recent_messages` (esto es lo que hace seguro suprimir).
8. Suite completa verde.

### Verificación por mutación
Con el guard desactivado, los tests 1–5 deben **fallar**. Si pasan igual, el guard no está
mordiendo.

---

## Definition of done

- Dos llamadas a `/ingest` en ráfaga → 200 en ambas (no más 500 en coalescencia).
- Un `edit`, un `audio`, un `unsupported` o una `image` sin caption **no generan turno ni
  llamada al LLM**, y quedan persistidos.
- Un texto normal se comporta byte-idéntico a hoy.
- El mensaje suprimido es recuperable del historial en el turno siguiente.
- Cero cambios en n8n. Cero migraciones. Cero schema.

## Fuera de alcance (NO tocar)

- **P16 propiamente**: descarga de medios desde Chakra, la ventana de 301-302 s de la URL
  firmada, persistencia de bytes, visión, ASR (P25). Este brief solo evita responder a ciegas.
- **Captions de imagen**: si una imagen trae `caption`, hoy no se extrae. Decidir en P16;
  aquí una imagen con caption cae en "sin contenido" como todas.
- **Acuse de recibo determinista**: pertenece a P16.
- **P29 (echoes)**: los echoes mueren en el guard de n8n por otra razón (`message_echoes[]`
  vs `messages[]`); es otro frente, otro commit.
- **P7 (rediseño del debounce)**: el Commit 1 arregla el CONTRATO, no la race ni el
  `sleep` dentro de la transacción. Eso sigue esperando su ADR.

## Nota para el ROADMAP al cerrar

Este trabajo cierra la **deuda #13** completa y una porción medible de **P16** (el 15,6% de
inbound ilegible deja de generar respuestas a ciegas). P16 sigue abierto para la descarga de
medios — anotar que su alcance se redujo, no que se cerró.
