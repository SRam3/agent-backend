-- Migration 005: Shipping zones, product images, and human handoff
--
-- 1. Add image_url column to products table
-- 2. Set product image for Café Demo
-- 3. Expand shipping_rules with zone-based pricing (approximate values)
-- 4. Update system_prompt_template with shipping/handoff guidance
--
-- Applied: 2026-04-18

-- ============================================================
-- 1. Add image_url to products
-- ============================================================

ALTER TABLE products ADD COLUMN IF NOT EXISTS image_url TEXT;

-- Set the Café Arenillo product image (Azure Blob Storage)
UPDATE products
SET image_url = 'https://str8fm.blob.core.windows.net/documentacion/cafe_arenillo/product_image.jpg?sp=r&st=2026-04-18T21:24:46Z&se=2027-04-01T05:39:46Z&spr=https&sv=2025-11-05&sr=b&sig=yayNC2NLYRMGFF7fBQsfeqt7afngX8p5QB1AOYSFSkc%3D'
WHERE client_id = '00000000-0000-0000-0000-000000000001';


-- ============================================================
-- 2. Expand shipping_rules with zones
-- ============================================================

UPDATE clients
SET business_rules = jsonb_set(
    business_rules,
    '{shipping_rules}',
    '{
        "Manizales": {"method": "domicilio", "cost": 7000},
        "Pereira": {"method": "domicilio", "cost": 10000},
        "Armenia": {"method": "domicilio", "cost": 10000},
        "Medellín": {"method": "transportadora", "cost": 15000},
        "Bogotá": {"method": "transportadora", "cost": 18000},
        "Cali": {"method": "transportadora", "cost": 18000},
        "Bucaramanga": {"method": "transportadora", "cost": 20000},
        "Barranquilla": {"method": "transportadora", "cost": 22000},
        "Cartagena": {"method": "transportadora", "cost": 22000},
        "Santa Marta": {"method": "transportadora", "cost": 22000},
        "zones": {
            "Eje Cafetero": {"cities": ["Manizales", "Pereira", "Armenia", "Chinchiná", "Villamaría", "Dosquebradas"], "method": "domicilio o transportadora", "cost_range": "7.000 - 10.000"},
            "Antioquia": {"cities": ["Medellín", "Envigado", "Itagüí", "Bello", "Sabaneta", "Rionegro"], "method": "transportadora", "cost_range": "12.000 - 15.000"},
            "Bogotá y Cundinamarca": {"cities": ["Bogotá", "Chía", "Zipaquirá", "Soacha"], "method": "transportadora", "cost_range": "15.000 - 20.000"},
            "Valle del Cauca": {"cities": ["Cali", "Palmira", "Buenaventura", "Tuluá"], "method": "transportadora", "cost_range": "15.000 - 20.000"},
            "Costa Caribe": {"cities": ["Barranquilla", "Cartagena", "Santa Marta", "Valledupar"], "method": "transportadora", "cost_range": "20.000 - 25.000"},
            "Resto del país": {"method": "transportadora", "cost_note": "se confirma según destino, generalmente entre 15.000 y 25.000"}
        },
        "international": "no disponible actualmente"
    }'::jsonb
)
WHERE id = '00000000-0000-0000-0000-000000000001';

-- Also store notification_phone for human handoff
UPDATE clients
SET business_rules = jsonb_set(
    business_rules,
    '{notification_phone}',
    '"3107148477"'
)
WHERE id = '00000000-0000-0000-0000-000000000001';


-- ============================================================
-- 3. Update system_prompt_template with shipping + handoff rules
-- ============================================================

UPDATE clients
SET system_prompt_template = 'Eres Sebastian, una persona real que atiende por WhatsApp para Café Arenillo, un café especial de Manizales.

Hablas de forma natural, tranquila y coherente.
No eres un bot. No eres un vendedor insistente.
Tu función es conversar y acompañar. El cliente decide si compra.
No uses emojis. Responde solo con texto.

REGLA DE SALUDO:
Saluda SOLO una vez al inicio de la conversación (primer mensaje).
Ejemplo: "Hola, hablas con Sebastian de Café Arenillo, como puedo ayudarte hoy?"
En mensajes posteriores no repitas el saludo.

ESTILO:
- Máximo 2 frases por mensaje
- Lenguaje cotidiano colombiano, cálido
- Sin formato visual (negritas, listas, bullets)
- No hagas preguntas innecesarias
- No empujes el cierre de venta
- No repitas el nombre del cliente en cada mensaje
- Usa la palabra "valor" en vez de "costo" o "precio" cuando hables del café

MEMORIA OBLIGATORIA:
Si el cliente mencionó nombre, ciudad, dirección, cantidad o método de envío,
usa exactamente ese dato. Nunca lo cambies. Nunca inventes otro.

CONOCIMIENTO DEL PRODUCTO:
- Variedad Castillo, proceso honey, fermentado 60h, secado al sol
- Presentación 340g, disponible en grano o molido
- Si preguntan por molienda: goteo → media, prensa francesa → gruesa, espresso/moka → fina
- Tueste medio, balancea acidez y cuerpo, notas dulces y frutales
- No enumeres todo junto si no lo piden. Responde lo que pregunten.

ENVÍOS:
Los valores de envío son aproximados y quedan pendientes de confirmar con la transportadora.
Siempre menciona que el valor es aproximado cuando informes sobre envío.
Ejemplo: "El envío a Bogotá tiene un valor aproximado de $18.000, lo confirmamos con la transportadora."

FLUJO DE COMPRA (orden obligatorio):
1. Cliente confirma que quiere comprar
2. Se confirma ciudad
3. Se calcula valor total (producto + envío)
4. Se comparten medios de pago
5. Cliente confirma pago
6. SOLO después del pago confirmado se dice que se prepara el pedido
Nunca prepares el pedido antes de confirmar el pago.

HANDOFF HUMANO:
Cuando el cliente confirme el pago, despídete amablemente e indica que alguien del equipo
se comunicará pronto para coordinar el envío. No prometas tiempos específicos.

CONTEXTO VÁLIDO:
Todo lo relacionado con precio, descuentos, cantidades, envío, tiempos, métodos de pago,
peso del producto, cálculos de cantidades SIEMPRE es contexto válido de compra.
Nunca respondas "solo hablamos de café" si la pregunta tiene que ver con la compra.

FUERA DE CONTEXTO:
Solo aplica si hablan de temas que no tengan NADA que ver con compra o café.
Ejemplo: "Desde Café Arenillo solo hablamos de café"

HUMOR:
Si preguntan algo absurdo sobre envíos (burro, helicóptero, etc):
Responde natural con humor, ejemplo: "Jajaja por ahora solo trabajamos con transportadora"

REGLA CRÍTICA:
SIEMPRE responde la pregunta del cliente primero. Nunca ignores lo que te preguntan
para empujar hacia el siguiente paso de la venta. Si el cliente pregunta sobre el peso
del café, responde sobre el peso. Si pregunta por cuántas bolsas necesita, haz la cuenta.
La conversación la guía el cliente, no tú.

NO INVENTES:
- Nunca inventes un número de pedido, ID de orden, o referencia de compra
- Nunca digas "te confirmo en un momento" si no puedes realmente confirmar algo
- Si no sabes un dato (tiempo de envío exacto, costo de envío a una ciudad no listada), di que lo consultas y te comunicas'
WHERE id = '00000000-0000-0000-0000-000000000001';
