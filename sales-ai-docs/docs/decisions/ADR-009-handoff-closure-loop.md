# ADR-009 — Cierre del lazo de handoff humano (confirmación de pago, notificación y corte)

- **Estatus**: Aceptado (implementado 2026-07-19/20 — ver Notas de implementación)
- **Fecha**: 2026-07-19
- **Decididores**: Sebastian + cofounder/principal architect
- **Origen**: postmortem de la primera venta cerrada (`docs/postmortems/`), que reveló
  que el tramo final del flujo de venta nunca ha corrido solo.

> Numeración: resuelta — 009 quedó para esta decisión sin colisión (`purchase_intents`,
> citado informalmente como "008" en ADRs viejos, tomará el siguiente número libre
> cuando se decida; ver nota en ADR-008).

---

## Contexto

El postmortem de la primera venta cerrada estableció, con evidencia dura, que el core de
recolección+validación funciona y es reproducible, PERO el tramo de cierre no existe
operativamente:

- `payment_confirmation` NUNCA se propuso ni persistió en la venta real. El DAG se detuvo
  en `user_confirmed`. El cierre (pago, envío) ocurrió 100% fuera del sistema.
- La venta NO se registró en `client_users.profile` (sin `payment_confirmed`, el registro
  en agent_action no se escribe). La primera clienta quedó sin historial de compra.
- En la única conversación con `human_handoff` real, hubo **7 outbounds DESPUÉS** de la
  transición: n8n NO respeta el estado `human_handoff` — el corte de respuesta no existe.
- La venta cerró porque el humano estaba mirando WhatsApp en vivo. Sin esa presencia, se
  habría perdido en "te comparto los medios de pago".

**Decisión de producto previa (vigente)**: el pago se valida por HUMANO, por fuera del
sistema (el comprobante es una imagen que el sistema no ve ni persiste). No hay
automatización de validación de pago, y está bien. Pero eso deja un hueco: no existe
mecanismo para que el humano le comunique al sistema "el pago está confirmado".

## El problema central

`payment_confirmed` es un checkpoint arquitectónicamente ÚNICO en el DAG: su verdad vive
FUERA del sistema entero — en el ojo de un humano mirando un comprobante. No lo puede
proponer el LLM (el cliente diciendo "ya pagué" no es prueba) ni validar el backend (no
ve la imagen). Es el punto donde el flujo determinista se encuentra con un juicio humano
irreductible. Se necesita un **canal de vuelta** del humano al sistema, que hoy no existe.

## Decisión

Construir el lazo completo de cierre de handoff con arquitectura **endpoint-como-verdad
+ Telegram-como-piel**:

### 1. El endpoint es la verdad (mecanismo estable)

Nuevo endpoint autenticado, p.ej. `POST /api/v1/agent/confirm-payment`, con
`conversation_id` (o `intent_id`). Al invocarse por un operador autorizado:
- marca `payment_confirmed` en el contexto,
- registra la venta en `client_users.profile` (purchases[], purchase_count,
  lifecycle_stage) — cierra el hueco de la clienta sin historial,
- transiciona la conversación a `closed`,
- emite side_effect `sale_closed` en audit_log.

**Auth como requisito de PRIMERA CLASE (no detalle)**: este endpoint es, en efecto, el
botón "una venta se pagó". Si se dispara sin autorización, marca ventas falsas y corrompe
datos de negocio. Mínimo: Bearer token de servicio (como el resto). Además, restringir a
que solo el operador legítimo (vía el bot de Telegram autenticado) pueda dispararlo — el
token del bot de Telegram no debe ser el mismo token de servicio genérico. Diseñar la
auth explícitamente en implementación.

### 2. Telegram es la piel (canal reemplazable)

El endpoint no sabe de Telegram; Telegram es solo cómo el operador lo dispara. Si mañana
se cambia de canal, el endpoint no cambia. Dos momentos de notificación:

- **Aviso pre-pago** (cuando el DAG llega a `user_confirmed` — pedido y datos completos,
  falta solo pagar): Telegram recibe "venta lista para cerrar, cliente por pagar, entra a
  acompañar y revisar el comprobante" + link/ref a la conversación. El operador entra a
  tiempo, NO después de que el sistema ya dio el pago por bueno.
- **Confirmación post-revisión**: tras revisar el comprobante con sus ojos, el operador
  pulsa un botón en Telegram que dispara el endpoint (§1). Ese es el único acto que marca
  `payment_confirmed`.

### 3. Corte de respuesta en n8n (arreglo, no construcción)

Incluido en este proyecto porque el lazo no cierra sin él. Hoy n8n manda
`final_response_text` sin chequear `approved` ni `conversation_state`. Cambio: n8n debe
NO enviar al cliente si la conversación está en `human_handoff` o `closed`. Esto arregla
el bug de los 7 outbounds post-handoff y hace efectivo TODO escalamiento (venta, P8,
futuro). Toca n8n vivo → sesión con export antes/después.

### 4. Cierre y reactivación

Al confirmar el pago, la conversación pasa a `closed`. El bot no vuelve a responder EN ESA
conversación. La reactivación para una PRÓXIMA venta NO requiere mecanismo nuevo: cuando
el cliente vuelve a escribir, el `find/create conversation` del ingest crea una
conversación NUEVA (la vieja está closed / pasó la ventana 24h) que nace en `active`. El
estado terminal vive en la CONVERSACIÓN, no en el `client_user`. `closed` cierra la
VENTA, no la RELACIÓN.

## Alternativas consideradas

**A. Palabra clave en WhatsApp (operador escribe /confirmar en el chat).** Rechazada:
mezcla mensajes de operador con mensajes de cliente en el mismo canal; frágil de parsear;
riesgo de que el cliente escriba el comando. El endpoint dedicado es más limpio.

**B. Que el LLM marque payment_confirmed cuando el cliente dice "ya pagué".** Rechazada:
viola la decisión de producto (el pago lo valida un humano) y la tesis del sistema (no se
confía en el LLM para verdad de negocio). "Ya pagué" no es prueba de pago.

**C. Tool de extracción de imagen del comprobante (OCR + validación de monto/fecha).**
Rechazada en esta etapa (decisión previa): sube costo de tokens, requiere que el sistema
vea imágenes (no lo hace), y le da al humano datos pre-masticados por IA menos confiables
que su propia revisión del comprobante crudo. Reevaluable a volumen alto.

**D. Cerrar dejando la conversación en human_handoff (no closed).** Rechazada: el humano
termina la interacción (envío, etc.) por FUERA del bot; mantener la conversación "viva"
en handoff no aporta y complica la reactivación. `closed` es más limpio y la reactivación
por conversación nueva ya está soportada.

## Consecuencias

**Positivas:**
- El sistema puede cerrar ventas SIN que el operador vigile WhatsApp en vivo — convierte
  la venta afortunada en flujo reproducible. Es la brecha del postmortem.
- Registra la venta en el profile → habilita (a futuro, no ahora) el reconocimiento del
  cliente recurrente / fidelización (ADR de memoria de relación, en pausa).
- Arregla el corte de respuesta roto → hace efectivo TODO escalamiento (venta, P8).
- Endpoint estable + canal reemplazable → Telegram se puede cambiar sin tocar la lógica.

**Negativas / costos:**
- Es el proyecto más grande desde el inicio: endpoint + auth + bot de Telegram con
  callbacks + arreglo de n8n + registro de venta. Cuatro piezas, dos en zonas sensibles
  (n8n vivo, auth de dinero). La implementación DEBE descomponerse en varios commits
  pequeños, no hacerse de una.
- Introduce el primer canal de salida hacia el operador (Telegram) → nueva pieza
  operacional que mantener (token del bot, secreto en Key Vault).
- El aviso pre-pago depende de detectar `user_confirmed` de forma fiable → verificar que
  ese checkpoint se marca consistentemente antes de conectar la notificación.

## Plan de implementación (baby steps — NO de una)

1. Endpoint `confirm-payment` (sin Telegram todavía): marca payment_confirmed + registra
   venta en profile + cierra. Auth con Bearer. Tests de contrato (incl. auth rechaza no
   autorizado). Se puede disparar a mano (curl/Postman) para probar.
2. Arreglo del corte en n8n: no enviar si human_handoff/closed. Sesión n8n con export
   antes/después. Verificar con una conversación de prueba que el bot se calla.
3. Registro de venta en profile: verificar shape (purchases con quantity/total — reusa lo
   de P2). Test de que una confirmación puebla el profile correctamente.
4. Notificación Telegram — aviso pre-pago (user_confirmed → mensaje al operador).
5. Notificación Telegram — botón de confirmación que dispara el endpoint (§1).
6. End-to-end con un cliente real de prueba: pedido → user_confirmed → aviso Telegram →
   operador revisa → botón → payment_confirmed → venta registrada → bot callado → closed.

Cada paso: un commit, su test donde aplique, verificado antes del siguiente. Los pasos 1-3
son backend (tu terreno fuerte); 2 toca n8n; 4-5 son la pieza nueva de Telegram.

## Notas de implementación (as-built, 2026-07-19/20)

Decisiones tomadas durante la implementación (confirmadas por el decisor):

1. **Ruta y auth**: `POST /api/v1/operator/confirm-payment` bajo namespace `/operator/*`
   propio, con `SALES_AI_OPERATOR_TOKEN` **escopado por path en el middleware**: el token
   de servicio no abre `/operator/*` ni el de operador abre agent/ingest; sin token
   configurado la superficie falla cerrada (500). Secretos: `sales-ai-operator-token` y
   `telegram-bot-token` en Key Vault; env var cableada en el Container App vía secretref
   + managed identity.
2. **Idempotencia**: re-confirmar una venta ya cerrada → `200 {already_confirmed: true}`
   sin duplicar `purchases` (seguro para double-tap/reintentos de Telegram).
3. **Precondición estricta**: sin `user_confirmation` en el contexto → `409
   order_not_confirmed` (protege contra confirmar la conversación equivocada, p.ej. un
   handoff por circuit breaker con contexto vacío). Una conversación `closed` por otra
   vía no es re-confirmable (`409 invalid_state`).
4. **La piel vive en n8n** (cero dependencias nuevas en backend): aviso en
   `cafe_arenillo_v2` + workflow `operator_confirm_telegram` (Telegram Trigger →
   validación → endpoint). Tokens como credenciales n8n (`telegram_arenillo_bot`,
   `sales_ai_operator_auth`).
5. **Extensión al §2 pedida por el decisor**: el aviso al operador dispara por
   `checkpoint_completed:user_confirmed` (venta lista, con botón "Confirmar pago") **y**
   por `circuit_breaker:loop_detected` (loop P8, solo aviso) — cierra el hueco de que el
   breaker escalaba sin notificar.
6. **Gatillo del aviso pre-pago**: side_effect `checkpoint_completed:user_confirmed`
   emitido solo en la TRANSICIÓN (helper `is_new_user_confirmation`) — el LLM re-propone
   el contexto acumulado cada turno y sin ese guard se re-notificaría cada turno.
7. **Corte en n8n (§3), as-built**: (a) `IF Should Respond` corta antes del LLM si
   `conversation_state ∈ {human_handoff, closed}`; (b) `Process Backend Response` computa
   `should_send = approved && new_state ∉ {human_handoff, closed} && texto no vacío` y
   expone `suppressed_reason`; (c) la rama false de `IF Approved to Send` se recableó a
   `IF Escalated` para que un turno suprimido por escalamiento NO pierda la notificación
   al dueño (regresión detectada y evitada durante la implementación).
8. **Seguridad del callback**: se autoriza contra el `from.id` de quien PULSA el botón
   (chat_id del operador, validado en `Validate Operator`), no solo se usa como destino;
   fail-closed. Webhook del bot registrado por n8n al activar el trigger.

Verificación pendiente: paso 6 (e2e con pedido real) tras el merge/deploy del backend.
Exports as-built en `n8n_workflow/` (`cafe_arenillo_v2.json`, `operator_confirm_telegram.json`,
snapshot pre-corte `cafe_arenillo_v2.pre-adr009-cut.json`).

## Cuándo revisar

- Si el volumen de ventas crece al punto de que la revisión manual de comprobantes es
  cuello de botella → reevaluar la tool de extracción (alternativa C).
- Si Telegram resulta incómodo para el operador → cambiar la piel sin tocar el endpoint.
- Si se decide construir la fidelización / memoria de relación → este ADR ya dejó la
  venta registrada en el profile como prerequisito cumplido.
