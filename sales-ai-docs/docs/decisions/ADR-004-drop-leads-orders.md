# ADR-004 — Drop de leads, orders, order_line_items

- **Estatus**: Accepted
- **Fecha**: 2026-04-21
- **Decididores**: Sebastian + cofounder/principal architect

## Contexto

El schema inicial (migración 001) incluía tres tablas para modelar el ciclo de vida comercial:

- `leads` — oportunidades de venta con estado `new → contacted → qualified → proposal_sent → won/lost`
- `orders` — pedidos con estado `draft → confirmed → processing → shipped → delivered/cancelled`
- `order_line_items` — items de pedido con `unit_price` snapshot del catálogo

El diseño asumía que el agente, al detectar intención de compra, crearía un lead, lo iría enriqueciendo, y eventualmente materializaría un order con line items. La arquitectura tenía action handlers (`create_lead`, `propose_order`, `confirm_order`, `cancel_order`) que el agente proponía y el backend validaba.

Tras el refactor del 19 de abril (que también colapsó la state machine — ver ADR-007), el sistema dejó de usar action handlers. El flujo real era:
- El cliente expresa intención → todo se acumula en `conversations.extracted_context` como JSONB
- Cuando todo está completo → auto-escalate a `human_handoff`
- Un humano cierra la venta manualmente fuera del sistema (toma el comprobante de pago, coordina envío)

Auditando la DB (mediados de abril): las tres tablas estaban con 0 filas. Ningún code path las poblaba después del refactor. Mantenerlas en el schema activo era confuso (sugería que existía una capa de "órdenes formales" que no existía) y genera deuda mental al razonar sobre el sistema.

## Decisión

Dropear las tres tablas (`leads`, `orders`, `order_line_items`) y todas las columnas relacionadas en `conversations` (`lead_id`, `order_id`, etc.) en la migración 007. El estado de la venta vive enteramente en `conversations.extracted_context` (JSONB) durante la conversación activa, y el cierre se hace fuera del sistema por un humano.

## Alternativas consideradas

- **Mantener las tablas vacías "por si acaso"**: descartado. Schema activo debe reflejar la realidad. Cargar peso muerto solo confunde.
- **Mantener las tablas y empezar a poblarlas**: requería reintroducir action handlers, validación de stock, gestión de estados de orden, etc. — todo trabajo grande para un valor marginal en MVP. Descartado por priorización.
- **Mover a una migración futura**: descartado. Si la deuda existe, quitarla pronto evita que código nuevo siga asumiendo que existen.

## Consecuencias

### Positivas
- Schema activo refleja la realidad del sistema. Razonar sobre el modelo es más simple.
- Menos chance de que código nuevo (incluyendo Claude Code) referencie tablas inexistentes asumiendo que están vivas.
- La migración 007 también dropeó columnas mirror que estaban siempre nulas (`whatsapp_id`, `email`, `full_name`, etc. en `client_users`) — agregando coherencia.

### Negativas
- **Pérdida del modelo de venta como entidad de negocio.** Reportes de "cuántas ventas cerradas el mes pasado" requieren parsear JSONB de `conversations` o consultar `client_users.profile.purchases`. Es viable pero menos elegante que un `SELECT COUNT(*) FROM orders WHERE status = 'confirmed'`.
- **Pérdida de continuidad entre conversaciones.** Si una conversación expira por la ventana de 24h y el cliente vuelve después, el estado de la venta se pierde porque vivía en `extracted_context` de la conversación vieja. Esto produce el bug del re-saludo y la pérdida de contexto de venta entre sesiones. (Detectado como problema en discusión de roadmap del 25 de abril.)
- Si en el futuro queremos materialización formal de órdenes (para pagar APIs de envío, integrar pasarelas, etc.), habrá que reintroducir entidades.

### Trade-offs explícitos
- Ganamos simplicidad y velocidad de iteración a cambio de poder de modelado. Para MVP sin volumen, vale la pena. Para producción con varios clientes, va a ser necesario reintroducir alguna forma de entidad de venta.

## Cuándo revisar

**Esta decisión está pidiendo revisión activamente.** El bug de pérdida de contexto entre conversaciones (carrito abandonado se pierde) ya está afectando UX. La solución probable es reintroducir una tabla nueva con propósito más claro (`purchase_intents`), no resucitar `leads`/`orders`. Cuando eso pase, este ADR queda como contexto histórico (no superseded — la decisión de dropear esas tablas específicas sigue válida) y se escribe ADR-008 para `purchase_intents`.

Revisar también si:
- Aterrizamos un cliente con volumen donde reportes sobre JSONB se vuelven lentos (improbable a corto plazo, JSONB con índices funciona bien hasta cientos de miles de filas)
- Se necesita integrar pasarela de pago o API de envíos automatizada (eso requiere entidades formales de orden)
