# Brief de implementación — ADR-008: idioma (Opción B, backend) + teléfono E.164-laxo

> **Sistema REAL (no greenfield)**: backend FastAPI en prod (Azure Container Apps,
> rev ca-backend--0000040), Postgres con 6 tablas activas (post-007), migración actual
> 009. Este brief edita código vivo del repo. NO rediseña nada. NO toca schema.
>
> **Dos cambios independientes en un solo objetivo (ADR-008). Van en dos commits
> separados** — idioma y teléfono no se acoplan.
>
> **Disciplina**: plan de cambios por archivo ANTES de codear, espera confirmación.
> Cada commit con sus tests, suite en verde. Rama nueva desde main:
> `feat/adr-008-language-phone`. NO desplegar en esta sesión.

---

## Contexto (de ADR-008 + auditoría)

Existe YA un mecanismo de idioma en `prompt_context.py` (`format_customer_profile`):
emite `INSTRUCCIÓN DE IDIOMA: ... Respóndele en inglés` cuando `profile["language"]
== "en"`. Pero `profile.language` lo puebla la compaction (diferida, entre-conversaciones,
y además rota — P4). Para un cliente NUEVO en inglés (caso del stand), `profile` está
vacío → ninguna instrucción → el prompt 100%-español gana → el bot responde en español.

Decisión ADR-008 (corregida a Opción B tras el hallazgo de logs: el prompt gigante de
~5.868 tokens NO respeta reglas enterradas — añadir la regla de idioma AL template
sería enterrarla en el sitio que ya se ignora): **detectar el idioma del mensaje
entrante en el backend (determinista) y alimentar con él el mecanismo de INSTRUCCIÓN DE
IDIOMA que ya existe, en vivo, sin depender de `profile`.**

---

## COMMIT 1 — Detección de idioma en backend (Opción B)

### Diseño a confirmar antes de codear

- **Dónde detecta**: en `ingest.py`, sobre `content` del mensaje entrante, dentro del
  turno (antes de armar el `conversation_summary` que se devuelve a n8n).
- **Con qué detecta**: función pura nueva `detect_language(text) -> "es" | "en"` en un
  módulo testeable (p.ej. `services/language.py`). PROPÓN el mecanismo y su costo:
  - opción liviana: heurística determinista (stopwords ES/EN, ratio de tokens) — cero
    dependencias, cero red, microsegundos, suficiente para es/en.
  - opción robusta: `langid` o `fasttext-langdetect` (dependencia nueva, offline).
  - Mi default recomendado: **heurística liviana primero** (es/en es un problema fácil;
    no metas una dependencia pesada para dos idiomas). Si la precisión no basta en
    pruebas, subimos a langid. Confírmame cuál.
- **Cómo se propaga**: el idioma detectado se pasa a `format_conversation_summary` /
  `format_customer_profile` como un parámetro `live_language`, que tiene PRIORIDAD sobre
  `profile.language`. Reusa el bloque de `INSTRUCCIÓN DE IDIOMA` que YA existe — no
  escribas uno nuevo. Solo cámbiale la fuente: hoy es `profile.get("language")`, pasa a
  ser `live_language or profile.get("language")`.
- **Refuerzo de prioridad**: además, anteponer al ensamblado (no al template en DB, sino
  en el string que `ingest` devuelve como `conversation_summary` o en un campo nuevo) una
  línea de máxima prioridad tipo: `LANGUAGE (overrides all else): reply in {lang}.`
  Esto ataca la baja adherencia: la instrucción va al INICIO, no enterrada.

### Archivos

1. `services/language.py` (nuevo): `detect_language(text) -> str`. Pura, testeable.
2. `services/prompt_context.py`: `format_customer_profile` y
   `format_conversation_summary` aceptan `live_language: str | None = None`;
   la instrucción de idioma usa `live_language or profile.get("language")`.
   El bloque de texto en sí NO cambia (reuso), solo su fuente.
3. `services/ingest.py`: llamar `detect_language(content)` y pasar el resultado a
   `format_conversation_summary(...)`. Añadir la línea LANGUAGE de máxima prioridad al
   inicio del contexto devuelto.

### Tests (puros, sin DB)
- `detect_language("Do you speak english? I live in medellin")` → `"en"`.
- `detect_language("Hola, quiero comprar café molido")` → `"es"`.
- `detect_language` robusto ante mensajes cortos / mixtos (define el fallback: ante
  duda, `"es"` — es el default del negocio).
- `format_customer_profile(..., live_language="en")` con `profile` VACÍO → emite la
  instrucción de responder en inglés (esto es el caso del stand que hoy falla).
- `live_language` tiene prioridad sobre `profile["language"]` cuando difieren.
- Regresión: sin `live_language`, comportamiento byte-idéntico al actual.

---

## COMMIT 2 — Validación de teléfono E.164-laxo

### Diseño

- Función pura `is_plausible_phone(raw) -> bool` en `services/language.py` o un
  `services/validation.py`: normaliza (quita espacios, guiones, paréntesis, un `+`
  inicial), cuenta dígitos, acepta **7 a 15 dígitos**, rechaza el resto (letras,
  vacío, <7, >15). NO valida país. NO "formato colombiano".
- **Dónde se aplica**: en `compute_context_updates` (`agent_action.py`), como gate del
  campo `phone` — igual patrón que los gates existentes. Si el phone propuesto no es
  plausible, NO se persiste y se emite un side_effect observable
  (`warning:invalid_phone_rejected`), consistente con la asimetría de side_effects que
  P3 dejó. La respuesta del LLM al usuario igual se envía (fail-safe, como todo gate).

### Tests (puros, sin DB)
- `is_plausible_phone("31071484777779")` → el caso del stand: 14 dígitos → cabe en
  E.164 (≤15) → **True**. (Registra que el problema del stand no era el largo sino la
  ausencia total de validación; este número específico pasa. Si el negocio quiere
  rechazar >13, es otra decisión — no la asumas.)
- `is_plausible_phone("+57 300 123 4567")` → True (10 dígitos).
- `is_plausible_phone("+1 415 555 2671")` → True (internacional).
- `is_plausible_phone("hola")` → False. `is_plausible_phone("123")` → False.
- Gate: turno con `phone` inválido → no se persiste, side_effect emitido, resto del
  turno intacto.
- Regresión: `phone` válido sigue persistiendo como hoy.

---

## Cierre

- Dos commits limpios (idioma / teléfono), cada uno verde. Reporta archivo:línea,
  tests añadidos, y confirma la regresión (sin live_language = comportamiento actual).
- NO desplegar. NO tocar n8n, prompt en DB, schema, ni north-star.
- ⚠️ Ojo de alcance: NO toques el `system_prompt_template` (migración). Todo el idioma
  vive en el ensamblado del backend, no en el prompt en DB. Esa es la esencia de la
  Opción B — sacar la regla del muro de texto que se ignora.

---

## Definition of done (postura del rol, aplicada)

- Flujo end-to-end: mensaje entrante → `detect_language` → instrucción de idioma en vivo
  al inicio del contexto → LLM responde en el idioma correcto → phone validado en
  `agent_action` antes de persistir.
- Tenant isolation: sin cambios (ambos fixes operan dentro del scope de client_id ya
  existente; no introducen queries nuevas cross-tenant).
- Idempotencia/concurrencia: sin cambios (no tocan el tramo de advisory lock / debounce).
- PII: el teléfono se valida por FORMA, nunca se loguea en claro (usa el `_mask_phone`
  existente si algo se registra).
- Sin secretos nuevos, sin endpoints nuevos, sin dependencias nuevas si se elige la
  heurística liviana.
