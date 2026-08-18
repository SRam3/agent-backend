# Brief de implementación — P14: mensajes LID/privacidad (BSUID) — recibir + responder

> **Definitivo.** Reemplaza el brief anterior (que quedó en stand-by esperando a Chakra).
> Chakra CONFIRMÓ soporte del campo `recipient` para responder a BSUIDs, así que este
> brief cubre recibir Y responder en un solo trabajo. SIN telemetría-de-derrota (no se
> construye el parche de "no pude atender"): el sistema recibe y responde, flujo completo.
>
> **Sistema REAL**: FastAPI en prod, Postgres 6 tablas post-007, n8n vivo (`master`
> , `cafe_arenillo_v2`), Chakra HQ transporte.
> Edita código, schema (migración), n8n vivo.
>
> **Alcance**: que los mensajes de clientes con privacidad ENTREN, se identifiquen por
> BSUID, se persistan, y se pueda RESPONDERLES vía BSUID. Identidad técnica por BSUID.
> **FUERA**: el rediseño de identidad primaria de negocio (BSUID como clave de negocio +
> teléfono recolectado en el DAG como dato de envío + manejo de user_id_update) → su
> propio ADR posterior (ver "Lo que sigue" al final).
>
> **Disciplina**: fases por riesgo. Plan por archivo antes de codear, espera confirmación.
> Cada fase su commit + test. Ramas desde origin/main (fetch primero). Export n8n
> antes/después. NO desplegar sin OK.

---

## Contexto y hallazgos confirmados

**Problema**: WhatsApp desplegó privacidad de número (BSUID). Para clientes con privacidad
activada, Meta omite `from`/`wa_id` y manda solo el BSUID (`CO.1034…`). El sistema perdía
estos mensajes en silencio. **Evidencia**: clienta real "Juan Perez"
(`CO.XXXXXXXXXXXXXXXX`) perdida el 2026-08-01, drop silencioso (Stop "success" 15ms).

**Confirmado contra el payload REAL (diagnóstico exec 9459 vs 9461)**:
1. Los campos BSUID YA llegan — formato pass-through verbatim de Meta. `user_id` y
   `from_user_id` presentes en TODOS los mensajes. NO hay que cambiar webhook ni pedir
   nada a Chakra para recibir. El problema es de parsing propio.
2. La única diferencia entre payload normal y de LID: faltan `wa_id` y `from`.
3. **Causa PRIMARIA**: `master` / `map_webhook_data_arenillo` (nodo `Set` whitelist)
   copia solo 6 campos y DESTRUYE `from_user_id`, `user_id`, `username`, `wa_id` antes de
   procesar. Arreglar solo el `If` NO funcionaría.
4. **Causa secundaria**: `cafe_arenillo_v2` / `If Message Exists` valida `from` strict → Stop.

**Confirmado por Chakra Support**:
- Soportan el campo `recipient` en sus APIs pass-through de envío → **se puede responder a
  un BSUID sin teléfono.**
- **Los BSUID están scoped a un solo WABA ID.** El mismo cliente físico tiene un BSUID
  DISTINTO por cada WABA/tenant. → requisito multi-tenant duro (abajo).

## Rutas de campos (verificadas contra payload real)

Bajo `body.entry[0].changes[0].value`:
- **Identidad (BSUID)** — 100% presente: `messages[0].from_user_id` (= `contacts[0].user_id`)
- **Teléfono** — OPCIONAL, puede faltar, NUNCA clave: `messages[0].from` (= `contacts[0].wa_id`)
- **Username** — display: `contacts[0].profile.username`
- **Nombre** — NO garantizado.

## Dos sutilezas OBLIGATORIAS

1. **El guard discrimina PRIMERO mensaje-vs-status.** Los status callbacks
   (`value.statuses[]`, sin `value.messages[]`) DEBEN seguir cayendo a Stop. Exigir
   `value.messages[0].id` antes de extraer identidad. NO solo cambiar `from`→`from_user_id`.
2. **NO usar `name`/`username` como condición** de validez — no garantizados. Sí como display.

## Requisito multi-tenant DURO (confirmado por Chakra)

El BSUID es scoped por WABA. El mismo cliente tiene BSUID distinto por tenant. Por tanto:
- La unicidad del `bsuid` es **por tenant: `(client_id, bsuid)`**, NUNCA global.
- El BSUID identifica "esta persona ante ESTE negocio", no "esta persona". No usarlo como
  identidad cross-tenant. (Meta lo diseñó así por privacidad — es correcto, no una
  limitación a sortear.)

---

## FASE 1 — Schema (migración) [DB]

**Decisión**: campo `bsuid` propio + `phone_number` opcional (NO meter BSUID en
`phone_number` — miente sobre lo que guarda; además es `VARCHAR(20)`, corto para BSUIDs).

> **Nomenclatura**: la columna real es **`client_users.phone_number`** (`core.py:74`). Ojo
> con `phone`: es OTRA cosa — el slot del DAG en `lead_qualified`, que vive en
> `extracted_context`, no una columna. En este brief `phone_number` = columna,
> `phone` = slot del DAG.

- Migración nueva (secuencial, `-- Applied:` en blanco, NO aplicar en sesión):
  - `bsuid VARCHAR(140)` en `client_users` (country code + `.` + hasta 128 chars; 140 da
    margen).
  - `phone_number` NULLABLE (quitar NOT NULL). La identidad ya no es el teléfono.
  - Índice único **compuesto `(client_id, bsuid)`** — scoping por tenant (requisito duro).
- **NO dropear `uq_client_user_phone`** (el `UNIQUE (client_id, phone_number)` de 001).
  Corrección sobre la versión previa de este brief, que pedía quitarlo: el código vivo lo
  referencia por nombre (`ingest.py:121` — `on_conflict_do_update(constraint=...)`), así que
  dropearlo rompe cada ingest hasta desplegar la Fase 2. Y sigue siendo útil: con
  `phone_number` nullable los NULL no colisionan entre sí, así que conserva su invariante
  solo entre las filas que sí tienen teléfono. Dejarlo hace la migración retrocompatible.
  Retirarlo, si algún día hace falta, es cosa de la fusión phone↔BSUID → ADR posterior.
- NO tocar lógica de identidad compleja (user_id_update, fusión) — es el ADR posterior.

## FASE 2 — Backend: identidad técnica por BSUID [B]

- Ingest/upsert de `client_user` usa `bsuid` como clave dentro del tenant (`client_id`).
  BSUID-first. Si hay teléfono, se guarda como atributo; si no, null.
- Resolución: buscar por `(client_id, bsuid)`. Si no existe, crear con el BSUID.
  (Fusión phone↔BSUID y write-back = ADR posterior; aquí solo BSUID como clave.)
- El upsert deja de inferir por `uq_client_user_phone` y pasa a `(client_id, bsuid)`
  (índice `uq_client_users_client_bsuid` de la migración 012, completo y no parcial
  justamente para que el `ON CONFLICT` infiera sin repetir predicado).
- Pydantic: `from`, `wa_id`, `phone_number` OPCIONALES. Su ausencia no rompe validación.
- Dedupe por `chakra_message_id` (wamid), no por teléfono (verificar que ya es así).
- Tests (puros): payload con BSUID sin teléfono → identifica/crea por bsuid; con ambos →
  guarda los dos; ausencia de `from`/`wa_id` no rompe.

## FASE 3 — n8n: recibir [N8N] (export antes/después)

- **`master` / `map_webhook_data_arenillo`** (el `Set`): añadir a los campos copiados
  `from_user_id`, `user_id`, `username`, `wa_id`. **Fix PRIMARIO** — sin esto la identidad
  se sigue destruyendo aquí.
- **`cafe_arenillo_v2` / `If Message Exists`**: guard discrimina mensaje-vs-status
  (exigir `value.messages[0].id`) y usa `from_user_id` como identidad, no `from`. Status
  callbacks siguen a Stop.
- **`Normalize Inbound`**: usar `from_user_id` como identidad; `from` como teléfono
  opcional (`phone = message.from || null`, no `|| ''`).

## FASE 4 — n8n: responder vía BSUID [N8N] (Chakra confirmó)

- El nodo de envío hoy usa `"to": phone_number`. Cambiar a: **si hay teléfono, `to`; si
  NO, `recipient: <bsuid>`** (omitir `to`). Chakra pasa `recipient` en su API pass-through.
- Usar el BSUID completo verbatim (country code + `.` + todo).
- Respetar la ventana de 24h (el inbound del cliente la abre, sea por teléfono o BSUID) —
  responder texto libre dentro de ella; ya es el caso de uso normal.
- (Plantillas de autenticación NO soportan BSUID y requieren teléfono — no aplica hoy,
  solo texto libre en ventana. Anotar por si acaso.)

---

## Secuencia y verificación

**Orden**: FASE 1 (schema) → FASE 2 (backend) → FASE 3 (n8n recibir) → FASE 4 (n8n
responder). Recibir antes que responder (no puedes responder a quien no identificaste).

**Verificación e2e**: mandar un mensaje real desde un WhatsApp con privacidad de número
activada (o simular payload sin `from`/`wa_id`). Confirmar el flujo COMPLETO: el mensaje
ENTRA → se identifica/crea client_user por `(client_id, bsuid)` → se persiste → **el bot
RESPONDE** (vía `recipient`) → la conversación fluye normal. Revisar la DB, no solo la
conversación (los bugs se esconden en el estado).

**Definition of done**:
- Un mensaje solo-BSUID (sin `from`/`wa_id`) ENTRA, se identifica por bsuid, y el bot le
  RESPONDE. Cero drop silencioso, cero parche de derrota.
- Status callbacks siguen cayendo a Stop.
- `phone_number` nullable; `bsuid` en columna propia con índice único `(client_id, bsuid)`.
- Ausencia de teléfono no rompe validación, ingest, ni respuesta.
- Respuesta vía `recipient` funciona dentro de la ventana de 24h.

**No tocar**: rediseño de identidad de negocio (ADR posterior), north-star, nada fuera
del alcance.

---

## Lo que sigue (NO en este brief) — ADR de identidad primaria por BSUID

Tu pregunta —enfocar la identidad en el BSUID en vez del teléfono, y que el bot recolecte
el teléfono durante el DAG como dato de envío— es el rediseño de fondo, y merece su ADR:

- **BSUID como identidad PRIMARIA de negocio** (no solo técnica): el cliente ES su BSUID
  (por tenant); el teléfono pasa a ser un atributo de logística.
- **El teléfono se recolecta en el DAG como dato de ENVÍO, no de identidad**: encaja en
  `shipping_info_collected` ("para coordinar la entrega, ¿un número de contacto?"). Ventaja:
  el bot pide el teléfono solo cuando lo necesita (al coordinar envío), no como requisito
  de identidad al inicio — respeta la privacidad que el cliente eligió, y solo pide el dato
  cuando hay razón de negocio (el mensajero necesita llamarlo).
- **Manejar `user_id_update`**: cuando el cliente cambia de teléfono, el BSUID cambia;
  Meta manda previous→current para re-vincular.
- **Migración de clientes viejos** que solo tienen teléfono, sin BSUID: cómo se concilian
  cuando vuelven a escribir (write-back del BSUID sobre el registro existente).
- **Contact Book / `REQUEST_CONTACT_INFO`** si se necesita recuperar teléfono activamente.

Es importante pero NO urgente (el sistema funciona con BSUID como clave técnica sin el
rediseño) y grande (toca modelo de datos, DAG, migración de clientes viejos). Primero para
la hemorragia (este brief); luego el rediseño con calma (ese ADR).
