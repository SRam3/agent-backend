# Análisis — Venta del 2026-08-19: primera clienta BSUID real, confirmación falsa y colisión bot/operador

**Fecha del análisis**: 2026-08-19 (mismo día) · **Ventana**: 19:35–19:45 UTC (14:35–14:45 Bogotá) · **Analista**: cofounder AI + testimonio del operador

**Convención de evidencia**: `exec NNNN` = ejecución de n8n (`cafe_arenillo_v2`) leída completa; toda afirmación con timestamp UTC está confirmada contra las ejecuciones de n8n o con SELECT read-only contra la Postgres viva (se indica cuál). Lo que viene del testimonio del operador se marca como tal. PII enmascarada. `archivo:línea` = verificado en el repo.

**Contexto de sistema en la ventana**: P14 fases 1–4 aplicadas (backend 2026-08-17, n8n 2026-08-18). Es decir: esta conversación corrió sobre el workflow tocado en 11 nodos **24 horas después** de su verificación e2e sintética.

---

## 0. Identificación

Hubo **dos conversaciones simultáneas**, entrelazadas minuto a minuto:

| | Clienta "M. O." | "Lodge P. V." |
|---|---|---|
| conversation | `90aa2b87…` | `1deb5fda…` |
| client_user | `59cd973e…`, **creado hoy** | `8d47e191…`, creado **2026-06-12** |
| Identidad | `bsuid CO.…5687`, `phone_number NULL` — **privacidad de número activada** | `phone 57…4465`, `bsuid` vacío (fila pre-P14, fallback por teléfono) |
| Resultado | Venta cerrada **por el operador a mano**; sin registrar en el sistema | Pedido de 4 bolsas **colgado** en v6 |

**M. O. es el primer cliente real del camino P14 completo.** Sin las fases 3/4, su "Buenas tardes" habría sido la exec 9459 otra vez: drop de 15 ms, y jamás nos enteramos de que una clienta recurrente quiso comprar de nuevo.

El operador (Sebastian) recibió el aviso Telegram de "venta lista para cerrar" (19:42:27) y **entró a escribir por WhatsApp desde ~19:42, sin pulsar el botón**, porque —cita— "vi el bot muy perdido".

## 1. Línea de tiempo reconstruida (M. O., con los mensajes del operador intercalados)

Los mensajes del operador (columna 🧑) vienen de su testimonio con hora local; **no existen en `messages` ni en ningún log** — el sistema fue ciego a ellos. El bot (🤖) razonó todo el tramo final sobre un diálogo al que le faltaba la mitad.

| UTC | Quién | Mensaje / evento | Estado del sistema |
|---|---|---|---|
| 19:36:05–:14 | clienta | "Buenas tardes" / "Para hacer un pedido" | conv creada, v1–v2 |
| 19:36:32 | clienta | "Sebastian quiero **nuevamente** este café" | `profile: {}` — el sistema no sabe que es recurrente |
| 19:36:55 | clienta | **imagen** (¿el producto que quiere?) | persiste con `content=''` — invisible (P16) |
| 19:37:17 | clienta | "1 bolsa en grano" | ✓ `quantity, grind` capturados pre-producto (P12) |
| 19:40:07–:18 | clienta | "2 bolsas mejor" / "¿Cuándo lo entregan? Para saber qué dirección les pongo" | ✓ corrige quantity · 🤖 ignora la pregunta, re-pide nombre |
| 19:40:24 | clienta | **evento `edit`** (corrigió "les pingo") | entra como inbound vacío → 🤖 **se re-presenta**: "Hola, soy Sebastian…" (exec 10514) |
| 19:40:45 | clienta | "🤔" | 🤖 se re-presenta OTRA vez |
| 19:40:51–19:41:17 | clienta | "¿Cuándo entregan las bolsas?" / "¿Hoy es el domicilio?" / "Entiendo" | 🤖 misma respuesta enlatada 2×, re-pide nombre 3× — dos outbound idénticos consecutivos (19:41:26/19:41:33): **a uno del circuit breaker** |
| 19:41:25 | clienta | "Cra ** * ** - **" (¡la dirección!) | `extracted_data: {}` — **slot ofrecido, no capturado** (exec 10560) |
| 19:41:31 | clienta | "Barrio E. C." | **HTTP 500 del ingest** (contrato debounce roto, exec 10566); n8n lo traga en silencio |
| 19:41:35–:55 | clienta | nombre → teléfono `***8227` | ✓ capturados; nótese: se le pidió el teléfono a una clienta que lo oculta |
| 19:42:07–:19 | clienta | "Si" → "Manizales + dirección" | segundo 500-debounce (exec 10600) |
| 19:42:23 | clienta | "Barrio E. C." (repite) | 🤖 recupera city+address del historial ✓ **pero extrae `user_confirmation: true`** → gate acepta → `checkpoint_completed:user_confirmed` (audit 19:42:32) → **Telegram con botón (19:42:27)** → y recién DESPUÉS manda el resumen "¿Todo bien con esos datos?" |
| ~19:42 | 🧑 | **"Manizales?" · "Listo Monica confirmado."** | El operador entra. El sistema no lo sabe. Conversación sigue `active` |
| 19:42:52–:55 | clienta | "Si quieres yo lo recojo" / "No tengo problema" | 1º tragado (500-debounce, exec 10612); 🤖 pide comprobante **sin haber dado nunca los medios de pago** |
| ~19:43 | 🧑 | "Sería en Villamaría. ¿Es viable para ti?" | — |
| 19:43:51 | clienta | "A no, pensé que era en el arenillo" | Responde AL OPERADOR; 🤖 lo interpreta como dirigido a él |
| 19:43:57 | clienta | "Ya te transfiero los 87" | `extracted_data` vacío: **el claim de pago no marcó nada (P11 aguantó)** |
| ~19:44 | 🧑 | "también si quieres puedes bajar" · **"ya te comparto la llave"** | — |
| 19:44:04 | clienta | **"Compárteme llave por favor"** | Le responde al operador. 🤖 se adelanta: |
| 19:44:13 | 🤖 | **"La llave para recoger el café es 1234"** | **Alucinación de dato operativo** (exec 10632). La "llave" pedida era la de transferencia; el bot inventó una llave física con valor fabricado — entre la promesa del operador y la llave real |
| ~19:45 | 🧑 | "mira la llave" · **"302***05"** (la real) · "Muchas gracias" | La clienta recibió DOS llaves: una falsa del bot y una real del humano, con 60 s de diferencia |
| 19:44:29–:39 | clienta/🤖 | "Mejor con el domicilio" (tragado, exec 10650) / "Listo" → 🤖 "seguimos con el envío… espero comprobante" | Último turno del bot. **Estado final: `active`, v23, `current_checkpoint=product_matched`** (SELECT confirmado) |
| 19:44:43–19:45:36 | — | 6 webhooks sin contenido (statuses), descartados | — |
| post-ventana | — | **`operator_confirm_telegram`: 0 ejecuciones.** Venta sin registrar; profile sin `purchases` | La primera venta de julio, repetida — con el mecanismo ya construido |

**Lodge P. V.** (paralela): "Hola" 19:35:52 → "3 libras café en grano" → conversión honesta a 4 bolsas de 340g ✓ → "Y dejarlo en Metropolitan Loft" (19:41:12) → el bot re-pregunta permiso sobre lo que ella acaba de pedir → **silencio**. Pedido de 4 bolsas muerto sin que ninguna pieza del sistema lo note. Además: Lodge tenía conversación previa (2026-06-12) y hoy fue saludado como desconocido — el re-saludo de la deuda #7, otra vez.

## 2. Hallazgos

### H1 · El camino de coalescencia del debounce devuelve 500 SIEMPRE — y n8n lo traga (deuda #13, nueva)

- **Muestra**: execs 10566, 10600, 10612, 10650 — 4 veces en 3 minutos. `services/ingest.py:228` retorna `{"should_respond": False, "reason": "debounce"}`; el endpoint hace `IngestMessageResponse(**result)` → faltan 8 campos requeridos → 500. En n8n, el AxiosError cae por la rama false de `IF Should Respond` → Stop, ejecución "success".
- **El contraste que lo delata**: el path de `DuplicateMessageError` (`api/v1/ingest.py:99-110`) SÍ construye la respuesta dummy completa. Al de debounce se le olvidó. **Este camino nunca ha devuelto una respuesta válida** — no se había visto porque exige mensajes a <5 s, y ningún cliente previo escribía en ráfaga.
- **Lo que amortiguó el daño**: el mensaje SÍ persiste antes del early-return, y el turno siguiente lo leyó de `recent_messages` (v18 reconstruyó ciudad+dirección de dos mensajes tragados; v23 aplicó "mejor con el domicilio" de un turno muerto). El resultado funcional fue el que el debounce quiere — logrado **por accidente**, porque un objeto de error casualmente no tiene `should_respond=true`.
- **Cubeta**: modo de falla nuevo (deuda #13) dentro del territorio conocido de deuda #2 (P7) y deuda #12 (P5). El fix del contrato es puntual y **no necesita el ADR de P7**.
- **Falsificaría**: test que llame `/ingest` 2× a <5 s y reciba 200 en ambas.
- **Severidad**: alta — sobrevive por una coincidencia de enrutamiento; P5 (manejo de errores n8n) cambiaría este comportamiento sin saberlo.

### H2 · P15 con daño real: `user_confirmation` falsa disparó el lazo de operador antes del resumen

- **Muestra**: exec 10602 — el entrante es "Barrio E. C." (fragmento de dirección); el LLM extrae `user_confirmation: true`; el gate lo acepta (los 4 slots de datos estaban — `agent_action.py:182-188`) y dispara `checkpoint_completed:user_confirmed` + Telegram con botón, todo **antes** de que el bot enviara el resumen del pedido. `user_confirmation: true` quedó **persistido en prod** (SELECT confirmado) sobre una confirmación que no existió.
- **Cubeta**: **P15 exactamente**, y lo **agrava**: el 2026-08-01 el daño fue nulo; aquí el checkpoint quedó escrito en falso y convocó al operador con premisa falsa. (Irónicamente, la convocatoria temprana fue lo que trajo al humano a tiempo para salvar la venta — un bug amortiguando otro no es un diseño.)
- **Severidad**: alta — es el checkpoint que decide qué escribe `confirm-payment`.

### H3 · Alucinación de dato operativo en el momento del pago: "la llave es 1234"

- **Muestra**: exec 10632. Contexto: "Ya te transfiero los 87" → "Compárteme llave por favor" → 🤖 "La llave para recoger el café es 1234". Los medios de pago reales **SÍ están en el system prompt** ("PAYMENT METHODS (share ONLY when the customer confirms…): Bancolombia ahorros …, Nequi …") y el flujo escrito ordena "cliente confirma → compartes medios de pago": **el LLM no los compartió en 23 turnos**, ni ante la petición explícita.
- **Doble causa (inferida)**: (a) regla enterrada en un prompt de ~22K caracteres que el modelo no obedece — el insight literal de P21; (b) "llave" (Bre-B) fuera del vocabulario del prompt: gpt-4o-mini resolvió la ambigüedad inventando. No existe ningún gobierno sobre el texto SALIENTE: ADR-002 gobierna slots entrantes; nadie valida datos operativos en el NLG. El postmortem de julio ya había rozado esta clase ("Sí, somos nosotros" a ciegas); hoy escaló al punto donde cambia dinero de manos.
- **Cubeta**: la mitad "regla ignorada" CONFIRMA P21; la mitad "NLG afirma datos operativos falsos" es **modo de falla nuevo → P28** (registrado en ROADMAP, no abierto).
- **Severidad**: **crítica** — sin intervención, la clienta transfiere $87.000 esperando una llave inexistente, o se va.

### H4 · Un evento `edit` entra como mensaje vacío y resetea la persona del bot

- **Muestra**: exec 10514 — payload `type: "edit"`, `text.body: null`, wamid nuevo → pasa `If Message Exists` (solo chequea id) → `content: ""` → turno completo → el LLM ve vacío y **se re-presenta en mitad de la venta**; la clienta responde "🤔" y el bot se re-presenta otra vez. Costó 2 turnos y el disfraz de humano.
- **Alcance real medido en DB**: 50 inbound con `content=''` — 35 `unsupported`, 12 `image`, 2 `audio`, 1 `edit` — **~15,6% de los 321 inbound históricos son invisibles** para el sistema.
- **Cubeta**: **P16, con alcance ampliado** — el frente ya se autodefinió como "el pipeline, no el tipo de medio"; este caso confirma que el pipeline necesita un allowlist de `message_type` (un `edit` ni siquiera es mensaje nuevo), además de la descarga de bytes.
- **Severidad**: alta — barato de disparar, rompe la ilusión central del producto.

### H5 · Slots ofrecidos ignorados; preguntas del cliente ignoradas por perseguir el slot de turno

- **Muestra**: v13 (exec 10560) — la clienta manda su dirección espontáneamente → `extracted_data: {}` → re-pide el nombre. Se capturó recién en v18 desde el historial. Tres preguntas de entrega (v7/v10/v11) respondidas con evasivas + re-pedido del nombre. Contraste: los ORDER_FIELDS **sí** se capturan oportunistamente (v5 — P12 funcionando); los slots del DAG no.
- **Cubeta**: **confirma P21** (flujo secuencial rígido) con el caso de uso que le faltaba.
- **Severidad**: media — esta clienta aguantó; julio ya avisó que la próxima no.

### H6 · `user_confirmed` completado con `product_matched` incompleto: venta "lista" sin producto

- **Muestra**: `missing=[product_id]` del v3 al v23 (todas las execs); `current_checkpoint=product_matched` al final (SELECT). El gate de `user_confirmation` exige `full_name+phone+shipping_address+shipping_city` (`_USER_CONFIRMATION_REQUIRES`) — **`product_id` no está en la lista**, así que un checkpoint downstream se completó con el upstream incompleto. La clienta nunca nombró el producto: dijo "este café" y mandó una imagen invisible (P16). El resumen dijo "2 bolsas de 340g" por acierto de facto (catálogo de 1 producto), no de sistema.
- **Consecuencia verificada en código**: si el operador pulsa el botón hoy, `confirm_payment` registra la compra con `_fetch_product_price(…, None) → None` (`agent_action.py:506`) — **venta sin precio ni total** en el profile.
- **Cubeta**: modo de falla nuevo de gobierno; se resuelve donde ya se va a trabajar: **añadido al alcance de P15** (mismo gate) — no amerita frente propio.
- **Severidad**: alta para integridad del dato de venta.

### H7 · Colisión bot/operador: dos "Sebastian" en el mismo chat (deuda #14, nueva)

- **Muestra + testimonio**: desde 19:42 el operador escribió en el chat mientras el bot seguía activo (estado `active` verificado hasta v23). Los mensajes del operador **no existen para el sistema** (no pasan por el webhook, no se persisten): el LLM interpretó como propias las respuestas de la clienta al humano ("A no, pensé que era en el arenillo" → Villamaría del operador; "Compárteme llave" → la promesa del operador). El punto más grave: la clienta recibió la llave falsa del bot ENTRE la promesa y la llave real del humano.
- **Mecanismo faltante**: no hay "modo operador". ADR-009 aceptó que tras la confirmación la conversación siga `active` ("el bot acompaña") y su aviso dice "entra a acompañar" — pero cuando el operador entra, **no existe ninguna forma de callar al bot** (el único freno automático es el breaker, y el "loop" de hoy era parafraseado — P10, no lo dispara).
- **Cubeta**: modo de falla nuevo → **P29** (registrado, no abierto) + deuda #14. Es también evidencia directa para la decisión P23 (estados de espera explícitos).
- **¿Fue necesaria la intervención?** **Sí, sin ambigüedad.** El turno exacto donde la conversación se perdía sola es v22 (19:44:04): la clienta con la plata en la mano pidiendo la llave, el bot inventándola, y los medios de pago sin compartir en 23 turnos. No fue falso positivo del operador. Lo que sí quedó a medias: **el botón no se pulsó** → venta sin registrar, profile sin `purchases`, conversación `active` hasta que la ventana muera.

### H8 · La venta del Lodge murió en una pregunta de permiso — y nadie lo va a notar

- **Muestra**: conv `1deb5fda` v6 — la clienta ya dijo dónde dejarlo y el bot preguntó "¿Te gustaría que lo dejemos en…?". Cero mensajes después (verificado en `messages`). Pedido: 4 bolsas (~$160.000), `extracted_context = {quantity: 4}`.
- **No determinable**: si la pregunta causó el abandono (n=1) o si retomará.
- **Cubeta**: la pregunta-permiso **confirma P21** (la misma regla anti-chatbot violada en julio); el carrito muerto e invisible es **la mejor evidencia de P24 hasta hoy** — no existe ninguna señal de "venta abandonada" en el sistema.

### H9 · No-fallas (expectativas correctas del diseño actual)

- **Pedirle el teléfono a una clienta con privacidad**: diseño vigente (el teléfono como dato de envío; la identidad ya no depende de él). Funcionó sin fricción. El caso teórico del ADR de identidad ya tiene ejemplar real.
- **Lodge resuelto por teléfono sin write-back del bsuid**: exactamente lo decidido en P14 ("la fusión es del ADR posterior").
- **41 conversaciones `active` históricas**: las ventanas muertas no transicionan de estado; es el diseño (la ventana se evalúa por timestamp). Relevante solo como insumo de P24.

## 3. Qué funcionó (con referencia)

- **P14 e2e con clienta real** (toda la ventana): bsuid-first, `phone_number NULL` de punta a punta, respuesta entregada contra el BSUID, aviso al operador sin "null". La verificación sintética del 08-18 se validó en producción 24 horas después. Es el mecanismo que hizo EXISTIR esta venta.
- **P11 / `OPERATOR_ONLY_FIELDS`** (v21): "Ya te transfiero los 87" no marcó pago, no convocó, no cerró. Cero `payment_confirmation` en contexto (SELECT). El gemelo del bug no reapareció.
- **P2 + P12** (v5→v18): `quantity`/`grind` capturados pre-producto, corregidos en caliente, aritmética del resumen correcta (2×40.000 + ~7.000 ≈ 87.000 ✓).
- **Sync de stable facts al profile** (v23): full_name, phone, city, address listos para el operador.
- **ADR-009, mitad máquina** (19:42:27): el aviso con botón salió en el momento y canal correctos (el trigger fue falso — H2 — y el eslabón humano no cerró el lazo).
- **Idempotencia inbound**: 321/321 con wamid, cero duplicados, incluso en ráfaga.
- **Anti-alucinación de catálogo** (Lodge v5): "3 libras" → 4 bolsas de 340g sin inventar presentaciones. Distinguir de H3: el prompt defiende bien el catálogo y nada los datos operativos.
- **Concurrencia real de dos clientas** sin cruce de estado ni historial (advisory lock por conversación). Primera evidencia en prod.
- **PII masking en audit_log**: identidades enmascaradas (`***…5687`) — funcionando.

## 4. Barrido de deuda técnica contra la DB (2026-08-19)

| Deuda | Dato duro (SELECT) | Veredicto |
|---|---|---|
| #7 compaction | `last_conversation_summary`: **0 de 35** client_users; 27 profiles vacíos; Lodge re-saludado hoy pese a conversación del 06-12 | **Total, no intermitente.** La "memoria del vendedor" no ha funcionado nunca |
| #11 idempotencia outbound | **284/284** outbound sin `chakra_message_id` | Sin cambios; sistema ciego a sus envíos |
| #2 debounce race | Par de outbounds idénticos consecutivos 19:41:26/19:41:33 (2 turnos respondiendo a inbounds a 6 s) | Confirmada otra vez; además deuda #13 (500) |
| #13 (nueva) contrato debounce | 4×500 hoy; `ingest.py:228` vs response model | Fix puntual, no espera al ADR de P7 |
| #14 (nueva) operador invisible | 7 mensajes del operador sin rastro en `messages`/logs; bot activo en paralelo | → P29 |
| bug `message_count` | 90aa2b87: count 54 vs 50 filas (27 inbound×2); 1deb5fda: 14 vs 13 (7×2) | Cuenta cada inbound dos veces, outbound cero (hallazgo (b) de julio, confirmado) |
| P18 datos legacy | Solo 2 conversaciones con `payment_confirmation`, ambas `closed` y legítimas (e2e `9635…` del 07-20 y venta real `7be24ff4` del 08-01) | **Diagnóstico hecho**: no hay filas inconsistentes; queda solo limpiar el `purchase_count: 2` del e2e (pendiente P11) |
| P16 alcance | 50 inbound vacíos = 15,6% del total (35 unsupported, 12 image, 2 audio, 1 edit) | El agujero es mayor de lo documentado |

## 5. Refutaciones a los docs vigentes

1. **Orden del ROADMAP, tensionado**: P15 como siguiente queda confirmado (H2). Pero los dos costos más graves del día — la llave inventada (H3) y los 500 del debounce (H1) — viven en frentes lejanos (P21 espera a P22; P7 espera a P23). Dos correcciones puntuales no deberían esperar su frente: el contrato del debounce (deuda #13, un return) y los medios de pago en el directive (hoy es una regla con dinero en tránsito que el LLM lleva 23 turnos ignorando).
2. **ADR-009, "consecuencia de diseño aceptada"**: "el bot acompaña en active hasta el botón" mostró su costo — el operador entra y compite con el bot (H7). La alternativa D rechazada merece re-lectura a la luz de P29/P23.
3. **CLAUDE.md contradecía al código sobre el gate**: el diagrama del DAG decía "gate: requiere los 4 anteriores" (incluiría product_matched); el código exige solo los 4 slots de datos. Corregido en CLAUDE.md con esta fecha; la decisión de exigir `product_id` queda dentro de P15 (H6).
4. **P16 se subestimó dos veces**: (a) "el operador viéndolo en WhatsApp basta" pensaba en comprobantes; hoy la imagen ciega costó la identificación del producto (raíz de H6); (b) el 15,6% de inbound invisible incluye tipos que no son medios (`edit`, `unsupported`).
5. **Deuda #8 no se agravó**: 23 turnos de `json_object` bien formado. **Deuda #7 sí**: ya no es "rota en prod" sino "jamás funcionó" (0/35).

## 6. Recomendación operativa inmediata (no requiere código)

**Pulsar el botón del aviso Telegram de las 14:42** para registrar la venta: el endpoint es idempotente y válido desde `active` (ADR-009). Consecuencia conocida: la compra queda sin precio/total (H6) — anotar el total real ($87.000) donde corresponda hasta que P15/P24 lo resuelvan. Si no se pulsa, M. O. queda como "interesada que nunca compró" y su próxima conversación repetirá el "Cliente nuevo" — con el agravante de que la compaction (deuda #7) tampoco va a rescatar nada.

## 7. Preguntas que siguen abiertas

1. ¿Llegó el comprobante después (por WhatsApp o en persona) y la transferencia usó la llave real? (severidad final de H3).
2. ¿Lodge retomó después de las 19:56 UTC? (si no: primera venta perdida cuantificable — $160.000 — para P21/P24).
3. ¿Por qué no se pulsó el botón? — el propio operador respondió: "no entré a confirmar rápidamente, me quedé atendiendo porque vi el bot muy perdido". El eslabón humano de ADR-009 compite con la urgencia de atender: cuando el bot falla, el operador va al chat, no a Telegram. Insumo directo para P29.

## Veredicto

**El sistema encontró a la clienta (P14), recolectó bien (P2/P12/profile), y perdió la venta en el punto exacto donde el dinero cambia de manos** — no por falta de datos (los medios de pago estaban en el prompt) sino por falta de gobierno sobre lo que el LLM DICE (H3) y sobre quién está hablando (H7). El operador salvó la venta interviniendo a tiempo — convocado, irónicamente, por una confirmación falsa (H2) — y el lazo de cierre construido en ADR-009 volvió a quedar sin ejercer: la venta real número dos del sistema tampoco existe en el sistema.
