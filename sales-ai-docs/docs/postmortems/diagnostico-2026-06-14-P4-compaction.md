# P4 — Diagnóstico de la lazy-compaction rota (2026-06-14)

> Sesión de diagnóstico, NO de fix a ciegas. Conclusión: **aún no concluyente**
> sobre la causa raíz exacta — pero acotada, con el mecanismo reproducido y la
> falla ahora hecha observable para capturar la causa en la próxima ocurrencia.

## Síntoma
`client_users.profile` está VACÍO en prod: nunca se poblaron `purchases`,
`last_conversation_summary`, etc. La lazy-compaction
([ingest.py:146-167](../../../sales_agent_api/app/services/ingest.py#L146-L167) →
[conversation_summary.py](../../../sales_agent_api/app/services/conversation_summary.py))
falla en silencio.

## Mecanismo confirmado (reproducido en test)
`summarize_conversation` envuelve la llamada al LLM en un `except Exception`
amplio ([:210-215](../../../sales_agent_api/app/services/conversation_summary.py#L210))
que tragaba **cualquier** fallo en un `logger.warning` que nadie lee, y devolvía
`None`. El caller (`ingest`) entonces sigue sin escribir el perfil. Como el bot
responde igual, nadie notó que la memoria del negocio moría en cada turno.

Reproducido con un fixture de 4 mensajes (shape de la conversación del 3-may) y
un summarizer que lanza: `test_compaction_failure_is_swallowed_but_returns_none`
y `test_compaction_failure_is_logged_at_error_with_traceback`.

## Hipótesis descartadas
- **Key ausente en runtime** — descartada. La key se exporta a `os.environ` en
  boot ([main.py:67](../../../sales_agent_api/app/main.py#L67)) y el brief lo
  confirma. (Aun así, una key *inválida* no queda descartada — ver candidata 2.)
- **SDK sin structured outputs** — descartada. `openai>=1.40` está pineado
  ([requirements.txt:12](../../../sales_agent_api/requirements.txt#L12)); el
  soporte de `response_format=json_schema` existe desde 1.40.
- **Schema strict inválido (400)** — improbable. `SUMMARY_SCHEMA` cumple las
  reglas strict de OpenAI (en todo nivel `required` == `properties`; `anyOf` solo
  a nivel de propiedad en `pending_intent`). Cubierto por tests existentes.

## Candidatas restantes (ranking — requieren el log de error ya capturado)
1. **Egress de red bloqueado** desde Azure Container Apps hacia
   `api.openai.com` → `APIConnectionError` / `APITimeoutError`. Prior más alto
   para un backend contenedorizado sin egress explícito y sin errores visibles.
2. **Auth inválida (401)** — la key carga pero está expirada / es de otra org /
   mal secreto. "Carga al boot" ≠ "es válida".
3. **Modelo / structured output (400)** — `SUMMARY_MODEL` apunta a un snapshot
   sin soporte de `json_schema`, o la org no lo tiene habilitado.
4. **Drift de imagen sin `openai`** — `from openai import AsyncOpenAI` lanzaría
   `ModuleNotFoundError`. Bajo (la dep está pineada), pero el lazy-import lo hace
   posible si la imagen no se reconstruyó.

## Entregado en esta sesión (🟢, seguro, en scope DEUDA #3)
Subir el warning silencioso a algo observable en
[conversation_summary.py](../../../sales_agent_api/app/services/conversation_summary.py):
`logger.error(..., exc_info=True)` con tipo de excepción + un contador de fallos
de proceso (`get_summary_failure_count()`). NO se cambió el comportamiento de
swallow: la compaction sigue siendo best-effort y nunca rompe el turno de chat.

## Próximo paso (decisión separada, NO en esta sesión)
Leer el error recién capturado en prod (ahora a nivel ERROR con stack trace) →
nombra la clase de excepción exacta → elegir el fix dirigido:
- egress → cambio de red/infra (🟡, fuera de hot path de código)
- auth → rotación de key (🟢)
- modelo/schema → ajustar `SUMMARY_MODEL` o el schema (🟢)

No se implementa ningún fix de causa raíz aquí porque la causa no está
confirmada. Si resultara tocar hot path o schema, va a decisión separada.

## Dependencia con P2 (verificada)
P4 se prueba DESPUÉS de P2 porque un resumen útil necesita que
quantity/preferencias ya estén en `extracted_context`. Con P2 aplicado, el input
del resumen reconstruido ya incluye el pedido completo —
`test_user_prompt_carries_full_order_after_p2`.
