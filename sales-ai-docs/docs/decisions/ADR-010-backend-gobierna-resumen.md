# ADR-010 — El backend gobierna el resumen del pedido y los datos operacionales

- **Estatus**: Accepted (2026-09-04 — mergeado en PR #68 y desplegado en la revisión `--0000058`)
- **Fecha**: 2026-08-23
- **Decididores**: Sebastian + cofounder/principal architect
- **Origen**: diagnóstico de P15 (`user_confirmation` marcado por interpretación del LLM),
  2026-08-23. Evidencia adicional: `analisis-2026-08-19-venta-bsuid-colision-operador.md`.

> ⚠️ **Nota de numeración**: el ROADMAP referencia un "ADR-010" para P10 (detección de
> conversaciones no-humanas) que **nunca se escribió**. Se aplica el mismo precedente que
> con ADR-008: el ADR que efectivamente se escribe toma el número. P10 tomará el siguiente
> libre cuando se escriba. Resolver la referencia del ROADMAP al mergear.

---

## Contexto

`user_confirmation` es el checkpoint que declara "el cliente aceptó su pedido". Hoy lo
propone el LLM interpretando el mensaje del cliente, y el gate del backend solo comprueba
*suficiencia de datos* (`full_name`, `phone`, `shipping_address`, `shipping_city`), no
*ocurrencia del acto*.

**Medición sobre el histórico completo (47 conversaciones, 616 mensajes)**: de las 5
conversaciones donde se marcó `user_confirmation`, **4 fueron falsos positivos — 80%**.

| conv | inbound que lo disparó | veredicto |
|---|---|---|
| 05-01 | `"2"` (una cantidad) | 🔴 falso |
| 07-15 | `"Si gracias"` tras resumen | ✅ legítimo |
| 07-20 | `"Unidad campestre sorry"` (corrección de dirección) | 🔴 falso |
| 08-01 | `"enviame una foto del producto"` | 🔴 falso |
| 08-19 | `"Barrio El Campin"` (fragmento de dirección) | 🔴 falso |

El caso más ilustrativo (08-19): el LLM devolvió, **en el mismo JSON**, el texto
`"…total aprox $87.000. ¿Todo bien con esos datos?"` y `user_confirmation: true`. Preguntó
y se auto-concedió la respuesta. La clienta no había visto el resumen: se estaba enviando
en ese momento.

**Dos agravantes medidos**:
1. `confirm_payment.py:78` — `user_confirmation` es la **única precondición** para que el
   botón del operador registre una venta. Un falso positivo no solo convoca al operador:
   habilita la escritura de la venta. Se materializó el 08-19: quedó en el perfil de la
   clienta una compra con `product_id: null` y `total: null`.
2. `agent_action.py:162` — el filtro `if k in STRATEGY_FIELDS and v` descarta los `false`
   por falsy. **No existe camino para desmarcar.** Una vez puesto, nada lo revierte.

**El hallazgo que decide el diseño**: no existe hoy ninguna señal determinista de que el
resumen se envió. Ni side_effect, ni campo, ni tipo de outbound, ni clave en contexto. El
resumen vive únicamente como texto libre dentro de un `response_text` que el LLM redactó.
**El backend no sabe que lo mandó**, así que no puede validar nada contra él.

## Decisión

**El backend es dueño de los datos operacionales que salen al cliente.** Concretamente,
seis decisiones que comparten una tesis: el LLM conserva la conversación; pierde la
aritmética y la declaración de hechos de negocio.

> **Alcance del cambio: 100% backend + una migración.** Este ADR NO toca n8n. Esa
> propiedad es deliberada: el fix del 80% de falsos positivos no debe arrastrar una sesión
> sobre el workflow vivo, que es la pieza más frágil del sistema (deuda #12, sin manejo de
> errores). Lo que exigía tocar n8n (mensaje propio del backend al escalar, que chocaba con
> el corte de ADR-009 §3) salió del alcance.

### 1. El backend renderiza y envía el resumen del pedido

Cuando el pedido tiene todos los datos necesarios y aún no hay confirmación, el backend
**renderiza el resumen él mismo** y su texto **reemplaza** el `final_response_text` del LLM
en ese turno.

- El backend ya tiene todo: `extracted_context` (producto, cantidad, molienda, nombre,
  teléfono, ciudad, dirección) + `products.price` + `business_rules.shipping_rules`.
- Plantilla en prosa corrida, no formulario. Sin guion largo (`—`): ese carácter es
  rarísimo en escritura casual de WhatsApp y delata al bot. Usar coma o punto.
- Ejemplo (Manizales): `"Va el pedido entonces: 2 bolsas de 340g en grano para Juan Pérez,`
  `al 3001234567, en Manizales, Cra 28 A # 48-30. El café son $80.000 y el envío $5.000,`
  `total $85.000. ¿Todo bien con esos datos?"`
- **Bilingüe**: ADR-008 estableció detección de idioma en backend. La plantilla necesita
  versión en inglés; el `live_language` del turno decide cuál se usa.

### 2. El backend calcula el total

`quantity × products.price + envío conocido`. Se elimina la aritmética del LLM. Hoy se le
pide a un modelo probabilístico que calcule dinero; acertó el 08-19, pero no hay garantía,
y un error ahí es un precio equivocado prometido a un cliente.

### 3. Reglas de envío (datos, no código)

Viven en `business_rules.shipping_rules` (JSONB — es edición de datos, no migración):
- **Manizales: $5.000 fijo.** Sin "aprox".
- **Medellin, Envigado, Sabaneta: $15.000 fijo.** Sin "aprox".
- **Resto de ciudades: por confirmar.** El resumen lo dice explícitamente ("el envío a
  Bogotá lo confirmamos contigo y te aviso el valor") y el operador lo coordina a mano.
- **No hay recogida en la finca.** Regla nueva; hoy el LLM improvisa si se lo preguntan.

> No se automatiza el cálculo de envío para otras ciudades. Se recolectan datos reales
> coordinando envíos a mano; cuando haya volumen suficiente, se decide si merece tabla
> propia. Automatizar antes de tener los datos sería inventar tarifas.

### 4. Reglas de presentación (sin escalamiento)

- **Bolsa de 340g** es la única presentación que el bot vende.
- **Libras, media libra, kilos sueltos: NO existen.** El bot informa que se vende en bolsas
  de 340g y **no hace conversiones**, ni ofrece calcularlas. Esto **elimina** la regla larga
  del prompt actual sobre equivalencias, que el modelo obedecía a medias.
- Si el cliente pide una presentación que no existe, el bot lo dice y sigue. **Sin
  detección especial, sin escalamiento, sin tocar n8n.**

> Los pedidos por encargo o volumen (cuarterón de 2.5 kg, cantidades altas) quedan **fuera
> de este ADR**: no hay evidencia de que sean una necesidad recurrente del negocio, y
> construir detección + escalamiento para un caso no validado es trabajo que después se
> borra. Registrado como frente futuro, no como decisión. Ver "Fuera de alcance".

### 5. La confirmación: el LLM juzga el lenguaje, el backend juzga el contexto

El LLM sigue proponiendo `user_confirmation` (juzgar si un enunciado es una afirmación
**es** una tarea de lenguaje), pero el gate del backend solo la acepta si se cumplen
**todas** estas condiciones deterministas:

1. **Hubo resumen**: el backend presentó un resumen en esta conversación.
2. **El resumen sigue vigente**: el estado del pedido no ha cambiado desde que se presentó
   (comparación por fingerprint, ver Mecánica).
3. **El mensaje llegó después**: el inbound que disparó este turno tiene timestamp
   posterior al envío del resumen.
4. **Este turno no modifica el pedido**: si el mismo turno trae cambios en campos del
   pedido, se invalida en vez de confirmar (ver §6).

**Verificación contra los 4 falsos positivos históricos** — las condiciones los atajan
todos, y cada uno por una razón distinta:

| caso | por qué se rechaza |
|---|---|
| 05-01 `"2"` | el turno modifica `quantity` (cond. 4); no había resumen (cond. 1) |
| 07-20 `"Unidad campestre sorry"` | el turno modifica `shipping_address` (cond. 4) |
| 08-01 `"enviame una foto"` | el inbound entró **795 ms ANTES** de que el resumen se persistiera (cond. 3) |
| 08-19 `"Barrio El Campin"` | no existía resumen previo del backend (cond. 1) |

Y el único caso legítimo (07-15, `"Si gracias"` tras el resumen, sin cambios) **pasa**.

### 6. Cualquier modificación invalida la confirmación

Si el cliente cambia un campo del pedido (cantidad, dirección, molienda, teléfono, ciudad,
producto) después de que se presentó el resumen:
- el backend **actualiza el pedido y el estado persistido**,
- **limpia `user_confirmation`** si estaba puesto (invalidación determinista — resuelve el
  defecto del desmarcado imposible de `agent_action.py:162`),
- el fingerprint cambia → el resumen anterior queda obsoleto,
- el backend **renderiza un resumen nuevo** en ese mismo turno.

Ciclo: `datos completos → resumen → (modificación → resumen nuevo)* → confirmación`.

Nunca se cierra una venta sobre un resumen que ya no refleja lo que el cliente pidió. Costo
aceptado: un turno extra cuando alguien corrige algo.

## Mecánica

**Estado nuevo en `conversations`** (migración; columnas propias, NO en `extracted_context`):
- `order_summary_fingerprint VARCHAR` — hash de los campos del pedido tal como se
  presentaron.
- `order_summary_sent_at TIMESTAMPTZ` — cuándo se envió.

Razón de columnas y no JSONB: son hechos **propiedad del backend**, no slots propuestos por
el LLM. Mezclarlos en `extracted_context` —donde se mergean las propuestas del modelo—
recrea el riesgo que obligó a inventar `OPERATOR_ONLY_FIELDS`. Además hace el fingerprint
consultable.

**El fingerprint** cubre los campos que aparecen en el resumen: `product_id`, `quantity`,
`grind_preference`, `full_name`, `phone`, `shipping_city`, `shipping_address`, y el envío
aplicado. Cualquier cambio en cualquiera de ellos produce un fingerprint distinto.

**H6 se resuelve estructuralmente**: el resumen necesita el precio para calcular el total, y
el precio necesita `product_id`. **No hay resumen sin producto resuelto**, y por tanto no hay
confirmación sin producto. El parche de añadir `product_id` a `_USER_CONFIRMATION_REQUIRES`
deja de hacer falta.

**Costo de tokens conocido**: en el turno del resumen, el LLM genera una respuesta que se
descarta. Aceptado para v1. Optimización futura (fuera de alcance): cortocircuitar en el
ingest para no llamar al LLM en ese turno.

## Alternativas consideradas

**A. Filtro léxico sobre el inbound disparador** ("¿el cliente dijo algo afirmativo?").
Ataja 4/4 históricos, costo bajo, sin migración. **Rechazada como solución principal**: no
distingue *sobre qué* se confirma. Si el cliente dice "sí pero ponme 3 bolsas", el filtro ve
un "sí" y acepta una confirmación sobre un pedido que acaba de cambiar. Además tiene un
falso positivo demostrado en los propios datos: `"Si quieres yo lo recojo"` (08-19, 20 s
después del resumen) pasaría cualquier filtro de prefijo afirmativo.

**B. Regex sobre outbounds previos buscando el resumen** (`"total aprox"`). Ataja solo 2/4.
Rechazada: acopla el gate a la redacción del prompt; una migración de prompt lo rompe en
silencio.

**C. Que el LLM emita `order_summary_presented: true`.** Rechazada: es exactamente la misma
clase de bug —otro hecho afirmado por interpretación—. Resolver un problema de confianza en
el LLM pidiéndole otra afirmación al LLM.

**D. No hacer nada.** Rechazada: 80% de falsos positivos sobre el checkpoint que habilita el
registro de ventas, con daño ya materializado (venta sin producto ni total, 08-19).

**E. Que el backend juzgue también el lenguaje de la confirmación** (sin LLM). Rechazada por
ahora: juzgar si un enunciado es una afirmación es tarea de lenguaje, y es donde el LLM
aporta valor. La decisión es acotar *cuándo* su juicio puede aceptarse, no reemplazarlo.

## Consecuencias

**Positivas**
- Elimina la causa estructural del 80% de falsos positivos, no el síntoma.
- Crea la señal determinista que hoy no existe (que el resumen se envió, y de qué versión).
- Resuelve H6 (`product_id`) y el desmarcado imposible **como consecuencia**, sin parches.
- Quita al LLM la aritmética de dinero: el total deja de depender de que el modelo acierte.
- Elimina del prompt la regla larga de conversiones de peso (prompt más corto → mejor
  adherencia al resto, alineado con P21).
- Tercera aplicación consistente de la misma tesis (tras `payment_confirmation` y la
  detección de idioma): los hechos y cálculos de negocio los gobierna el backend.
- **Cero cambios en n8n.** El fix entra sin abrir una sesión sobre el workflow vivo, que es
  donde está el mayor riesgo operativo del sistema.

**Negativas / costos**
- Migración nueva (dos columnas) + lógica de fingerprint e invalidación.
- El backend pasa a redactar un mensaje que va al cliente: la plantilla debe cuidarse
  (tono, bilingüe es/en) o se sentirá robótica justo en el momento más importante.
- Tokens desperdiciados en el turno del resumen (el LLM genera y se descarta).
- **Riesgo residual**: un mensaje que suene afirmativo pero no lo sea, dentro de la ventana
  válida (resumen vigente, sin cambios, posterior). Las condiciones lo acotan mucho pero no
  lo eliminan. Mitigación futura: medir en shadow y decidir si hace falta endurecer.

## Fuera de alcance

- **Pedidos por encargo / volumen** (cuarterón de 2.5 kg, cantidades altas). Descartado
  conscientemente: el negocio **no ha validado** que sea una necesidad recurrente, y no hay
  ningún caso en el histórico. Construir detección y escalamiento para un caso no observado
  es trabajo que después se borra. Además arrastraba la única parte que exigía tocar n8n.
  Cuando llegue el primer cliente que pida volumen y se atienda a mano, ahí habrá evidencia
  y un umbral real. **Recomendación registrada para cuando se abra**: disparar por
  `quantity` anormalmente alta (campo que el backend ya tiene y valida) en vez de por
  keywords de producto: es determinista, cubre parafraseos ("para mi cafetería", "para un
  evento" terminan todos en un número) y no añade claves al prompt ni a la allowlist de n8n.
  El comportamiento sería silencio + `human_handoff` + aviso Telegram, que ya existe y está
  verificado; sin mensaje propio del backend, para no chocar con el corte de ADR-009 §3.
- **Canal de entrada de datos del operador**: hoy el operador solo tiene una acción binaria
  (botón de pago). No puede aportar un dato (p. ej. el valor real del envío a Bogotá). Se
  coordina a mano. Registrar como necesidad futura.
- **Automatización del costo de envío** por ciudad/cantidad: esperar datos reales.
- **Cortocircuito del LLM** en el turno del resumen (optimización de tokens).
- **P21** (rediseño del prompt/directive): este ADR reduce el prompt pero no lo rediseña.

## Cuándo revisar

- Si aparecen falsos positivos dentro de la ventana válida (afirmación aparente que no lo
  era) → considerar endurecer con validación adicional del disparador.
- Si el resumen renderizado se percibe robótico en conversaciones reales → ajustar plantilla
  antes que devolverle la redacción al LLM.
- Cuando haya datos suficientes de envíos reales → decidir si las tarifas merecen tabla.
