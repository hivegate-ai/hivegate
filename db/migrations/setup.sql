-- Database Setup Script
-- This script creates all tables and schemas required for the agent-api
-- Run this script for fresh database setup
--
-- Schemas:
--   public  - Core application tables (agent_info, teams, tokens, knowledge, usage)
--   prompts - Prompt templates storage
--   ai      - Agno framework (auto-created at runtime)

-- ============================================================================
-- SHARED FUNCTIONS
-- ============================================================================

-- Create or replace the update_updated_at_column function (used by all tables)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- AGENT INFO TABLE (public schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS agent_info (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    description TEXT,
    version VARCHAR NOT NULL DEFAULT '2.0',
    prompt_service_id VARCHAR NOT NULL,
    tags VARCHAR,  -- JSON string for tags
    config TEXT,   -- JSON configuration for agent runtime settings (memory, history, reasoning)
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_agent_info_is_active ON agent_info(is_active);
CREATE INDEX IF NOT EXISTS idx_agent_info_created_at ON agent_info(created_at);
CREATE INDEX IF NOT EXISTS idx_agent_info_prompt_service_id ON agent_info(prompt_service_id);

DROP TRIGGER IF EXISTS update_agent_info_updated_at ON agent_info;
CREATE TRIGGER update_agent_info_updated_at
    BEFORE UPDATE ON agent_info
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMENT ON COLUMN agent_info.config IS 'JSON configuration for agent runtime settings (memory, history, reasoning)';

-- ============================================================================
-- TEAM INFO TABLE (public schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS team_info (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    description TEXT,
    version VARCHAR NOT NULL DEFAULT '2.0',
    mode VARCHAR NOT NULL DEFAULT 'coordinate',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS team_agent (
    id SERIAL PRIMARY KEY,
    team_id VARCHAR NOT NULL,
    agent_id VARCHAR NOT NULL,
    role VARCHAR,
    order_index INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_team_info_is_active ON team_info(is_active);
CREATE INDEX IF NOT EXISTS idx_team_info_created_at ON team_info(created_at);
CREATE INDEX IF NOT EXISTS idx_team_agent_team_id ON team_agent(team_id);
CREATE INDEX IF NOT EXISTS idx_team_agent_agent_id ON team_agent(agent_id);
CREATE INDEX IF NOT EXISTS idx_team_agent_is_active ON team_agent(is_active);
CREATE INDEX IF NOT EXISTS idx_team_agent_order ON team_agent(team_id, order_index);

CREATE UNIQUE INDEX IF NOT EXISTS uq_team_agent_active
    ON team_agent(team_id, agent_id)
    WHERE is_active = TRUE;

DROP TRIGGER IF EXISTS update_team_info_updated_at ON team_info;
CREATE TRIGGER update_team_info_updated_at
    BEFORE UPDATE ON team_info
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- KNOWLEDGE ENTRIES TABLE (public schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS knowledge_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(255) NOT NULL,
    collection_id VARCHAR(255),
    file_id UUID NOT NULL,
    original_filename VARCHAR(500) NOT NULL,
    file_type VARCHAR(50) NOT NULL CHECK (file_type IN ('company', 'project')),
    content_type VARCHAR(200),
    gcs_path VARCHAR(1000),
    status VARCHAR(50) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'active', 'failed', 'deleted')),
    knowledge_status VARCHAR(50) NOT NULL DEFAULT 'indexing' CHECK (knowledge_status IN ('indexing', 'indexed', 'failed', 'outdated')),
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_knowledge_tenant_file UNIQUE(tenant_id, file_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_tenant_id ON knowledge_entries(tenant_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_collection_id ON knowledge_entries(collection_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_file_id ON knowledge_entries(file_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge_entries(status);
CREATE INDEX IF NOT EXISTS idx_knowledge_knowledge_status ON knowledge_entries(knowledge_status);
CREATE INDEX IF NOT EXISTS idx_knowledge_created_at ON knowledge_entries(created_at);

DROP TRIGGER IF EXISTS update_knowledge_entries_updated_at ON knowledge_entries;
CREATE TRIGGER update_knowledge_entries_updated_at
    BEFORE UPDATE ON knowledge_entries
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- USER TOKENS TABLE (public schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_tokens (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    integration_key VARCHAR(100) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    token_type VARCHAR(20) NOT NULL CHECK (token_type IN ('oauth2', 'api_key', 'jwt')),
    encrypted_token_data TEXT NOT NULL,
    scopes TEXT[],
    expires_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_user_token_integration UNIQUE (user_id, integration_key)
);

CREATE INDEX IF NOT EXISTS idx_user_tokens_user_id ON user_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_user_tokens_integration_key ON user_tokens(integration_key);
CREATE INDEX IF NOT EXISTS idx_user_tokens_expires_at ON user_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_user_tokens_created_at ON user_tokens(created_at);

DROP TRIGGER IF EXISTS update_user_tokens_updated_at ON user_tokens;
CREATE TRIGGER update_user_tokens_updated_at
    BEFORE UPDATE ON user_tokens
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE user_tokens IS 'Stores encrypted user tokens for various integrations';
COMMENT ON COLUMN user_tokens.user_id IS 'User identifier';
COMMENT ON COLUMN user_tokens.integration_key IS 'Integration key (e.g., google, openai, slack)';
COMMENT ON COLUMN user_tokens.provider IS 'Provider name for grouping';
COMMENT ON COLUMN user_tokens.token_type IS 'Token type: oauth2, api_key, or jwt';
COMMENT ON COLUMN user_tokens.encrypted_token_data IS 'Encrypted JSON blob containing token data';
COMMENT ON COLUMN user_tokens.scopes IS 'Array of permission scopes';
COMMENT ON COLUMN user_tokens.expires_at IS 'Token expiration timestamp (nullable for non-expiring tokens)';
COMMENT ON COLUMN user_tokens.is_active IS 'Soft delete flag';

-- ============================================================================
-- TOKEN USAGE TABLE (public schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS token_usage (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(255),
    session_id VARCHAR(255),
    user_id VARCHAR(255),
    model VARCHAR(100),
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    is_estimated BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    -- cost metering (migration 011)
    run_id VARCHAR(255),
    api_key_id INTEGER,
    api_key_name VARCHAR(255),
    tenant_id VARCHAR(255),
    request_model VARCHAR(100),
    provider VARCHAR(50),
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    model_requests INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    ttft_ms INTEGER,
    cost_usd NUMERIC(12, 6),
    unpriced_models TEXT[],
    prices_version VARCHAR(20),
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    error TEXT,
    breakdown JSONB
);

CREATE INDEX IF NOT EXISTS idx_token_usage_agent_id ON token_usage(agent_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_session_id ON token_usage(session_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_user_id ON token_usage(user_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_created_at ON token_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_agent_created ON token_usage(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_tenant_created ON token_usage(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_api_key_created ON token_usage(api_key_id, created_at);

-- ============================================================================
-- API KEYS TABLE (public schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS api_keys (
    id SERIAL PRIMARY KEY,
    key_hash VARCHAR(64) NOT NULL UNIQUE,  -- SHA-256 hash of the API key
    name VARCHAR(255) NOT NULL,             -- Human-readable name/description
    owner_id VARCHAR(255),                  -- Optional owner identifier
    scopes TEXT,                            -- JSON array of allowed scopes/permissions
    rate_limit INTEGER DEFAULT 1000,        -- Requests per hour (0 = unlimited)
    expires_at TIMESTAMP,                   -- Optional expiration (NULL = never expires)
    last_used_at TIMESTAMP,                 -- Last time the key was used
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_owner_id ON api_keys(owner_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_is_active ON api_keys(is_active);
CREATE INDEX IF NOT EXISTS idx_api_keys_expires_at ON api_keys(expires_at);

DROP TRIGGER IF EXISTS update_api_keys_updated_at ON api_keys;
CREATE TRIGGER update_api_keys_updated_at
    BEFORE UPDATE ON api_keys
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE api_keys IS 'Stores hashed API keys for authentication';
COMMENT ON COLUMN api_keys.key_hash IS 'SHA-256 hash of the API key (never store raw keys)';
COMMENT ON COLUMN api_keys.name IS 'Human-readable name for the API key';
COMMENT ON COLUMN api_keys.owner_id IS 'Optional identifier for key owner (user, service, etc.)';
COMMENT ON COLUMN api_keys.scopes IS 'JSON array of allowed scopes (e.g., ["read", "write", "admin"])';
COMMENT ON COLUMN api_keys.rate_limit IS 'Maximum requests per hour (0 = unlimited)';
COMMENT ON COLUMN api_keys.expires_at IS 'Key expiration timestamp (NULL = never expires)';
COMMENT ON COLUMN api_keys.last_used_at IS 'Timestamp of last successful authentication';
COMMENT ON COLUMN api_keys.is_active IS 'Soft delete flag';

-- ============================================================================
-- PROMPTS SCHEMA
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS prompts;

CREATE TABLE IF NOT EXISTS prompts.prompts (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    template TEXT NOT NULL,
    description TEXT,
    tags TEXT,   -- JSON string for tags
    tools TEXT,  -- JSON string for tools configuration
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_prompts_is_active ON prompts.prompts(is_active);
CREATE INDEX IF NOT EXISTS idx_prompts_created_at ON prompts.prompts(created_at);

DROP TRIGGER IF EXISTS trigger_prompts_updated_at ON prompts.prompts;
CREATE TRIGGER trigger_prompts_updated_at
    BEFORE UPDATE ON prompts.prompts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE prompts.prompts IS 'Stores prompt templates for agents';
COMMENT ON COLUMN prompts.prompts.id IS 'Unique identifier for the prompt';
COMMENT ON COLUMN prompts.prompts.name IS 'Human-readable name for the prompt';
COMMENT ON COLUMN prompts.prompts.template IS 'The actual prompt template text';
COMMENT ON COLUMN prompts.prompts.description IS 'Optional description of the prompt purpose';
COMMENT ON COLUMN prompts.prompts.tags IS 'JSON array of tags for categorization';
COMMENT ON COLUMN prompts.prompts.tools IS 'JSON array of tool configurations';
COMMENT ON COLUMN prompts.prompts.version IS 'Version number, incremented on updates';
COMMENT ON COLUMN prompts.prompts.is_active IS 'Soft delete flag';

-- ============================================================================
-- SKILLS TABLE (public schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS skills (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    description TEXT,
    instructions TEXT NOT NULL,
    category VARCHAR,
    "references" TEXT,  -- quoted: 'references' is a PostgreSQL reserved word
    scripts TEXT,
    allowed_tools TEXT,
    tags VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_skills_is_active ON skills(is_active);
CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);
CREATE INDEX IF NOT EXISTS idx_skills_created_at ON skills(created_at);

DROP TRIGGER IF EXISTS update_skills_updated_at ON skills;
CREATE TRIGGER update_skills_updated_at
    BEFORE UPDATE ON skills
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE skills IS 'Stores skill definitions and metadata for agent lazy-loading';
COMMENT ON COLUMN skills.id IS 'Unique skill identifier';
COMMENT ON COLUMN skills.instructions IS 'Main skill content (instructions for the agent)';
COMMENT ON COLUMN skills.category IS 'Skill category for filtering';
COMMENT ON COLUMN skills."references" IS 'JSON list of reference documents [{name, content}]';
COMMENT ON COLUMN skills.scripts IS 'JSON list of scripts [{name, content}]';
COMMENT ON COLUMN skills.allowed_tools IS 'JSON list of tool names this skill can use';
COMMENT ON COLUMN skills.is_active IS 'Soft delete flag';

-- ============================================================================
-- AI SCHEMA (Agno 2.1.2)
-- Agno automatically creates tables in the "ai" schema at runtime
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS ai;

-- Note: Tables in the ai schema (agent sessions, user memories, etc.)
-- are automatically created by the Agno PostgresDb class when initialized.
-- No manual table creation is needed for Agno-managed data.

-- ============================================================================
-- SUPERVISOR RUNS TABLE (public schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS supervisor_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id VARCHAR NOT NULL,
    session_id VARCHAR,
    user_id VARCHAR,
    user_message TEXT NOT NULL,
    supervisor_response JSONB,
    worker_agent_id VARCHAR,
    execution_engine VARCHAR,
    status VARCHAR NOT NULL DEFAULT 'pending',
    execution_output TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_supervisor_runs_team_id ON supervisor_runs(team_id);
CREATE INDEX IF NOT EXISTS idx_supervisor_runs_session_id ON supervisor_runs(session_id);
CREATE INDEX IF NOT EXISTS idx_supervisor_runs_user_id ON supervisor_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_supervisor_runs_status ON supervisor_runs(status);
CREATE INDEX IF NOT EXISTS idx_supervisor_runs_created_at ON supervisor_runs(created_at);

COMMENT ON TABLE supervisor_runs IS 'Audit log for supervisor classification and execution runs';

-- ============================================================================
-- EXECUTION JOBS TABLE (public schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS execution_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supervisor_run_id UUID,
    worker_config JSONB NOT NULL,
    prompt TEXT NOT NULL,
    execution_target JSONB,
    status VARCHAR NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'awaiting_approval', 'completed', 'failed', 'oom', 'failed_circuit_open')),
    result JSONB,
    container_id VARCHAR,
    target_host VARCHAR,
    retry_count INTEGER DEFAULT 0,
    memory_limit_mb INTEGER,
    last_failure_reason TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    timeout_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_execution_jobs_supervisor_run_id ON execution_jobs(supervisor_run_id);
CREATE INDEX IF NOT EXISTS idx_execution_jobs_status ON execution_jobs(status);
CREATE INDEX IF NOT EXISTS idx_execution_jobs_target_host ON execution_jobs(target_host);
CREATE INDEX IF NOT EXISTS idx_execution_jobs_created_at ON execution_jobs(created_at);

CREATE INDEX IF NOT EXISTS idx_execution_jobs_queue_poll
    ON execution_jobs(status, target_host, created_at)
    WHERE status = 'queued';

COMMENT ON TABLE execution_jobs IS 'Job queue for execution engine dispatch with OOM retry tracking';

-- ============================================================================
-- EXECUTION ENGINES TABLE (public schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS execution_engines (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    type VARCHAR NOT NULL CHECK (type IN ('code_agent', 'managed_agent', 'direct_ops', 'custom')),
    provider VARCHAR NOT NULL DEFAULT 'anthropic',
    handler_config JSONB,
    description TEXT,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_execution_engines_is_active ON execution_engines(is_active);
CREATE INDEX IF NOT EXISTS idx_execution_engines_type ON execution_engines(type);
CREATE INDEX IF NOT EXISTS idx_execution_engines_provider ON execution_engines(provider);

DROP TRIGGER IF EXISTS update_execution_engines_updated_at ON execution_engines;
CREATE TRIGGER update_execution_engines_updated_at
    BEFORE UPDATE ON execution_engines
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE execution_engines IS 'Registry of available execution engines';

-- Default engines
INSERT INTO execution_engines (id, name, type, provider, description, is_default)
VALUES
    ('claude_code', 'Claude Code', 'code_agent', 'anthropic', 'Claude Code CLI for repository-based work', TRUE),
    ('anthropic_managed', 'Anthropic Managed Agents', 'managed_agent', 'anthropic', 'Anthropic managed agents API', FALSE),
    ('openai_agents', 'OpenAI Agents', 'managed_agent', 'openai', 'OpenAI Agents API', FALSE),
    ('google_agents', 'Google Vertex AI Agents', 'managed_agent', 'google', 'Google Vertex AI agents', FALSE),
    ('direct_ops', 'Direct Operations Tools', 'direct_ops', 'internal', 'Runtime operations tools', FALSE)
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- EXECUTION TARGETS TABLE (public schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS execution_targets (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    type VARCHAR NOT NULL CHECK (type IN ('local', 'ssh', 'remote_service', 'managed_agents')),
    connection_config JSONB,
    capacity JSONB,
    worker_pool VARCHAR NOT NULL DEFAULT 'linux_worker_pool',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_execution_targets_is_active ON execution_targets(is_active);
CREATE INDEX IF NOT EXISTS idx_execution_targets_worker_pool ON execution_targets(worker_pool);
CREATE INDEX IF NOT EXISTS idx_execution_targets_type ON execution_targets(type);

DROP TRIGGER IF EXISTS update_execution_targets_updated_at ON execution_targets;
CREATE TRIGGER update_execution_targets_updated_at
    BEFORE UPDATE ON execution_targets
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE execution_targets IS 'Registry of available execution targets';

-- Default targets
INSERT INTO execution_targets (id, name, type, worker_pool, connection_config)
VALUES
    ('local', 'Local', 'local', 'linux_worker_pool', '{}'),
    ('managed', 'Managed Agents (Cloud)', 'managed_agents', 'linux_worker_pool', '{}')
ON CONFLICT (id) DO NOTHING;
