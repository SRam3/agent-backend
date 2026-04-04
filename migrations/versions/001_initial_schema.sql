-- ============================================================
-- Migration 001: Initial schema for sales_ai database
-- Applied to: psql-r8fm.postgres.database.azure.com / sales_ai
-- ============================================================

-- Requires: CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- Helper: updated_at trigger function
-- ============================================================
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ============================================================
-- TABLE: clients
-- Tenant boundary — every other table references this.
-- ============================================================
CREATE TABLE clients (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                    VARCHAR(255) NOT NULL,
    slug                    VARCHAR(100) NOT NULL UNIQUE,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    chakra_phone_number_id  VARCHAR(50),
    chakra_secret_ref       VARCHAR(255),
    system_prompt_template  TEXT,
    ai_model                VARCHAR(100) NOT NULL DEFAULT 'gpt-4o-mini',
    ai_temperature          NUMERIC(3,2) NOT NULL DEFAULT 0.3,
    max_tool_calls_per_turn INTEGER NOT NULL DEFAULT 3,
    business_rules          JSONB NOT NULL DEFAULT '{}',
    message_retention_days  INTEGER,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_clients_updated_at
    BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();


-- ============================================================
-- TABLE: client_users
-- End customers, unique by (client_id, phone_number).
-- ============================================================
CREATE TABLE client_users (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id               UUID NOT NULL REFERENCES clients(id),
    phone_number            VARCHAR(20) NOT NULL,
    whatsapp_id             VARCHAR(50),
    display_name            VARCHAR(255),
    email                   VARCHAR(255),
    full_name               VARCHAR(255),
    address                 TEXT,
    city                    VARCHAR(100),
    identification_number   VARCHAR(50),
    first_contact_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_contact_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_blocked              BOOLEAN NOT NULL DEFAULT FALSE,
    metadata                JSONB NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_client_user_phone UNIQUE (client_id, phone_number)
);

CREATE INDEX ix_client_users_client_id ON client_users (client_id);
CREATE INDEX ix_client_users_phone     ON client_users (phone_number);

CREATE TRIGGER trg_client_users_updated_at
    BEFORE UPDATE ON client_users
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();


-- ============================================================
-- TABLE: products
-- Catalog per client. Prices are source of truth for orders.
-- ============================================================
CREATE TABLE products (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id       UUID NOT NULL REFERENCES clients(id),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    sku             VARCHAR(100),
    price           NUMERIC(12,2) NOT NULL,
    is_available    BOOLEAN NOT NULL DEFAULT TRUE,
    tags            JSONB NOT NULL DEFAULT '[]',
    ai_description  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_product_sku UNIQUE (client_id, sku)
);

CREATE INDEX ix_products_client_id  ON products (client_id);
CREATE INDEX ix_products_available  ON products (client_id, is_available);

CREATE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();


-- ============================================================
-- TABLE: leads
-- Sales opportunities. Created by backend, not agent.
-- ============================================================
CREATE TABLE leads (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id               UUID NOT NULL REFERENCES clients(id),
    client_user_id          UUID NOT NULL REFERENCES client_users(id),
    status                  VARCHAR(20) NOT NULL DEFAULT 'new',
    intent                  VARCHAR(255),
    score                   INTEGER,
    qualification_data      JSONB NOT NULL DEFAULT '{}',
    source_conversation_id  UUID,  -- FK to conversations added after that table exists
    assigned_to             VARCHAR(100),
    qualified_at            TIMESTAMPTZ,
    won_at                  TIMESTAMPTZ,
    lost_at                 TIMESTAMPTZ,
    lost_reason             TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_lead_status CHECK (
        status IN ('new','contacted','qualified','proposal_sent','won','lost')
    )
);

CREATE INDEX ix_leads_client_id      ON leads (client_id);
CREATE INDEX ix_leads_client_user_id ON leads (client_user_id);
CREATE INDEX ix_leads_status         ON leads (client_id, status);

CREATE TRIGGER trg_leads_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();


-- ============================================================
-- TABLE: orders
-- Purchase orders. Line items carry snapshot prices.
-- ============================================================
CREATE TABLE orders (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id               UUID NOT NULL REFERENCES clients(id),
    client_user_id          UUID NOT NULL REFERENCES client_users(id),
    lead_id                 UUID REFERENCES leads(id),
    status                  VARCHAR(20) NOT NULL DEFAULT 'draft',
    shipping_name           VARCHAR(255),
    shipping_address        TEXT,
    shipping_city           VARCHAR(100),
    shipping_phone          VARCHAR(20),
    subtotal                NUMERIC(12,2) NOT NULL DEFAULT 0,
    shipping_cost           NUMERIC(12,2) NOT NULL DEFAULT 0,
    total                   NUMERIC(12,2) NOT NULL DEFAULT 0,
    source_conversation_id  UUID,  -- FK added after conversations exists
    confirmed_at            TIMESTAMPTZ,
    cancelled_at            TIMESTAMPTZ,
    cancel_reason           TEXT,
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_order_status CHECK (
        status IN ('draft','confirmed','processing','shipped','delivered','cancelled')
    )
);

CREATE INDEX ix_orders_client_id      ON orders (client_id);
CREATE INDEX ix_orders_client_user_id ON orders (client_user_id);
CREATE INDEX ix_orders_status         ON orders (client_id, status);

CREATE TRIGGER trg_orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();


-- ============================================================
-- TABLE: conversations
-- Chat sessions. State machine + strategy tracking lives here.
-- ============================================================
CREATE TABLE conversations (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id               UUID NOT NULL REFERENCES clients(id),
    client_user_id          UUID NOT NULL REFERENCES client_users(id),
    state                   VARCHAR(30) NOT NULL DEFAULT 'active',
    previous_state          VARCHAR(30),
    extracted_context       JSONB NOT NULL DEFAULT '{}',
    lead_id                 UUID REFERENCES leads(id),
    order_id                UUID REFERENCES orders(id),
    assigned_operator_id    VARCHAR(100),
    escalation_reason       TEXT,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at               TIMESTAMPTZ,
    message_count           INTEGER NOT NULL DEFAULT 0,
    agent_turn_count        INTEGER NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_conversation_state CHECK (
        state IN ('idle','active','qualifying','selling','ordering','human_handoff','closed')
    )
);

CREATE INDEX ix_conversations_client_id      ON conversations (client_id);
CREATE INDEX ix_conversations_client_user_id ON conversations (client_user_id);
CREATE INDEX ix_conversations_state          ON conversations (client_id, state);
CREATE INDEX ix_conversations_last_message   ON conversations (client_id, last_message_at);

CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- Now add deferred FKs that needed conversations to exist first
ALTER TABLE leads  ADD CONSTRAINT fk_leads_source_conversation
    FOREIGN KEY (source_conversation_id) REFERENCES conversations(id);
ALTER TABLE orders ADD CONSTRAINT orders_source_conversation_id_fkey
    FOREIGN KEY (source_conversation_id) REFERENCES conversations(id);


-- ============================================================
-- TABLE: messages
-- Every message in/out — audit trail + decision explainability.
-- ============================================================
CREATE TABLE messages (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id         UUID NOT NULL REFERENCES conversations(id),
    client_id               UUID NOT NULL REFERENCES clients(id),
    direction               VARCHAR(10) NOT NULL,
    message_type            VARCHAR(20) NOT NULL DEFAULT 'text',
    content                 TEXT,
    media_url               VARCHAR(2048),
    media_mime_type         VARCHAR(100),
    chakra_message_id       VARCHAR(100) UNIQUE,
    idempotency_key         VARCHAR(100) NOT NULL DEFAULT uuid_generate_v4()::TEXT,
    delivery_status         VARCHAR(20),
    delivered_at            TIMESTAMPTZ,
    read_at                 TIMESTAMPTZ,
    ai_model_used           VARCHAR(100),
    ai_prompt_tokens        INTEGER,
    ai_completion_tokens    INTEGER,
    ai_latency_ms           INTEGER,
    proposed_action         VARCHAR(50),
    action_approved         BOOLEAN,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_message_direction CHECK (direction IN ('inbound','outbound')),
    CONSTRAINT uq_message_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX ix_messages_conversation_id    ON messages (conversation_id);
CREATE INDEX ix_messages_client_id_created  ON messages (client_id, created_at);
CREATE INDEX ix_messages_delivery_status    ON messages (client_id, delivery_status);


-- ============================================================
-- TABLE: order_line_items
-- Items with snapshot prices from products table.
-- ============================================================
CREATE TABLE order_line_items (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id        UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id      UUID NOT NULL REFERENCES products(id),
    product_name    VARCHAR(255) NOT NULL,
    unit_price      NUMERIC(12,2) NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    subtotal        NUMERIC(12,2) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_line_item_quantity_positive CHECK (quantity > 0),
    CONSTRAINT ck_line_item_price_non_negative CHECK (unit_price >= 0)
);

CREATE INDEX ix_order_line_items_order_id ON order_line_items (order_id);


-- ============================================================
-- TABLE: audit_log
-- Append-only event trail for traceability.
-- ============================================================
CREATE TABLE audit_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id       UUID NOT NULL REFERENCES clients(id),
    event_type      VARCHAR(100) NOT NULL,
    entity_type     VARCHAR(50) NOT NULL,
    entity_id       UUID NOT NULL,
    actor_type      VARCHAR(20) NOT NULL,
    actor_id        VARCHAR(100),
    old_value       JSONB,
    new_value       JSONB,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_audit_log_client_id ON audit_log (client_id);
CREATE INDEX ix_audit_log_entity    ON audit_log (entity_type, entity_id);
CREATE INDEX ix_audit_log_created   ON audit_log (client_id, created_at);


-- ============================================================
-- SEED DATA: Café Demo
-- ============================================================
INSERT INTO clients (id, name, slug, is_active, ai_model, business_rules) VALUES
(
    '00000000-0000-0000-0000-000000000001',
    'Café Demo',
    'cafe-demo',
    TRUE,
    'gpt-4o-mini',
    '{
        "currency": "COP",
        "default_goal": "close_sale",
        "shipping_cities": ["Manizales", "Pereira", "Armenia"],
        "require_address_for_order": true,
        "auto_escalate_after_minutes": 30
    }'::jsonb
);

INSERT INTO products (client_id, name, description, sku, price, is_available) VALUES
('00000000-0000-0000-0000-000000000001', 'Café Molido Premium',    'Café colombiano de origen único, molido grueso para cafetera francesa', 'CAFE-001', 25000, TRUE),
('00000000-0000-0000-0000-000000000001', 'Café en Grano Especial', 'Granos seleccionados de fincas cafeteras del Eje Cafetero',             'CAFE-002', 45000, TRUE),
('00000000-0000-0000-0000-000000000001', 'Kit Barista',            'Incluye café molido, prensa francesa y guía de preparación',            'KIT-001',  89000, TRUE);
