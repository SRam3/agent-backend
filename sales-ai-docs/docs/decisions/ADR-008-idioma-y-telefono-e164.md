# ADR-008 — Soporte multiidioma e internacional (clientes extranjeros)

- **Estatus**: Aceptado
- **Fecha**: 2026-06-16 (revisado 2026-06-24)
- **Decididores**: Sebastian + cofounder/principal architect
- **Origen**: hallazgos del stand 2026-06-16 (ver `docs/postmortems/stand-2026-06-16.md`)

> **Revisión 2026-06-24**: la decisión de idioma cambió de "regla en el
> `system_prompt_template`" a **detección determinista en el backend** (antes
> alternativa D, ahora la decisión). Motivo: el análisis de logs post-stand demostró
> que gpt-4o-mini NO respeta reglas enterradas en el prompt de ~5.868 tokens (el tono
> robótico persistente es la evidencia: la regla anti-chatbot lleva 6 migraciones de
> refinamiento y aun así se viola casi cada turno). Por tanto "basta instruirlo en el
> prompt" es falso para este sistema. Ver Decisión §1 revisada y Alternativa D.

> ⚠️ Nota de numeración: "ADR-008" estaba citado informalmente en ADR-004/005 como
> reservado para `purchase_intents`. Ese ADR nunca se escribió. Se reasigna el número
> 008 a esta decisión, que es la que de hecho llegó primero. `purchase_intents`, cuando
> se decida, tomará el siguiente número libre. Resolver esta colisión al mergear.

---

## Contexto

Hasta ahora el sistema asumió implícitamente un perfil de cliente único: colombiano,
hispanohablante, teléfono de 10 dígitos. El system prompt (009) está íntegramente en
español e instruye al modelo a hablar español colombiano. No hay detección de idioma en
vivo, ni validación de teléfono.

El stand del 2026-06-16 reveló que esa asunción es falsa para el foco de negocio:
**parte del público objetivo son extranjeros que vienen a Colombia** y escriben en
inglés, con teléfonos no colombianos. Dos hallazgos del stand son consecuencia directa
de la asunción monolingüe/local:

1. Un cliente escribió en inglés ("Do you speak in english? I live in medellin") y el
   bot respondió en español. El modelo entiende inglés perfectamente; el prompt le
   ordena español.
2. Un cliente dio un teléfono malformado de 14 dígitos y el bot lo aceptó (sin
   validación). El instinto inicial de "validar formato colombiano (10 dígitos)" se
   descartó: cerraría la puerta justo a los clientes internacionales que se quiere
   atender.

## Decisión

**Sales AI Agent atiende clientes en español e inglés y acepta clientes
internacionales como ciudadanos de primera clase del producto.** Dos consecuencias
concretas:

### 1. Idioma: detección determinista en el backend (Opción B)

El bot responde en el idioma del cliente (español o inglés) desde el primer turno,
**sin depender de la compaction** (diferida y rota, ver P4) **y sin depender de que el
LLM obedezca una regla enterrada en el prompt** (que demostradamente no lo hace).

Mecanismo:
1. El backend detecta el idioma del `content` entrante en `ingest.py`, con una función
   pura testeable `detect_language(text) -> "es" | "en"` (heurística liviana de
   stopwords/ratio; sin dependencia pesada — es/en es un problema fácil; se sube a
   `langid` solo si la precisión no basta en pruebas). Fallback ante ambigüedad: `"es"`
   (default del negocio).
2. Ese `live_language` se pasa a `prompt_context.py` y **alimenta el bloque
   `INSTRUCCIÓN DE IDIOMA` que YA existe** en `format_customer_profile` — hoy ese bloque
   se dispara desde `profile.get("language")`; pasa a dispararse desde
   `live_language or profile.get("language")`. Se reusa el texto existente, solo cambia
   la fuente.
3. Además, una línea de idioma de **máxima prioridad al INICIO** del contexto
   ensamblado (`LANGUAGE (overrides all else): reply in {lang}`), porque la posición
   importa para la adherencia: lo primero pesa más que lo enterrado.

Esto es la tesis del sistema aplicada al idioma: **el LLM no decide el idioma; el
backend lo detecta deterministamente y se lo ordena.** El mecanismo diferido
(`profile.language` vía compaction) se mantiene como refuerzo entre conversaciones,
subordinado al `live_language`.

**No toca el `system_prompt_template` (no hay migración de prompt).** Todo vive en el
ensamblado del backend — sacar la regla del muro de texto que se ignora es el punto.

### 2. Teléfono: validación E.164-laxa, no formato colombiano

La validación de `phone` rechaza basura evidente pero acepta cualquier número
internacional plausible. Regla: **7 a 15 dígitos** (con o sin `+`, ignorando espacios y
guiones), rechazar el resto. E.164 permite hasta 15 dígitos para números
internacionales. Validación determinista en backend (en el gate de `phone` /
`compute_context_updates`), NUNCA en el prompt ni delegada al LLM.

> ⚠️ **Precisión honesta sobre el caso del stand**: el número que motivó esto,
> `31071484777779`, tiene 14 dígitos y por tanto **CABE en E.164 (≤15) — esta regla NO
> lo rechaza**. Eso es correcto y deliberado: el problema del stand nunca fue el largo,
> fue que NO existía validación alguna (hasta "hola" habría pasado como teléfono). La
> regla atrapa la *categoría* de basura evidente (letras, vacío, <7 dígitos, >15), no
> ese número puntual. Si el negocio quisiera rechazar números de más de ~13 dígitos por
> política, esa es OTRA decisión y no se asume aquí.

## Alternativas consideradas

**A. Solo español (status quo).** Rechazada: contradice el foco de negocio en
extranjeros. El stand mostró el fallo en el primer cliente angloparlante.

**B. Validación de teléfono de formato colombiano (10 dígitos / +57).** Rechazada
explícitamente: cierra el producto a clientes internacionales. Era el fix "obvio" y era
el equivocado. La incomodidad con esta opción fue lo que destrabó la decisión correcta.

**C. Fine-tuning de un modelo bilingüe / con conocimiento de café.** Rechazada por
tres razones alineadas con ADR-001: (1) fine-tuning enseña forma, no hechos ni idiomas —
gpt-4o-mini YA es bilingüe, solo hay que dejarlo serlo; un modelo tuneado en español
sería PEOR en inglés. (2) Rompe la propiedad de "el LLM es la pieza más reemplazable":
casaría el sistema con un modelo/proveedor/dataset y un ciclo de reentrenamiento. (3) La
evidencia es una pregunta de un cliente, no un patrón. RAG/fine-tuning siguen no siendo
apropiados en esta etapa.

**D. Detección de idioma en backend (clasificador propio). → ELEGIDA (rev. 2026-06-24).**
En la versión original de este ADR se rechazó por "innecesaria: el LLM ya detecta idioma
trivialmente, basta instruirlo". Ese razonamiento fue **invalidado** por el análisis de
logs post-stand: "basta instruirlo" asume que el LLM obedece la instrucción, y se
comprobó que gpt-4o-mini NO respeta reglas enterradas en el prompt de ~5.868 tokens (la
regla anti-chatbot, con 6 migraciones de refinamiento, se viola casi cada turno). Poner
la regla de idioma en el prompt sería enterrarla en el mismo muro que ya se ignora. La
detección determinista en backend no es "infraestructura sin pago" — es una heurística
liviana de decenas de líneas que convierte el idioma de "esperemos que el LLM obedezca"
a "el backend lo garantiza". Alineada con la tesis del sistema. Ver Decisión §1.

## Consecuencias

**Positivas:**
- Atiende al público objetivo extranjero sin fricción; ataca de frente la mala
  percepción de chatbots (un extranjero que escribe inglés y recibe inglés natural es la
  antítesis del bot esperado).
- Ambos fixes son baratos y deterministas donde deben serlo: idioma en prompt, teléfono
  en código. Cero fine-tuning, cero infra nueva.
- El idioma en vivo NO depende de P4, así que se puede hacer ya, independiente de la
  reparación de la compaction.

**Negativas / costos:**
- El prompt 009 crece con la regla de idioma (ya es largo; vigilar presupuesto de
  tokens, aunque una línea no mueve la aguja).
- La validación de teléfono E.164-laxa acepta números técnicamente bien formados pero
  falsos (un cliente puede teclear 12 dígitos válidos pero inventados). La validación de
  forma no garantiza veracidad — eso es inherente y aceptable; el operador humano
  confirma en el handoff.
- Soporte bilingüe implica que el conocimiento curado de café (Hallazgo 3) debería
  existir en ambos idiomas si se decide cubrirlo. Decisión separada.

## Cuándo revisar

- Si aparece un tercer idioma recurrente (portugués de turistas brasileños, p.ej.) →
  reevaluar si "es/en en prompt" escala o si conviene otra estrategia.
- Si la validación E.164-laxa deja pasar basura que causa problemas operativos reales →
  endurecer, pero nunca hacia "solo Colombia".
