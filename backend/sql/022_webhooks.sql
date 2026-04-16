-- 022: Outgoing webhook subscriptions + delivery log
-- Allows external systems to receive HTTP POST callbacks on charging events

CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name VARCHAR(200) NOT NULL,
    url VARCHAR(2000) NOT NULL,
    secret VARCHAR(500) NOT NULL,
    events TEXT[] NOT NULL DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS webhook_delivery_log (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    subscription_id VARCHAR NOT NULL REFERENCES webhook_subscriptions(id) ON DELETE CASCADE,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    status_code INTEGER,
    response_body TEXT,
    attempt INTEGER DEFAULT 1,
    success BOOLEAN DEFAULT false,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_webhook_subs_active ON webhook_subscriptions(is_active);
CREATE INDEX IF NOT EXISTS idx_webhook_log_sub ON webhook_delivery_log(subscription_id);
CREATE INDEX IF NOT EXISTS idx_webhook_log_created ON webhook_delivery_log(created_at);
