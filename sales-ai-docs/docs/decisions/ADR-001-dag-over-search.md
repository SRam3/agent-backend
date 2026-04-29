# ADR-001 — DAG sobre algoritmos de búsqueda

- **Estatus**: Accepted
- **Fecha**: 2026-04-03
- **Decididores**: Sebastian + cofounder/principal architect

## Contexto

Al diseñar el motor que decide el siguiente movimiento del bot, evaluamos enfoques basados en literatura académica reciente sobre diálogo orientado a objetivos:

- **PPDPP** (ICLR 2024): refuerzo + LLM para política de diálogo
- **ChatSOP** (ACL 2025): planeación con MCTS sobre estados de diálogo
- **GDP-Zero**: búsqueda Monte Carlo guiada por LLM para diálogos persuasivos

Estos enfoques producen mejor comportamiento conversacional en benchmarks (27–91% mejora en controllability sobre LLM puro), pero a un costo: ChatSOP reporta ~8-9× más tokens consumidos por turno, dado que cada nodo del árbol de búsqueda implica una o varias llamadas al LLM para evaluar el estado.

Para nuestro caso de uso (WhatsApp con clientes finales en LATAM, alta sensibilidad a costo y latencia, dominio comercial relativamente acotado), ese trade-off no compensa.

## Decisión

Usar un **DAG de checkpoints determinístico** como motor de política, con el LLM limitado a NLU/NLG. La estrategia se computa en código Python puro: dado el estado de información recolectada, el motor calcula qué checkpoint está completo, cuál está bloqueado, y cuál es el siguiente accionable. Sin búsqueda, sin árboles, sin llamadas adicionales al LLM para razonar sobre el estado.

## Alternativas consideradas

- **MCTS sobre estados de diálogo (ChatSOP-style)**: descartado por costo de tokens (~8-9× más por turno) y latencia. Para WhatsApp con turnos de varios segundos, agregar 2-3 segundos extra por turno degrada la experiencia notablemente.
- **Refuerzo/PPDPP-style**: descartado por requerir dataset histórico de conversaciones etiquetadas con outcome — no lo tenemos. Y para entrenarlo necesitaríamos primero operar en producción durante meses con un sistema más simple.
- **Slot filling tradicional (Rasa NLU/Dialogflow)**: descartado por fragilidad lingüística. La variedad de cómo un cliente colombiano pide café por WhatsApp es alta; las gramáticas tradicionales sobre-rechazan.
- **Agente ReAct con tools**: descartado por costo (5-15 LLM calls por turno) y debugabilidad. Ver ADR-002.

## Consecuencias

### Positivas
- Costo predecible: 1 LLM call por mensaje entrante, independiente de complejidad del DAG.
- Latencia predecible: el cómputo del DAG es microsegundos.
- Auditabilidad: cada decisión es código Python con tests unitarios. No hay caja negra.
- Customizable por cliente: `business_rules` JSONB permite modificar el DAG (skip checkpoints, agregar campos requeridos) sin código.
- El LLM se vuelve la pieza más reemplazable del sistema. Si sale un modelo mejor o más barato, se cambia el string del modelo.

### Negativas
- Cada nuevo "goal" (ej. servicio post-venta, soporte técnico) requiere escribir un nuevo `_build_*_checkpoints`. No es plug-and-play como un agente con tools.
- El sistema es bueno en el dominio para el que fue diseñado (venta) y mediocre fuera de él. No es un asistente general.
- Los DAG estáticos no aprenden del comportamiento real del cliente. Si descubrimos que el orden óptimo de pedir datos es distinto, hay que cambiar código, no parámetros.

### Trade-offs explícitos
- Ganamos confiabilidad y costo a cambio de flexibilidad. Para una startup en validación de producto con un dominio claro, esto es correcto.

## Cuándo revisar

Revisar esta decisión si:
- El dominio se vuelve mucho más amplio (10+ tipos de conversación distintos por cliente)
- Aparece evidencia de que el orden de checkpoints óptimo varía significativamente entre segmentos de clientes
- El costo de tokens deja de ser una restricción material (muy improbable en el horizonte cercano)
