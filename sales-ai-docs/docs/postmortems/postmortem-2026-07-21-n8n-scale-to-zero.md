# Postmortem — Mensajes perdidos por scale-to-zero de n8n 2026-07-21

- **Tipo**: registro inmutable de un evento real. No se edita.
- **Fecha del evento**: 2026-07-21
- **Componente**: Container App de **n8n** (`ca-r8fm-n8n`, RG `rg-r8fm`) — NO el backend.
- **Severidad**: alta (mensajes de cliente perdidos sin rastro ni alerta).

> Nota de carriles: este documento REGISTRA lo que pasó. El fix ya está aplicado en vivo
> (infra, reversible). No mezclar con decisiones de producto.

---

## Resumen

Un cliente real (identificado en WhatsApp como "Jd") escribió dos veces a la línea de Café
Arenillo. El bot respondió el primer mensaje y **perdió el segundo por completo**: nunca
generó una ejecución en n8n, nunca llegó al backend, nunca hubo respuesta. La causa es la
**misma clase de fallo del stand 2026-06-16 (scale-to-zero), pero en un componente que
aquel fix no tocó**: en junio se puso `minReplicas=1` solo en el backend (`ca-backend`);
el Container App de **n8n** quedó en `minReplicas=0` desde siempre. No es regresión: nunca
estuvo cubierto.

---

## Incidente (resuelto en vivo)

**Síntoma**: cliente escribió, recibió UNA respuesta, y sus mensajes siguientes se
quedaron sin respuesta. Desde el lado del negocio: "el bot respondió una vez y después
nada".

**Timeline (UTC)**:
- `21:43:44` — Jd envía "Cómo estás?" (por la línea de Café Arenillo). n8n lo
  procesa (master exec `8588` → `cafe_arenillo_v2` exec `8589`), el backend responde,
  Chakra entrega "Bien, gracias. ¿Y tú? Soy Sebastian, de Café Arenillo…". ✓
- `21:44` — dos callbacks de estado de WhatsApp (payload con campos `null`) → cortan
  correctamente en `If Message Exists` → Stop.
- `21:44 – 22:23` — **39 minutos sin NINGUNA ejecución** en la instancia n8n (dormida).
- `22:16:24` — Jd envía "Qué productos tienes?" (mismo `phone_number_id`). **Llega a
  Chakra pero NO genera ninguna ejecución en n8n.** Mensaje perdido.
- `22:23:55` — un mensaje de prueba interno despierta la instancia y responde normal.

**Causa raíz**: `ca-r8fm-n8n` tenía `minReplicas` sin definir (=0) → escalaba a cero tras
~5 min sin tráfico. Cuando el 2º mensaje llegó (tras 33 min de inactividad), n8n estaba
frío: durante el cold-start el webhook aún no está registrado (404 / connection-refused)
y Chakra no reintenta con éxito. El mensaje se pierde sin dejar ejecución, error ni
alerta. El 1er mensaje sobrevivió porque su cold-start alcanzó a completar antes de que el
cliente/Chakra desistiera; es intermitente por naturaleza.

**Descarte del enrutamiento**: ambos mensajes eran idénticos en routing (mismo
`phone_number_id` de la línea de Café Arenillo, mismo cliente). El Switch del `master` en vivo enruta
ese ID correctamente a `cafe_arenillo`. El 1º lo probó de punta a punta. El problema no
estaba dentro de n8n ni del backend, sino en que el 2º **nunca entró**.

**Evidencia**:
- `az containerapp show -n ca-r8fm-n8n -g rg-r8fm` → `minReplicas: null`, `maxReplicas: 10`.
- Log de Chakra con el 2º mensaje ("Qué productos tienes?").
- Cero ejecuciones de cualquier workflow entre `21:44` y `22:23` UTC en la instancia.
- Backend (`ca-backend`) en `minReplicas: 1` (rev 47) — el fix de junio sigue intacto,
  no revirtió.

**Fix aplicado en vivo**:
`az containerapp update -n ca-r8fm-n8n -g rg-r8fm --min-replicas 1 --max-replicas 1`
→ revisión `ca-r8fm-n8n--0000009`, Succeeded. Una réplica caliente permanente; cold
starts eliminados. `maxReplicas` bajado de 10 → 1 a propósito: n8n en **modo regular**
(sin queue mode) NO debe correr multi-réplica — varias réplicas duplican registro de
webhooks, ejecuciones y schedule triggers. Reversible.

---

## ¿Bug nuevo o regresión? — Ninguna: componente nunca cubierto

El postmortem `stand-2026-06-16.md` describió y arregló este mismo tipo de fallo, pero:

| | Stand 2026-06-16 | Este evento 2026-07-21 |
|---|---|---|
| Componente | Backend `ca-backend` | n8n `ca-r8fm-n8n` |
| Cold start | ~17s: imagen + Python + Key Vault + Postgres + Uvicorn | arranque de n8n + registro de webhooks |
| Fix aplicado a | `ca-backend--0000036` (`minReplicas=1`) | nunca se aplicó hasta hoy |
| Estado hoy | sigue en `minReplicas=1` (intacto) | estaba en `0` |

El fix de junio fue **solo al backend**. n8n es un Container App aparte con su propio
scale-to-zero, y nunca se le puso `minReplicas`. El propio postmortem de junio encuadró a
n8n como *receptor* de "connection-refused/timeout" (víctima del cold start del backend),
lo que ocultó que n8n tenía su propia ventana fría. La evidencia de aquel día (KEDA
`Scaled from 0 to 1`, `startup probe failed`) venía de los logs del backend y apuntaba
hacia allá.

**Por qué no saltó antes**:
1. Intermitente: solo falla si un mensaje aterriza justo en la ventana fría de n8n tras un
   periodo de inactividad. Con tráfico, n8n se mantiene caliente.
2. Sin telemetría de fallos silenciosos n8n→backend (deuda técnica #3): un mensaje que
   muere en la puerta de n8n no deja ejecución, error ni alerta. Pudo estar ocurriendo de
   forma esporádica sin que nadie lo notara.

---

## Lección

El mismo modo de fallo puede vivir en **cada** componente con scale-to-zero de la cadena,
no solo en el que se depuró primero. Arreglar la pieza visible (el backend, donde estaban
los logs) dio falsa sensación de "scale-to-zero resuelto" a nivel de sistema, cuando la
puerta de entrada (n8n) seguía expuesta. Al cerrar un fallo de infra, auditar el mismo
patrón en TODA la ruta del mensaje (Chakra → n8n → backend), no solo donde apareció la
evidencia.

---

## Pendientes (derivados, no bloquean el fix)

- **Telemetría / deuda #3**: sin observabilidad, el próximo drop silencioso —de n8n, de un
  409, de lo que sea— tampoco se verá. Es la causa de fondo de por qué esto se descubre
  tarde. Priorizar alertas de fallas silenciosas n8n→backend y Log Analytics histórico.
- **Reintentos de Chakra**: verificar si el webhook de Chakra reintenta entregas fallidas.
  Con `minReplicas=1` la ventana fría desaparece, pero un reintento haría el sistema
  resistente ante cualquier caída transitoria futura.
- **Buffer durable (norte)**: Chakra → cola → n8n desacoplaría la recepción del
  procesamiento y garantizaría cero pérdida ante indisponibilidad de n8n. Alternativa:
  n8n en queue mode con workers.
- **Cliente Jd**: su 2º mensaje se perdió del todo (sin estado pendiente ni reintento). El
  bot nunca lo responderá — requiere respuesta manual.

---

## Limitación de evidencia

Sin Log Analytics histórico en el environment (deuda #3), la reconstrucción se hizo con:
ejecuciones de n8n (API), el payload de Chakra aportado a mano, y la config de escalado de
los Container Apps vía `az`. No hay traza del intento de entrega de Chakra a n8n en el
momento exacto del cold-start; el "no reintenta con éxito" es inferencia consistente con
la ausencia total de ejecución para un mensaje que sí llegó a Chakra.
