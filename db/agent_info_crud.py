"""CRUD operations for AgentInfo model."""

import datetime
import json
import logging
from typing import List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.services.logging import log_safe
from db.db_models import AgentInfoDB
from supervisor.models import WorkerConfig


class AgentConfig(BaseModel):
    """Agent runtime configuration stored in database."""

    # Memory settings
    enable_memory: bool = True  # Controls MemoryManager creation

    # History settings
    enable_history: bool = True  # Controls read_chat_history + add_history_to_context
    num_history_runs: int = 3  # Number of history runs to include

    # Reasoning settings (Agno native reasoning support)
    enable_reasoning: bool = False  # Maps to Agent(reasoning=True)
    reasoning_min_steps: int = 1  # Minimum reasoning steps
    reasoning_max_steps: int = 10  # Maximum reasoning steps

    # Supervisor worker settings (optional — only for agents used as supervisor workers)
    worker_config: Optional[WorkerConfig] = None

    # Registered MCP servers (/v2/mcp-servers) the agent's chat connects to, by id (#91).
    # Chat only: supervisor and container workers keep using worker_config.mcp_servers.
    mcp_server_ids: List[str] = Field(default_factory=list)


def create_agent_info(
    db: Session,
    agent_id: str,
    name: str,
    prompt_service_id: str,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None,
    version: str = "2.0",
    config: Optional[AgentConfig] = None,
) -> AgentInfoDB:
    """
    Create a new AgentInfo record or reactivate an existing inactive one.

    Args:
        db: Database session
        agent_id: Unique agent identifier
        name: Display name for the agent
        prompt_service_id: Reference to prompt service
        description: Optional description
        tags: Optional list of tags
        version: API version (defaults to "2.0")
        config: Optional agent configuration (memory, history, reasoning settings)

    Returns:
        Created or reactivated AgentInfo instance
    """
    try:
        tags_json = json.dumps(tags) if tags else None
        config_json = config.model_dump_json() if config else None

        # Check if there's an inactive agent with the same ID
        existing_agent = db.query(AgentInfoDB).filter(AgentInfoDB.id == agent_id, ~AgentInfoDB.is_active).first()

        if existing_agent:
            # Reactivate and update the existing agent
            existing_agent.name = name  # type: ignore[assignment]
            existing_agent.description = description  # type: ignore[assignment]
            existing_agent.version = version  # type: ignore[assignment]
            existing_agent.prompt_service_id = prompt_service_id  # type: ignore[assignment]
            existing_agent.tags = tags_json  # type: ignore[assignment]
            existing_agent.config = config_json  # type: ignore[assignment]
            existing_agent.is_active = True  # type: ignore[assignment]
            existing_agent.updated_at = datetime.datetime.utcnow()  # type: ignore[assignment]

            db.commit()
            db.refresh(existing_agent)

            logging.info(f"Reactivated existing AgentInfo for {agent_id} with prompt_service_id: {prompt_service_id}")
            return existing_agent
        else:
            # Create a new agent
            agent_info = AgentInfoDB(
                id=agent_id,
                name=name,
                description=description,
                version=version,
                prompt_service_id=prompt_service_id,
                tags=tags_json,
                config=config_json,
                is_active=True,
            )

            db.add(agent_info)
            db.commit()
            db.refresh(agent_info)

            logging.info(f"Created new AgentInfo for {agent_id} with prompt_service_id: {prompt_service_id}")
            return agent_info
    except Exception as e:
        db.rollback()
        logging.error(f"Error creating agent info: {e}")
        raise


def get_agent_info(db: Session, agent_id: str) -> Optional[AgentInfoDB]:
    """
    Get AgentInfo by agent_id.

    Args:
        db: Database session
        agent_id: Agent identifier

    Returns:
        AgentInfoDB instance or None if not found
    """
    return db.query(AgentInfoDB).filter(AgentInfoDB.id == agent_id, AgentInfoDB.is_active).first()


def get_all_agent_info(db: Session, include_inactive: bool = False) -> List[AgentInfoDB]:
    """
    Get all AgentInfo records.

    Args:
        db: Database session
        include_inactive: Whether to include inactive agents

    Returns:
        List of AgentInfoDB instances
    """
    query = db.query(AgentInfoDB)
    if not include_inactive:
        query = query.filter(AgentInfoDB.is_active)

    return query.order_by(AgentInfoDB.name).all()


def get_agents_by_ids(db: Session, agent_ids: List[str]) -> List[AgentInfoDB]:
    """
    Get active AgentInfo records by a list of agent IDs.

    Args:
        db: Database session
        agent_ids: List of agent identifiers to filter by

    Returns:
        List of active AgentInfoDB instances matching the provided IDs
    """
    if not agent_ids:
        return []

    return (
        db.query(AgentInfoDB)
        .filter(AgentInfoDB.id.in_(agent_ids), AgentInfoDB.is_active)
        .order_by(AgentInfoDB.name)
        .all()
    )


def update_agent_info(
    db: Session,
    agent_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None,
    prompt_service_id: Optional[str] = None,
    config: Optional[AgentConfig] = None,
) -> Optional[AgentInfoDB]:
    """
    Update an existing AgentInfo record.

    Args:
        db: Database session
        agent_id: Agent identifier
        name: New name (optional)
        description: New description (optional)
        tags: New tags (optional)
        prompt_service_id: New prompt service ID (optional)
        config: New agent configuration (optional)

    Returns:
        Updated AgentInfo instance or None if not found
    """
    agent_info = get_agent_info(db, agent_id)
    if not agent_info:
        return None

    if name is not None:
        agent_info.name = name  # type: ignore[assignment]
    if description is not None:
        agent_info.description = description  # type: ignore[assignment]
    if tags is not None:
        agent_info.tags = json.dumps(tags)  # type: ignore[assignment]
    if prompt_service_id is not None:
        agent_info.prompt_service_id = prompt_service_id  # type: ignore[assignment]
    if config is not None:
        agent_info.config = config.model_dump_json()  # type: ignore[assignment]

    db.commit()
    db.refresh(agent_info)

    logging.info(f"Updated AgentInfo for {log_safe(agent_id)}")
    return agent_info


def soft_delete_agent_info(db: Session, agent_id: str) -> bool:
    """
    Soft delete an AgentInfo record by setting is_active to False.

    Args:
        db: Database session
        agent_id: Agent identifier

    Returns:
        True if deleted, False if not found
    """
    agent_info = get_agent_info(db, agent_id)
    if not agent_info:
        return False

    agent_info.is_active = False  # type: ignore[assignment]
    db.commit()

    logging.info(f"Soft deleted AgentInfo for {agent_id}")
    return True


def get_agent_tags(agent_info: AgentInfoDB) -> List[str]:
    """
    Parse tags from AgentInfoDB record.

    Args:
        agent_info: AgentInfoDB instance

    Returns:
        List of tags or empty list if no tags
    """
    if not agent_info.tags:
        return []

    try:
        return json.loads(agent_info.tags)  # type: ignore[arg-type]
    except json.JSONDecodeError:
        logging.warning(f"Failed to parse tags for agent {agent_info.id}: {agent_info.tags}")
        return []


def parse_agent_config(agent_info: AgentInfoDB) -> AgentConfig:
    """The stored config, or defaults when none is stored. Raises when it is stored but does
    not validate; use this rather than get_agent_config before writing the config back, or
    the defaults overwrite it."""
    if not agent_info.config:
        return AgentConfig()
    return AgentConfig.model_validate_json(agent_info.config)  # type: ignore[arg-type]


def get_agent_config(agent_info: AgentInfoDB) -> AgentConfig:
    """
    Parse config from AgentInfoDB record, returning defaults if not set.

    Args:
        agent_info: AgentInfoDB instance

    Returns:
        AgentConfig instance (defaults if no config stored)
    """
    try:
        return parse_agent_config(agent_info)
    except Exception:
        logging.warning(f"Failed to parse config for agent {agent_info.id}, using defaults")
        return AgentConfig()


def _referenced_mcp_server_ids(agent_info: AgentInfoDB) -> List[str]:
    """mcp_server_ids read from the raw config JSON, so a config that fails AgentConfig
    validation for an unrelated field still counts as referencing its servers."""
    try:
        data = json.loads(agent_info.config or "{}")  # type: ignore[arg-type]
    except (json.JSONDecodeError, TypeError):
        return []
    ids = data.get("mcp_server_ids") if isinstance(data, dict) else None
    return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []


def get_agents_using_mcp_server(db: Session, server_id: str) -> List[str]:
    """Ids of active agents whose config references this registered MCP server."""
    return sorted(str(agent.id) for agent in get_all_agent_info(db) if server_id in _referenced_mcp_server_ids(agent))


def agent_info_exists(db: Session, agent_id: str) -> bool:
    """
    Check if an active AgentInfoDB record exists.

    Args:
        db: Database session
        agent_id: Agent identifier

    Returns:
        True if exists and active, False otherwise
    """
    return db.query(AgentInfoDB).filter(AgentInfoDB.id == agent_id, AgentInfoDB.is_active).first() is not None


def delete_agent_info(db: Session, agent_id: str) -> bool:
    """
    Hard delete an AgentInfo record from the database.

    Args:
        db: Database session
        agent_id: Agent identifier

    Returns:
        True if deleted, False if not found
    """
    try:
        agent_info = db.query(AgentInfoDB).filter(AgentInfoDB.id == agent_id).first()

        if not agent_info:
            logging.warning(f"Agent info not found for deletion: {agent_id}")
            return False

        db.delete(agent_info)
        db.commit()

        logging.info(f"Hard deleted AgentInfo for {agent_id}")
        return True
    except Exception as e:
        db.rollback()
        logging.error(f"Error deleting agent info: {e}")
        raise
