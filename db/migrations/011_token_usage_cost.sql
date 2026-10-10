-- Migration: per-call cost metering on token_usage
-- Adds the fields needed to price and attribute every model call made through the
-- gateway (api/services/usage.py records them; agents/pricing.py prices them).
-- Existing rows keep working: every new column is nullable or has a default.

ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS run_id VARCHAR(255);
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS api_key_id INTEGER;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS api_key_name VARCHAR(255);
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255);
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS request_model VARCHAR(100);
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS provider VARCHAR(50);
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS cache_read_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS cache_write_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS reasoning_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS model_requests INTEGER NOT NULL DEFAULT 0;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS tool_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS duration_ms INTEGER;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS ttft_ms INTEGER;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(12, 6);
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS unpriced_models TEXT[];
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS prices_version VARCHAR(20);
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'completed';
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS breakdown JSONB;

COMMENT ON COLUMN token_usage.cost_usd IS 'USD at the prices in prices_version - NULL when no model of the call could be priced';
COMMENT ON COLUMN token_usage.status IS 'completed, error, refused, paused or cancelled - failed calls are billed too';
COMMENT ON COLUMN token_usage.breakdown IS 'Per model type (model, memory_model, compression_model, ...): id, provider, tokens, cost_usd';

CREATE INDEX IF NOT EXISTS idx_token_usage_agent_created ON token_usage(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_tenant_created ON token_usage(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_api_key_created ON token_usage(api_key_id, created_at);
