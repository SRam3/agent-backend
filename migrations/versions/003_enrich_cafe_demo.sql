-- ============================================================
-- Migration 003: Enrich Café Demo seed data
--
-- Updates:
--   1. Replace placeholder products with real Café Arenillo product
--   2. Enrich business_rules with shipping, payment, discounts, persona
--   3. Set system_prompt_template for the Café Demo client
--
-- Applied to: <POSTGRES_HOST>.postgres.database.azure.com / sales_ai
-- ============================================================

-- ============================================================
-- 1. Replace products — remove placeholders, insert real product
-- ============================================================

-- First check for any order_line_items referencing these products
-- (safe: dev environment, no real orders yet)
DELETE FROM order_line_items
WHERE product_id IN (
    SELECT id FROM products
    WHERE client_id = '00000000-0000-0000-0000-000000000001'
);

DELETE FROM products
WHERE client_id = '00000000-0000-0000-0000-000000000001';

INSERT INTO products (client_id, name, description, sku, price, is_available, ai_description) VALUES
(
    '00000000-0000-0000-0000-000000000001',
    'Café Arenillo',
    'Variedad Castillo, proceso honey, presentación 340g. Disponible en grano o molido.',
    'CAFE-001',
    40000,
    TRUE,
    'Café especial de Manizales. Variedad Castillo, proceso honey, 340g. Disponible en grano o molido.'
);


-- ============================================================
-- 2. Enrich business_rules
-- ============================================================

UPDATE clients
SET business_rules = '{
    "currency": "COP",
    "default_goal": "close_sale",
    "shipping_cities": ["Manizales", "Pereira", "Armenia"],
    "require_address_for_order": true,
    "auto_escalate_after_minutes": 30,
    "shipping_rules": {
        "Manizales": {"method": "domicilio", "cost": 7000},
        "Medellín": {"method": "Uber envíos", "cost_note": "variable según distancia"},
        "Bogotá": {"method": "transportadora", "cost_note": "desde 20.000 si supera 2kg"},
        "other": {"method": "transportadora", "cost_note": "se confirma según transportadora"},
        "international": "no disponible actualmente"
    },
    "payment_methods": [
        {"type": "bank_transfer", "bank": "Bancolombia", "account_type": "ahorros", "account": "05965752562"},
        {"type": "nequi", "number": "3107148477"}
    ],
    "discount_rules": {
        "no_discount_message": "No ofrecemos descuento por unidad",
        "bulk_threshold": 10,
        "bulk_message": "Podemos revisar un precio especial por volumen"
    },
    "agent_persona": {
        "name": "Sebastian",
        "role": "persona real que atiende por WhatsApp"
    }
}'::jsonb
WHERE id = '00000000-0000-0000-0000-000000000001';


-- ============================================================
-- 3. Set system_prompt_template
-- ============================================================

UPDATE clients
SET system_prompt_template = 'Eres Sebastian, una persona real que atiende por WhatsApp para Café Arenillo, un café especial de Manizales.

Hablas de forma natural, tranquila y coherente.
No eres un bot. No eres un vendedor insistente.
Tu función es conversar y acompañar. El cliente decide si compra.

ESTILO:
- Máximo 2 frases por mensaje
- Lenguaje cotidiano colombiano
- Sin formato visual (negritas, listas, bullets)
- No hagas preguntas innecesarias
- No empujes el cierre de venta

DATOS DEL CLIENTE:
Si el cliente ya proporcionó datos, úsalos exactamente como los dio.
Nunca inventes nombre, ciudad, dirección ni cantidad.

FUERA DE CONTEXTO:
Si hablan de temas que no tienen nada que ver con café o compra:
"Desde Café Arenillo solo hablamos de café"
No lo uses para descuentos, envíos o logística — eso SÍ es contexto válido.

REGLA DE SALUDO:
Saluda SOLO una vez al inicio de la conversación (primer mensaje).
En mensajes posteriores no repitas el saludo.'
WHERE id = '00000000-0000-0000-0000-000000000001';
