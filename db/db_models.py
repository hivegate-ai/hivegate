import datetime
import uuid

from sqlalchemy import ARRAY, Boolean, Column, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


class PromptDB(Base):
    """Database model for storing prompts/templates."""

    __tablename__ = "prompts"
    __table_args__ = {"schema": "prompts"}

    id = Column(String(255), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    template = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)  # JSON string for tags
    tools = Column(Text, nullable=True)  # JSON string for tools configuration
    version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class AgentInfoDB(Base):
    """Database model for storing agent information for consistency across services."""

    __tablename__ = "agent_info"

    id = Column(String, primary_key=True, index=True)  # agent_id as primary key
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    version = Column(String, default="2.0", nullable=False)
    prompt_service_id = Column(String, nullable=False, index=True)  # Reference to prompt service
    tags = Column(String, nullable=True)  # JSON string for tags
    config = Column(Text, nullable=True)  # JSON string for agent config (memory, history, reasoning)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class TeamInfoDB(Base):
    """Database model for storing team information for consistency across services."""

    __tablename__ = "team_info"

    id = Column(String, primary_key=True, index=True)  # team_id as primary key
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    version = Column(String, default="2.0", nullable=False)
    mode = Column(String, default="coordinate", nullable=False)  # coordinate, supervisor
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class TeamAgentDB(Base):
    """Junction table for team-agent relationships (many-to-many)."""

    __tablename__ = "team_agent"

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(String, nullable=False, index=True)  # FK to team_info.id
    agent_id = Column(String, nullable=False, index=True)  # FK to agent_info.id
    role = Column(String, nullable=True)  # Optional role description for agent in team
    order_index = Column(Integer, nullable=True)  # Optional ordering of agents in team
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    # Ensure unique team-agent combinations
    __table_args__ = (
        # UniqueConstraint('team_id', 'agent_id', name='uq_team_agent'),
    )


class TokenUsage(Base):
    """Model for storing token usage data."""

    __tablename__ = "token_usage"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String, index=True)
    session_id = Column(String, index=True)
    user_id = Column(String, nullable=True)
    model = Column(String)
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    total_tokens = Column(Integer)
    is_estimated = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    # Cost metering (db/migrations/011_token_usage_cost.sql; api/services/usage.py)
    run_id = Column(String, nullable=True)
    api_key_id = Column(Integer, nullable=True)
    api_key_name = Column(String, nullable=True)
    tenant_id = Column(String, nullable=True)
    request_model = Column(String, nullable=True)
    provider = Column(String, nullable=True)
    cache_read_tokens = Column(Integer, nullable=False, default=0)
    cache_write_tokens = Column(Integer, nullable=False, default=0)
    reasoning_tokens = Column(Integer, nullable=False, default=0)
    model_requests = Column(Integer, nullable=False, default=0)
    tool_calls = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=True)
    ttft_ms = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(12, 6), nullable=True)
    unpriced_models = Column(ARRAY(String), nullable=True)  # type: ignore[var-annotated]
    prices_version = Column(String, nullable=True)
    status = Column(String, nullable=False, default="completed")
    error = Column(Text, nullable=True)
    breakdown = Column(JSONB, nullable=True)


class KnowledgeEntryDB(Base):
    """Database model for storing knowledge entries (tenant and collection level)."""

    __tablename__ = "knowledge_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(String(255), nullable=False, index=True)
    collection_id = Column(String(255), nullable=True, index=True)  # NULL for tenant-level
    file_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    original_filename = Column(String(500), nullable=False)
    file_type = Column(String(50), nullable=False)  # 'company' or 'project'
    content_type = Column(String(200), nullable=True)
    gcs_path = Column(String(1000), nullable=True)
    status = Column(String(50), nullable=False, default="pending", index=True)
    knowledge_status = Column(String(50), nullable=False, default="indexing", index=True)
    entry_metadata = Column("metadata", JSONB, nullable=True)  # type: ignore[assignment]  # JSONB for PostgreSQL
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)

    # Unique constraint on tenant_id + file_id
    __table_args__ = (UniqueConstraint("tenant_id", "file_id", name="uq_knowledge_tenant_file"),)


class UserTokenDB(Base):
    """Database model for storing encrypted user tokens for integrations."""

    __tablename__ = "user_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), nullable=False, index=True)
    integration_key = Column(String(100), nullable=False, index=True)  # 'google', 'openai', 'slack', etc.
    provider = Column(String(50), nullable=False)  # Provider name for grouping
    token_type = Column(String(20), nullable=False)  # 'oauth2', 'api_key', 'jwt'
    encrypted_token_data = Column(Text, nullable=False)  # Encrypted JSON blob
    scopes = Column(ARRAY(String), nullable=True)  # type: ignore[var-annotated]  # Array of permission scopes
    expires_at = Column(DateTime, nullable=True, index=True)  # Token expiry (nullable for non-expiring tokens)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    # Composite unique constraint on user_id + integration_key
    __table_args__ = (UniqueConstraint("user_id", "integration_key", name="uq_user_token_integration"),)


class SkillDB(Base):
    """Database model for storing skills."""

    __tablename__ = "skills"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    instructions = Column(Text, nullable=False)
    category = Column(String, nullable=True)
    references = Column(Text, nullable=True)  # JSON list of {name, content}
    scripts = Column(Text, nullable=True)  # JSON list of {name, content}
    allowed_tools = Column(Text, nullable=True)  # JSON list of tool names
    tags = Column(String, nullable=True)  # JSON string for tags
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class ApiKeyDB(Base):
    """Database model for storing API keys for authentication."""

    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    key_hash = Column(String(64), nullable=False, unique=True, index=True)  # SHA-256 hash
    name = Column(String(255), nullable=False)  # Human-readable name
    owner_id = Column(String(255), nullable=True, index=True)  # Optional owner identifier
    scopes = Column(Text, nullable=True)  # JSON array of allowed scopes
    rate_limit = Column(Integer, default=1000)  # Requests per hour (0 = unlimited)
    expires_at = Column(DateTime, nullable=True, index=True)  # Optional expiration
    last_used_at = Column(DateTime, nullable=True)  # Last usage timestamp
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, index=True)


class SupervisorRunDB(Base):
    """Audit log for supervisor classification and execution runs."""

    __tablename__ = "supervisor_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    user_message = Column(Text, nullable=False)
    supervisor_response = Column(JSONB, nullable=True)  # type: ignore[assignment]
    worker_agent_id = Column(String, nullable=True)
    execution_engine = Column(String, nullable=True)
    status = Column(String, default="pending", nullable=False, index=True)
    execution_output = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)


class ExecutionJobDB(Base):
    """Job queue for execution engine dispatch."""

    __tablename__ = "execution_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supervisor_run_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    worker_config = Column(JSONB, nullable=False)  # type: ignore[assignment]
    prompt = Column(Text, nullable=False)
    execution_target = Column(JSONB, nullable=True)  # type: ignore[assignment]
    status = Column(String, default="queued", nullable=False, index=True)
    result = Column(JSONB, nullable=True)  # type: ignore[assignment]
    container_id = Column(String, nullable=True)
    target_host = Column(String, nullable=True)
    retry_count = Column(Integer, default=0)
    memory_limit_mb = Column(Integer, nullable=True)
    last_failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    timeout_at = Column(DateTime, nullable=True)


class ExecutionEngineDB(Base):
    """Registry of available execution engines."""

    __tablename__ = "execution_engines"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # code_agent, managed_agent, direct_ops, custom
    provider = Column(String, default="anthropic", nullable=False)
    handler_config = Column(JSONB, nullable=True)  # type: ignore[assignment]
    description = Column(Text, nullable=True)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, index=True)


class ExecutionTargetDB(Base):
    """Registry of available execution targets (VMs, K8s clusters, etc.)."""

    __tablename__ = "execution_targets"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # local, ssh, remote_service, managed_agents
    connection_config = Column(JSONB, nullable=True)  # type: ignore[assignment]
    capacity = Column(JSONB, nullable=True)  # type: ignore[assignment]
    worker_pool = Column(String, default="linux_worker_pool", nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
