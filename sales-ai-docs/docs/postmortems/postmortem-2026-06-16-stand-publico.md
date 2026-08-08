# Postmortem — Stand público (experimento con usuarios reales) 2026-06-16

- **Tipo**: registro inmutable de un evento real. No se edita.
- **Fecha del evento**: 2026-06-16
- **Contexto**: primer contacto del bot con usuarios desconocidos en volumen. Stand
  público, número de producción de Café Arenillo (no había número separado). El bot
  vivo corría sin los commits P2/P4 (no desplegados antes del evento).

> Nota de carriles: este documento REGISTRA lo que pasó. Las DECISIONES que se derivan
> viven en su propio ADR (ver ADR de soporte multiidioma/internacional). Los FIXES
> viven en un brief de implementación posterior. No mezclar.

---

## Resumen

El stand cumplió su objetivo real: exponer el bot a desconocidos y ver cómo rompe y
cómo reacciona la gente. Valor principal del día: tres fallos concretos con evidencia,
un bug de infraestructura descubierto y resuelto en vivo, y una señal estratégica sobre
percepción. Ninguna venta se cerró; no era el objetivo.

---

## Incidente de infraestructura (resuelto en vivo)

**Síntoma**: "a veces responde, a veces no". Mensajes perdidos sin respuesta tras
periodos de inactividad.

**Causa raíz**: el Container App tenía `minReplicas: 0` → escalaba a cero sin tráfico.
El primer mensaje tras inactividad disparaba un cold start (~17s: pull de imagen +
arranque de Python + carga de key de Key Vault + conexión a Postgres + Uvicorn).
Durante esa ventana n8n/Chakra recibía connection-refused/timeout y el mensaje se
perdía. Si había una réplica caliente (alguien escribió hace poco), respondía normal.

**Evidencia**: `KEDAScaleTargetActivated: Scaled from 0 to 1` con `Count: 6`;
`startup probe failed: connection refused` ×4 durante el arranque.

**Fix aplicado en vivo**: `minReplicas=1` → revisión `ca-backend--0000036`,
Healthy/Running, una réplica caliente permanente. Cold starts eliminados. Reversible.

**Lección**: este fallo no vive en el código — ninguna auditoría de código lo habría
encontrado. Solo apareció con tráfico real esporádico. Confirma el valor de exponer el
sistema a uso real.

**Pendientes vistos de paso (no tocados)**:
- Debounce `asyncio.sleep(5)` (DEUDA #2) suma 5s a cada `/ingest`, acercándolo al
  timeout de n8n. Revisar el timeout de n8n vs. esa latencia.
- Sin Log Analytics en el environment (`destination: ""`): no hay histórico de
  requests/errores, solo stream en vivo. Por eso no se pudo reconstruir mensaje por
  mensaje qué se perdió. Configurarlo sigue disponible como opción (DEUDA #3 relacionada).

---

## Hallazgos de comportamiento (3) — con clasificación de causa raíz

### Hallazgo 1 — Respondió en español a un cliente que escribió en inglés

**Qué pasó**: cliente escribió "Do you speak in english? I live in medellin". El bot
entendió pero respondió en español ("Claro, hablo español. Entonces, el envío es para
Medellín...").

**Clasificación: bug de DATOS, no del LLM.** La instrucción de idioma existe en
`prompt_context.py` (`format_customer_profile`: "INSTRUCCIÓN DE IDIOMA... Respóndele en
inglés") pero SOLO se dispara si `profile["language"] == "en"`. Ese campo lo llena la
compaction (`conversation_summary.py`) DESPUÉS de una conversación, para la SIGUIENTE.
No hay detección de idioma en vivo dentro de la primera conversación. El system prompt
(009) está íntegramente en español y le dice al modelo que hable español colombiano;
el modelo obedece.

**Agravante**: la compaction está rota (P4), así que el mecanismo diferido que existe
nunca se ejecutó. El idioma no se detecta nunca con el estado actual.

**NO es fine-tuning**: gpt-4o-mini habla inglés perfectamente; el prompt le ordena
español. Fix = regla de idioma en vivo en el prompt.

### Hallazgo 2 — Aceptó un teléfono inválido (`31071484777779`, 14 dígitos)

**Qué pasó**: cliente dio un número malformado; el bot lo aceptó y siguió.

**Clasificación: validación AUSENTE en backend, no del LLM.** `phone` entra por
`STRATEGY_FIELDS` con el único gate `if v` (truthy). No hay validación de formato en
ninguna capa (ni prompt, ni DAG gates, ni `compute_context_updates`). Consistente con
la limitación ya reconocida: "los DAG gates protegen el ORDEN pero no la PRECISIÓN".

**NO es fine-tuning, NO es prompt**: la validación de formato es determinista y va en
código. Ver el ADR de soporte internacional para por qué la validación NO debe ser
"formato colombiano".

### Hallazgo 3 — Inventó una distinción técnica de café (Specialty vs. café especial)

**Qué pasó**: cliente preguntó la diferencia entre "Specialty Coffee" y "café
especial". El bot respondió algo autoritativo pero impreciso/fabricado.

**Clasificación: alucinación de dominio — este SÍ es del LLM.** El modelo no sabía la
respuesta real y la fabricó con confianza. La sección NO INVENTES del prompt 009 no lo
cubrió porque el modelo no "sabía que no sabía".

**NO es fine-tuning**: fine-tuning enseña FORMA (tono, estilo), no HECHOS. Para inyectar
conocimiento factual la herramienta correcta es darle el hecho en contexto (sección de
conocimiento curado en el prompt, o más adelante recuperación de un FAQ), no reentrenar.
Además, una pregunta rara de un cliente no es evidencia de necesidad de entrenamiento.
Decisión de producto pendiente: ¿queremos que responda preguntas técnicas de café, o
que diga "déjame confirmarte bien eso" y no invente?

---

## Señal estratégica (lo más valioso del día)

**Percepción generalizada negativa hacia chatbots/asistentes conversacionales.** La
gente llega predispuesta en contra. Implicación: la ventaja del producto NO es "tener un
chatbot" sino "no sentirse como uno". Los tres hallazgos de arriba (idioma equivocado,
aceptar basura, inventar) son exactamente lo que confirma ese prejuicio. Corregirlos es
trabajo de producto central, no detalle técnico.

---

## Conclusión de causa raíz (responde "¿LLM, backend, o fine-tuning?")

| Hallazgo | Causa raíz | Capa del fix | ¿Fine-tuning? |
|---|---|---|---|
| Idioma | Datos (detección diferida + compaction rota) | Prompt (regla en vivo) | No |
| Teléfono inválido | Validación ausente | Backend (determinista) | No |
| Conocimiento de café | Alucinación de dominio | Prompt (conocimiento) + decisión de producto | No |
| Cold start | Infra (`minReplicas: 0`) | Infra (resuelto en vivo) | No |

**Patrón**: el reflejo es culpar al modelo porque es la pieza visible, pero en un
sistema "el backend gobierna", la causa casi siempre está en lo que el backend dio o
dejó de dar. 2 de 3 hallazgos lo confirman. Buena señal: los fallos son corregibles con
cambios baratos y deterministas, no con reentrenamientos caros.

---

## Limitación de evidencia

Stand corrido en el número de PRODUCCIÓN (no había alterno). Las conversaciones del
stand se mezclan con cualquier cliente real en la misma ventana. Sin Log Analytics
histórico, la reconstrucción mensaje-por-mensaje no es posible — solo lo capturado en
vivo y lo que quede en `messages`/`audit_log`. Filtrar por la ventana horaria del stand
es el único separador disponible.
