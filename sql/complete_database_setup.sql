-- ====================================
-- COMPLETE DATABASE SETUP FOR AGENT API
-- ====================================

-- This file contains all SQL queries for setting up the Agent API database
-- including agent_info, team_info, team_agent, token_usage, and knowledge_entries tables

-- ====================================
-- 1. AGENT INFO TABLE
-- ====================================

-- Create agent_info table for persistent agent metadata
CREATE TABLE IF NOT EXISTS agent_info (
    id VARCHAR PRIMARY KEY,  -- agent_id as primary key
    name VARCHAR NOT NULL,
    description TEXT,
    version VARCHAR NOT NULL DEFAULT '2.0',
    prompt_service_id VARCHAR NOT NULL,  -- Reference to prompt service
    tags VARCHAR,  -- JSON string for tags
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Create indexes for agent_info
CREATE INDEX IF NOT EXISTS idx_agent_info_is_active ON agent_info(is_active);
CREATE INDEX IF NOT EXISTS idx_agent_info_created_at ON agent_info(created_at);
CREATE INDEX IF NOT EXISTS idx_agent_info_prompt_service_id ON agent_info(prompt_service_id);

-- ====================================
-- 2. TEAM INFO TABLE
-- ====================================

-- Create team_info table for persistent team metadata
CREATE TABLE IF NOT EXISTS team_info (
    id VARCHAR PRIMARY KEY,  -- team_id as primary key
    name VARCHAR NOT NULL,
    description TEXT,
    version VARCHAR NOT NULL DEFAULT '2.0',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Create indexes for team_info
CREATE INDEX IF NOT EXISTS idx_team_info_is_active ON team_info(is_active);
CREATE INDEX IF NOT EXISTS idx_team_info_created_at ON team_info(created_at);

-- ====================================
-- 3. TEAM-AGENT JUNCTION TABLE
-- ====================================

-- Create team_agent junction table for team-agent relationships
CREATE TABLE IF NOT EXISTS team_agent (
    id SERIAL PRIMARY KEY,
    team_id VARCHAR NOT NULL,  -- FK to team_info.id
    agent_id VARCHAR NOT NULL,  -- FK to agent_info.id
    role VARCHAR,  -- Optional role description for agent in team
    order_index INTEGER,  -- Optional ordering of agents in team
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Create indexes for team_agent
CREATE INDEX IF NOT EXISTS idx_team_agent_team_id ON team_agent(team_id);
CREATE INDEX IF NOT EXISTS idx_team_agent_agent_id ON team_agent(agent_id);
CREATE INDEX IF NOT EXISTS idx_team_agent_is_active ON team_agent(is_active);
CREATE INDEX IF NOT EXISTS idx_team_agent_order ON team_agent(team_id, order_index);

-- Create unique constraint to prevent duplicate team-agent relationships
CREATE UNIQUE INDEX IF NOT EXISTS uq_team_agent_active 
    ON team_agent(team_id, agent_id) 
    WHERE is_active = TRUE;

-- ====================================
-- 4. TOKEN USAGE TABLE
-- ====================================

-- Create token_usage table (if not exists)
CREATE TABLE IF NOT EXISTS token_usage (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR,
    session_id VARCHAR,
    user_id VARCHAR,
    model VARCHAR,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    is_estimated BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
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

-- Create indexes for token_usage
CREATE INDEX IF NOT EXISTS idx_token_usage_agent_id ON token_usage(agent_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_session_id ON token_usage(session_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_user_id ON token_usage(user_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_created_at ON token_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_agent_created ON token_usage(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_tenant_created ON token_usage(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_api_key_created ON token_usage(api_key_id, created_at);

-- ====================================
-- 5. TRIGGERS FOR AUTOMATIC TIMESTAMPS
-- ====================================

-- Create trigger function to automatically update updated_at column
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create triggers for agent_info
CREATE TRIGGER IF NOT EXISTS update_agent_info_updated_at 
    BEFORE UPDATE ON agent_info 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();

-- Create triggers for team_info
CREATE TRIGGER IF NOT EXISTS update_team_info_updated_at 
    BEFORE UPDATE ON team_info 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();

-- ====================================
-- 6. KNOWLEDGE ENTRIES TABLE
-- ====================================

-- Create knowledge_entries table for organization and project knowledge management
CREATE TABLE IF NOT EXISTS knowledge_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id VARCHAR(255) NOT NULL,
    project_id VARCHAR(255),  -- NULL for organization-level knowledge
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
    
    -- Unique constraint to prevent duplicate file_id per org
    CONSTRAINT uq_knowledge_org_file UNIQUE(org_id, file_id)
);

-- Create indexes for knowledge_entries
CREATE INDEX IF NOT EXISTS idx_knowledge_org_id ON knowledge_entries(org_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_project_id ON knowledge_entries(project_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_file_id ON knowledge_entries(file_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge_entries(status);
CREATE INDEX IF NOT EXISTS idx_knowledge_knowledge_status ON knowledge_entries(knowledge_status);
CREATE INDEX IF NOT EXISTS idx_knowledge_created_at ON knowledge_entries(created_at);

-- Create trigger for knowledge_entries
CREATE TRIGGER IF NOT EXISTS update_knowledge_entries_updated_at 
    BEFORE UPDATE ON knowledge_entries 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();

-- ====================================
-- 7. FOREIGN KEY CONSTRAINTS (Optional)
-- ====================================

-- Note: These are commented out as the current implementation uses
-- string references without formal FK constraints for flexibility

-- Add foreign key constraints (uncomment if needed)
-- ALTER TABLE team_agent 
--     ADD CONSTRAINT fk_team_agent_team_id 
--     FOREIGN KEY (team_id) REFERENCES team_info(id) ON DELETE CASCADE;

-- ALTER TABLE team_agent 
--     ADD CONSTRAINT fk_team_agent_agent_id 
--     FOREIGN KEY (agent_id) REFERENCES agent_info(id) ON DELETE CASCADE;