-- MCP server registry (#91): servers defined once and referenced by id from agent configs.
-- Header values are encrypted with SECRET_TOKEN_ENC_KEY; header_names stays readable.
-- tenant_id is NULL (shared) for now; per-tenant scoping is v0.9.0 (#226).
CREATE TABLE IF NOT EXISTS mcp_servers (
    id VARCHAR(63) PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    url TEXT NOT NULL,
    transport VARCHAR(32) NOT NULL DEFAULT 'streamable-http' CHECK (transport IN ('http', 'streamable-http')),
    auth_type VARCHAR(32) NOT NULL DEFAULT 'none' CHECK (auth_type IN ('none', 'static_headers')),
    encrypted_headers TEXT,
    header_names TEXT,
    tenant_id VARCHAR(255),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_is_active ON mcp_servers(is_active);
CREATE INDEX IF NOT EXISTS idx_mcp_servers_tenant_id ON mcp_servers(tenant_id);

DROP TRIGGER IF EXISTS update_mcp_servers_updated_at ON mcp_servers;
CREATE TRIGGER update_mcp_servers_updated_at
    BEFORE UPDATE ON mcp_servers
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE mcp_servers IS 'Registered MCP servers that agents reference by id';
COMMENT ON COLUMN mcp_servers.id IS 'Slug; also used as the tool-name prefix';
COMMENT ON COLUMN mcp_servers.encrypted_headers IS 'Fernet-encrypted JSON of header name to value (auth_type static_headers)';
COMMENT ON COLUMN mcp_servers.header_names IS 'JSON list of the header names, readable without the key';
COMMENT ON COLUMN mcp_servers.tenant_id IS 'NULL = shared by every tenant';
