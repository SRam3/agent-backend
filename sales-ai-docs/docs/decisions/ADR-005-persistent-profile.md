# ADR-005 — Perfil persistente en client_users

- **Estatus**: Accepted
- **Fecha**: 2026-04-21
- **Decididores**: Sebastian + cofounder/principal architect

## Contexto

Antes de la migración 007, los datos del cliente final vivían en dos lugares:

- Columnas mirror en `client_users` (`full_name`, `email`, `address`, `city`, `identification_number`, `whatsapp_id`, `metadata`) — pensadas para almacenar datos persistentes
- `conversations.extracted_context` (JSONB) — usado en la práctica para todo

Auditando la DB: las columnas mirror estaban siempre nulas. El código nunca las llenaba después del refactor del 19 de abril. Toda la información del cliente se acumulaba en `extracted_context`, lo que significa que se perdía al expirar la conversación (ventana de 24h o reset por idle de 30 min).

Síntoma visible: un cliente que vuelve después de varios días es tratado como nuevo. El bot le pregunta nombre, ciudad, dirección — datos que el sistema ya tenía pero no pudo recuperar.

## Decisión

Eliminar las columnas mirror de `client_users` y reemplazarlas con una sola columna `profile JSONB NOT NULL DEFAULT '{}'::jsonb`. Esta columna almacena datos persistentes del cliente entre conversaciones:

```json
{
  "first_name": "Juan",
  "full_name": "Juan Pérez",
  "email": "...",
  "city": "Manizales",
  "shipping_address": "Calle 10 #5-20",
  "preferences": {"grind": "molido", "roast": "medio"},
  "purchase_count": 2,
  "purchases": [{"date": "...", "product_id": "...", "total": 80000}],
  "last_conversation_summary": "..."
}
```

Lógica de sincronización:
- En `agent_action.py`, cuando se aceptan slots estables (full_name, phone, shipping_address, shipping_city, email), se mergean a `profile` además de a `extracted_context`.
- En `ingest.py`, cuando se crea una conversación nueva, se hace seed del `extracted_context` desde el `profile` para que el LLM arranque sabiendo lo que tenemos en archivo.

## Alternativas consideradas

- **Mantener columnas tipadas**: más estricto en schema, pero requiere migración cada vez que aparece un campo nuevo (preferencias, historial de compras, etc.). Descartado por velocidad de iteración.
- **Tabla separada `client_user_profiles`** con relación 1:1: técnicamente más limpia pero overkill para la cantidad de datos. Una sola fila por usuario, JOINs adicionales en cada query. Descartado.
- **Solo `extracted_context`, sin perfil**: descartado — es el problema actual que se quiere resolver.

## Consecuencias

### Positivas
- Cliente recurrente es reconocido entre conversaciones. El system prompt del LLM puede saludar diferente si el cliente ya compró antes.
- Schema flexible para agregar campos sin migraciones (preferencias nuevas, segmentación, etc.).
- El bug del re-saludo en el primer mensaje de una nueva conversación queda parcialmente resuelto (el LLM recibe el contexto, aunque la regla del prompt sigue siendo importante).
- Capacidad emergente: `purchase_count` y `purchases` permiten lógica de remarketing y recompra.

### Negativas
- Schema flexible = sin garantías de tipos. Un campo mal escrito (typo en `frist_name`) no falla; simplemente no se usa. Mitigación: validación en código + helper de seed/merge centralizado.
- Datos sensibles (PII) en JSONB son menos auditables que en columnas. No hay forma trivial de hacer "encripta solo la columna `phone`" si llegáramos a necesitarlo. Mitigación pendiente: si encriptación a nivel campo se vuelve requirement, considerar mover esos campos a columnas tipadas.
- **NO resuelve el caso de venta interrumpida.** El `profile` guarda datos del cliente, no estado de venta en curso. Si un cliente estaba a punto de comprar 3 bolsas y vuelve después, el `profile` sabe quién es pero no que estaba comprando. Eso requiere otra entidad — pendiente de ADR-008 (`purchase_intents`).

### Trade-offs explícitos
- Aceptamos schema laxo a cambio de iteración rápida. Estamos en MVP; la rigidez correcta es la que sirve hoy, no la que servirá en 2 años.

## Cuándo revisar

Revisar esta decisión si:
- La cantidad de campos del perfil crece a 20+ y se vuelve confuso navegarlos como JSONB
- Aparecen requirements de compliance (encriptación a nivel campo, retention policies por tipo de dato)
- El segundo cliente en producción tiene shape de perfil radicalmente distinta y forzar `business_rules` para customizar deja de ser razonable
