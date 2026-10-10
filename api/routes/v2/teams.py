import asyncio
import copy
import json
import logging
from functools import lru_cache
from threading import Lock
from typing import Any, AsyncGenerator, Dict, List, Optional

from agno.db.postgres import PostgresDb
from agno.memory import MemoryManager
from agno.models.google import Gemini

# agno.models.metrics.Metrics was only a backward-compat alias of RunMetrics, and
# agno 3 removed it. RunMetrics lives in agno.metrics from 2.6 through 3.x.
from agno.team import Team, TeamMode
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents import DEFAULT_MODEL, Model
from agents.hitl import requirements_for
from agents.model_resolver import resolve_model_id
from agents.v2_selector import get_agent
from api.routes.v2.agents import TenantProfile, UserProfile, _log_safe
from api.services import usage as usage_service
from api.services.access_token import fetch_access_token
from api.services.auth import get_api_key
from api.services.knowledge_service import get_knowledge_service
from api.settings import api_settings
from db.agent_info_crud import get_agent_info
from db.db_models import ApiKeyDB
from db.session import db_engine

# Database models imported but not directly used - accessed via CRUD functions
from db.session import get_db
from db.team_info_crud import (
    add_agent_to_team,
    create_team_info,
    delete_team_info,
    get_all_team_info,
    get_team_agents,
    get_team_info,
    remove_agent_from_team,
    soft_delete_team_info,
    team_info_exists,
    update_team_agent_role,
)
from db.url import get_db_url
from supervisor.approval import ApprovalNotification, register_pending_approval

# Team run cache for confirmation flow
_team_run_cache: Dict[str, Any] = {}
_team_run_cache_lock = Lock()

######################################################
## Pydantic Models for Team API
######################################################


class TeamAgent(BaseModel):
    """Team agent information model"""

    agent_id: str
    role: Optional[str] = None
    order_index: Optional[int] = None


class TeamInfo(BaseModel):
    """Team information model for v2 API"""

    id: str
    name: str
    description: Optional[str] = None
    version: str = "2.0"
    mode: str = "coordinate"
    agents: List[TeamAgent] = []
    updated_at: str
    created_at: str


class TeamAgentAssignment(BaseModel):
    """Model for assigning an agent to a team with role and order"""

    agent_id: str
    role: Optional[str] = None
    order_index: Optional[int] = None


class CreateTeamRequest(BaseModel):
    """Request model for creating a new team"""

    id: str
    name: str
    description: Optional[str] = None
    mode: str = "coordinate"  # coordinate or supervisor
    agents: List[TeamAgentAssignment] = []  # List of agent assignments with roles


class CreateTeamResponse(BaseModel):
    """Response model for team creation"""

    id: str
    name: str
    description: Optional[str] = None
    version: str = "2.0"
    message: str


class TeamRunRequest(BaseModel):
    """Request model for team run"""

    message: str
    stream: bool = True
    stream_verbosity: str = "events"  # full, events, result
    model: Model = DEFAULT_MODEL
    user_id: Optional[str] = None
    session_id: Optional[str] = None

    # Profile objects from ChatRequest
    user_profile: Optional[UserProfile] = None
    tenant_profile: Optional[TenantProfile] = None

    # User context fields (at root level)
    timezone: Optional[str] = None
    locale: Optional[str] = None

    # Optional fields from ChatRequest
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    images: Optional[List[Dict[str, Any]]] = None


class TeamRunResponse(BaseModel):
    """Response model for team run"""

    content: str
    team_id: str
    session_id: Optional[str] = None
    model: str
    token_usage: Optional[dict] = None
    status: Optional[str] = None
    run_id: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None


class TeamCommitRequest(BaseModel):
    """Request model for resuming paused team run with confirmed/edited tools"""

    run_id: str
    stream: bool = True
    model: Model = DEFAULT_MODEL
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    updated_tools: List[Dict[str, Any]]


class TeamSession(BaseModel):
    """Team session information model"""

    id: str
    team_id: str
    created_at: str
    updated_at: str
    message_count: int = 0


class TeamMemory(BaseModel):
    """Team memory information model"""

    id: str
    team_id: str
    session_id: Optional[str] = None
    content: str
    created_at: str


class AddMemberRequest(BaseModel):
    """Request model for adding a member to a team"""

    agent_id: str
    role: Optional[str] = None
    order_index: Optional[int] = None


class UpdateMemberRequest(BaseModel):
    """Request model for updating a team member's role/order"""

    role: Optional[str] = None
    order_index: Optional[int] = None


class MemberResponse(BaseModel):
    """Response model for team member operations"""

    agent_id: str
    role: Optional[str] = None
    order_index: Optional[int] = None
    created_at: str


class AddMemberResponse(BaseModel):
    """Response model for adding a member to a team"""

    message: str
    member: MemberResponse


def _register_paused_approvals(
    run_id: str,
    team_id: str,
    tools: List[Dict[str, Any]],
) -> None:
    """Register each pending tool as an approval notification when a team run is paused."""
    for tool_data in tools:
        tool_name = tool_data.get("tool_name", "unknown")
        if not tool_data.get("requires_confirmation", False):
            continue
        notification = ApprovalNotification(
            job_id=f"{run_id}:{tool_data.get('tool_call_id', 'unknown')}",
            run_id=run_id,
            team_id=team_id,
            tool_name=tool_name,
            tool_args=tool_data.get("tool_args") or {},
            reason="Tool requires human confirmation before execution",
        )
        register_pending_approval(notification)
        logging.info(f"Registered approval for tool {tool_name} in run {run_id}")


######################################################
## V2 Routes for the Team Interface
######################################################

v2_teams_router = APIRouter(prefix="/teams", tags=["V2 Teams"])


######################################################
## Helper Functions
######################################################


def _event_to_dict(ev: Any) -> Dict[str, Any]:
    """Convert event to dictionary for SSE streaming."""
    if hasattr(ev, "to_dict"):
        return copy.deepcopy(ev.to_dict())
    return {"error": "Could not serialize event"}


def _format_sse_event(event_type: str, data: Dict[str, Any]) -> str:
    """Format data as Server-Sent Event."""
    json_data = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {json_data}\n\n"


def _parse_result(result: Any) -> Any:
    """
    Parse tool result, converting string representations to proper dicts.

    Args:
        result: The raw result from the tool (could be string, dict, or other)

    Returns:
        Parsed result as a dict if possible, otherwise the original value
    """
    if result is None:
        return None

    # If it's already a dict, return as-is
    if isinstance(result, dict):
        return result

    # If it's a string, try to parse it as JSON
    if isinstance(result, str):
        # Try JSON parsing first
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            pass

        # Try ast.literal_eval for Python dict strings like "{'key': 'value'}"
        try:
            import ast

            return ast.literal_eval(result)
        except (ValueError, SyntaxError):
            pass

    # Return original if we can't parse it
    return result


def _process_tools_in_event(event_data: Dict[str, Any]) -> None:
    """
    Process tools in event data, converting them to dict format with parsed results.

    Modifies event_data in place, converting "tools" or "tool" to standardized "tools" array.

    Args:
        event_data: Event data dictionary (deep copied from chunk)
    """
    # Convert tools to dict format and add to event data
    if "tools" in event_data and event_data["tools"]:
        # Tools already in event_data, process them
        tools_data = event_data["tools"] if isinstance(event_data["tools"], list) else [event_data["tools"]]
        tools_dict = []
        for tool_data in tools_data:
            if isinstance(tool_data, dict):
                # Already a dict, parse result if present
                if "result" in tool_data:
                    tool_data["result"] = _parse_result(tool_data["result"])
                tools_dict.append(tool_data)
            else:
                # Convert tool object to dict
                tool_dict = {
                    "tool_call_id": getattr(tool_data, "tool_call_id", None),
                    "tool_name": getattr(tool_data, "tool_name", None),
                    "requires_confirmation": getattr(tool_data, "requires_confirmation", False),
                    "tool_args": getattr(tool_data, "tool_args", None),
                    "result": _parse_result(getattr(tool_data, "result", None)),
                }
                tools_dict.append(tool_dict)
        event_data["tools"] = tools_dict
    elif "tool" in event_data and event_data["tool"]:
        # Single tool in event_data, convert to tools array
        tool_data = event_data.pop("tool")  # Remove "tool" key
        if isinstance(tool_data, dict):
            # Already a dict, parse result if present
            if "result" in tool_data:
                tool_data["result"] = _parse_result(tool_data["result"])
            event_data["tools"] = [tool_data]
        else:
            # Convert tool object to dict
            tool_dict = {
                "tool_call_id": getattr(tool_data, "tool_call_id", None),
                "tool_name": getattr(tool_data, "tool_name", None),
                "requires_confirmation": getattr(tool_data, "requires_confirmation", False),
                "tool_args": getattr(tool_data, "tool_args", None),
                "result": _parse_result(getattr(tool_data, "result", None)),
            }
            event_data["tools"] = [tool_dict]


# Every team run is metered by api/services/usage.py, like agent runs. Module-level so
# tests can patch it.
record_usage = usage_service.record


######################################################
## Team Storage and Creation Functions
######################################################


@lru_cache(maxsize=None)
def get_team_db(session_table: Optional[str] = None, memory_table: Optional[str] = None) -> PostgresDb:
    """Team storage on the SHARED engine (db.session), cached per table pair.

    A PostgresDb per call opens its own connection pool; Supabase's session pooler
    allows 15 clients in total, so that exhausted it (see db/session.py).
    """
    return PostgresDb(db_engine=db_engine, session_table=session_table, memory_table=memory_table)


def get_team_storage(team_id: str, db_url: str) -> PostgresDb:
    """
    Get PostgresDb instance for a team.

    Args:
        team_id: The team ID
        db_url: Database URL

    Returns:
        PostgresDb: Storage instance for the team
    """
    return get_team_db(session_table=f"t_{team_id}_s")


def get_team_memory_db(team_id: str, db_url: str) -> PostgresDb:
    """
    Get PostgresDb instance for team memory operations.

    Args:
        team_id: The team ID
        db_url: Database URL

    Returns:
        PostgresDb: Memory database instance for the team
    """
    return get_team_db(memory_table=f"t_{team_id}_m")


def create_team(
    team_name: str,
    team_id: str,
    user_id: str,
    session_id: str,
    members: List,
    model_id: str,
    db_url: str,
    debug_mode: bool = False,
) -> Team:
    """
    Create an Agno team with advanced configuration.

    Args:
        team_name: The name of the team
        team_id: Unique identifier for the team
        user_id: User identifier
        session_id: Session identifier
        members: List of Agent objects
        model_id: Model identifier (e.g., "gemini-2.5-pro")
        db_url: Database URL for storage
        debug_mode: Enable debug mode

    Returns:
        Team: Configured Agno team instance
    """
    # Create PostgresDb instance for team storage and memory
    db_instance = get_team_db(session_table=f"t_{team_id}_s", memory_table=f"t_{team_id}_m")

    # Create MemoryManager
    memory_manager = MemoryManager(
        model=Gemini(id=resolve_model_id(model_id), api_key=api_settings.gemini_api_key),
        db=db_instance,
        delete_memories=True,
        clear_memories=True,
    )

    # Team pre-hook: Execute all member agents' pre-hooks
    def team_pre_hook(**kwargs):
        """
        Execute pre-hooks for all team members.

        Args:
            **kwargs: Arguments from Agno Team (includes 'team', 'message', etc.)
        """
        # Extract team instance and message from kwargs
        team_instance = kwargs.get("team")

        if not team_instance or not hasattr(team_instance, "members"):
            return

        if not team_instance.members:
            return

        for member in team_instance.members:
            # Check if member has pre_hooks defined
            if hasattr(member, "pre_hooks") and member.pre_hooks:
                # Execute each pre-hook for the member
                for pre_hook in member.pre_hooks:
                    try:
                        # Call the pre-hook with the member agent and message
                        # Pass the same kwargs structure that Agno expects
                        pre_hook(member, None)
                    except Exception as e:
                        logging.error(f"Error executing pre-hook for member {member.name}: {e}")

    return Team(  # type: ignore[call-arg]
        name=team_name,
        id=team_id,
        user_id=user_id,
        session_id=session_id,
        members=members,
        mode=TeamMode.coordinate,
        model=Gemini(id=resolve_model_id(model_id), api_key=api_settings.gemini_api_key),
        compress_tool_results=True,
        enable_agentic_state=True,
        add_datetime_to_context=True,
        enable_agentic_memory=True,
        respond_directly=True,
        read_chat_history=True,
        store_history_messages=True,
        num_history_runs=3,
        markdown=True,
        debug_mode=debug_mode,
        show_members_responses=True,
        telemetry=True,
        db=db_instance,
        memory_manager=memory_manager,
        knowledge=get_knowledge_service().get_dynamic_kb(),
        pre_hooks=[team_pre_hook],
    )


@v2_teams_router.get("", response_model=List[TeamInfo])
async def list_teams_v2(db: Session = Depends(get_db), include_inactive: bool = False):
    """
    Returns a list of all available teams from database.

    Args:
        db: Database session
        include_inactive: If True, include soft-deleted teams (default: False)

    Returns:
        List[TeamInfo]: List of team information objects
    """
    logging.info(f"Request to list all available teams (v2) from database (include_inactive={include_inactive})")

    # Get teams from database
    db_teams = get_all_team_info(db, include_inactive=include_inactive)

    # Transform database models to API response models with agents
    teams = []
    for db_team in db_teams:
        # Get team agents
        team_agents_db = get_team_agents(db, str(db_team.id))
        agents = [TeamAgent(agent_id=ta.agent_id, role=ta.role, order_index=ta.order_index) for ta in team_agents_db]  # type: ignore[arg-type]

        teams.append(
            TeamInfo(  # type: ignore[arg-type]
                id=db_team.id,  # type: ignore[arg-type]
                name=db_team.name,  # type: ignore[arg-type]
                description=db_team.description,  # type: ignore[arg-type]
                version=db_team.version,  # type: ignore[arg-type]
                mode=db_team.mode or "coordinate",  # type: ignore[arg-type]
                agents=agents,
                updated_at=db_team.updated_at.isoformat(),
                created_at=db_team.created_at.isoformat(),
            )
        )

    logging.info(f"Returning {len(teams)} available teams (v2) from database")
    return teams


@v2_teams_router.get("/{team_id}", response_model=TeamInfo)
async def get_team_info_v2(team_id: str, db: Session = Depends(get_db)):
    """
    Get detailed information about a specific team from database.

    Args:
        team_id: The ID of the team to get information for
        db: Database session

    Returns:
        TeamInfo: Team information object
    """
    logging.info(f"Request for team info: {team_id} from database")

    # Get team data from database
    db_team = get_team_info(db, team_id)
    if not db_team:
        logging.error(f"Team {_log_safe(team_id)} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

    # Get team agents
    team_agents_db = get_team_agents(db, team_id)
    agents = [TeamAgent(agent_id=ta.agent_id, role=ta.role, order_index=ta.order_index) for ta in team_agents_db]  # type: ignore[arg-type]

    team_info = TeamInfo(  # type: ignore[arg-type]
        id=db_team.id,  # type: ignore[arg-type]
        name=db_team.name,  # type: ignore[arg-type]
        description=db_team.description,  # type: ignore[arg-type]
        version=db_team.version,  # type: ignore[arg-type]
        mode=db_team.mode or "coordinate",  # type: ignore[arg-type]
        agents=agents,
        updated_at=db_team.updated_at.isoformat(),
        created_at=db_team.created_at.isoformat(),
    )

    logging.info(f"Returning team info for: {team_id} from database")
    return team_info


async def team_response_streamer(
    team: Team,
    message: str,
    db: Session = Depends(get_db),
    usage_ctx: Optional[usage_service.UsageContext] = None,
) -> AsyncGenerator:
    """
    Stream team responses chunk by chunk (SSE format).

    Args:
        team: The team instance to interact with
        message: User message to process
        db: Database session

    Yields:
        SSE-formatted event strings with team response chunks including status, run_id, and tools
    """
    logging.debug(f"Starting team streaming response for message: {_log_safe(message[:50])}...")
    run_response = team.arun(message, stream=True)  # Returns async generator, don't await
    chunk_count = 0
    full_output_text = ""
    collector = usage_service.UsageCollector(
        usage_ctx or usage_service.UsageContext(agent_id=getattr(team, "id", None), kind="team")
    )
    collector.set_model(*usage_service.model_of(team))
    collector.input_text = message

    try:
        async for chunk in run_response:
            # Skip None chunks
            if chunk is None:
                logging.debug("Skipping None chunk in team streaming")
                continue

            chunk_count += 1
            if chunk_count % 10 == 0:  # Log every 10 chunks to avoid excessive logging
                logging.debug(f"Team streaming chunk #{chunk_count}")

            # Convert chunk to dict
            event_data = _event_to_dict(chunk)

            # Skip if we couldn't convert to dict properly
            if not event_data or "error" in event_data:
                logging.warning(f"Skipping chunk that couldn't be serialized: {event_data}")
                continue
            collector.observe(event_data)

            # Accumulate the answer text
            if isinstance(event_data.get("content"), str):
                full_output_text += event_data["content"]

            # Cache the run for later commit if run_id is present
            if "run_id" in event_data and event_data["run_id"]:
                try:
                    with _team_run_cache_lock:
                        _team_run_cache[event_data["run_id"]] = chunk
                        logging.debug(f"Cached team run {event_data['run_id']} for commit")

                        # If this is a child agent's RunPaused event (has parent_run_id), also cache by parent_run_id
                        # This allows the client to commit using the team's run_id when a child agent pauses
                        if (
                            event_data.get("event") == "RunPaused"
                            and "parent_run_id" in event_data
                            and event_data["parent_run_id"]
                        ):
                            _team_run_cache[event_data["parent_run_id"]] = chunk
                            logging.debug(
                                f"Child agent paused - also cached by parent run_id {event_data['parent_run_id']}"
                            )
                except Exception as cache_error:
                    logging.error(f"Error caching run: {cache_error}", exc_info=True)

            # Process tools using shared helper function
            _process_tools_in_event(event_data)

            yield _format_sse_event("message", event_data)

    except (asyncio.CancelledError, GeneratorExit):
        # The client hung up mid-stream. Record the run as cancelled, not completed:
        # its final metrics never arrived (api/services/usage.py, disconnect()).
        collector.disconnect()
        raise
    except Exception as e:
        # Log the error and send error event to client
        logging.error(f"Error during team streaming response: {type(e).__name__}: {str(e)}", exc_info=True)
        collector.fail(f"{type(e).__name__}: {e}")
        error_data = {
            "status": "error",
            # The detail stays server-side (the log above and token_usage.error): an
            # exception message can carry provider or internal details.
            "error": "Streaming error",
            "content": full_output_text if full_output_text else None,
        }
        yield _format_sse_event("error", error_data)
    finally:
        # Meter the run after all chunks are processed, on error, or on a disconnect -
        # failed and abandoned runs are billed too. Shielded: after a disconnect the
        # stream's cancel scope would otherwise cancel this write as well.
        await usage_service.shielded(record_usage(collector))
        logging.debug(f"Completed team streaming response with {chunk_count} chunks")


async def commit_team_response_streamer(
    team: Team,
    body: TeamCommitRequest,
    cached_run: Any,
    db: Session = Depends(get_db),
    usage_ctx: Optional[usage_service.UsageContext] = None,
) -> AsyncGenerator:
    """
    Stream team responses for commit/continue run chunk by chunk.

    Args:
        team: The team instance to interact with
        body: TeamCommitRequest with run_id and updated_tools
        cached_run: Cached run output with updated tools
        db: Database session

    Yields:
        SSE-formatted event strings with team response chunks including status, run_id, and tools
    """
    logging.debug(f"Starting team streaming commit response for run_id: {body.run_id}")

    run_response = team.acontinue_run(  # type: ignore[attr-defined]
        run_id=body.run_id,
        # agno 3 resumes from `requirements` and ignores `updated_tools` - see agents/hitl.py.
        requirements=requirements_for(cached_run, cached_run.tools),
        stream=True,
    )
    chunk_count = 0
    full_output_text = ""
    collector = usage_service.UsageCollector(
        usage_ctx or usage_service.UsageContext(agent_id=getattr(team, "id", None), kind="team")
    )
    collector.set_model(*usage_service.model_of(team))
    collector.input_text = "[commit continuation]"

    try:
        async for chunk in run_response:
            # Skip None chunks
            if chunk is None:
                continue

            chunk_count += 1
            if chunk_count % 10 == 0:  # Log every 10 chunks to avoid excessive logging
                logging.debug(f"Team streaming commit chunk #{chunk_count}")

            # Convert chunk to dict
            event_data = _event_to_dict(chunk)
            collector.observe(event_data)

            # Accumulate the answer text
            if isinstance(event_data.get("content"), str):
                full_output_text += event_data["content"]

            # Process tools using shared helper function
            _process_tools_in_event(event_data)

            yield _format_sse_event("message", event_data)

    except (asyncio.CancelledError, GeneratorExit):
        # The client hung up mid-stream. Record the run as cancelled, not completed:
        # its final metrics never arrived (api/services/usage.py, disconnect()).
        collector.disconnect()
        raise
    except Exception as e:
        # Log the error and send error event to client
        logging.error(f"Error during team streaming commit response: {type(e).__name__}: {str(e)}")
        collector.fail(f"{type(e).__name__}: {e}")
        error_data = {
            "status": "error",
            # The detail stays server-side (the log above and token_usage.error): an
            # exception message can carry provider or internal details.
            "error": "Streaming error",
            "content": full_output_text if full_output_text else None,
        }
        yield _format_sse_event("error", error_data)
    finally:
        # Meter the run after all chunks are processed, on error, or on a disconnect -
        # failed and abandoned runs are billed too. Shielded: after a disconnect the
        # stream's cancel scope would otherwise cancel this write as well.
        await usage_service.shielded(record_usage(collector))
        logging.debug(f"Completed team streaming commit response with {chunk_count} chunks")

        # Clean up run cache
        with _team_run_cache_lock:
            if body.run_id in _team_run_cache:
                del _team_run_cache[body.run_id]


@v2_teams_router.post("/{team_id}/runs", status_code=status.HTTP_200_OK)
async def create_team_run_v2(
    team_id: str,
    body: TeamRunRequest,
    db: Session = Depends(get_db),
    api_key: Optional[ApiKeyDB] = Depends(get_api_key),
):
    """
    Create a team run using the v2 API.

    Args:
        team_id: The ID of the team to run
        body: Team run request parameters
        db: Database session

    Returns:
        Either a streaming response or a complete TeamRunResponse
    """
    logging.info(f"Creating v2 team run for team_id: {_log_safe(team_id)}")
    logging.debug(f"TeamRunRequest: {_log_safe(body)}")

    # Validate team exists in database first
    db_team = get_team_info(db, team_id)
    if not db_team:
        logging.error(f"Team {_log_safe(team_id)} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

    try:
        # Get team agents
        team_agents_db = get_team_agents(db, team_id)
        if not team_agents_db:
            logging.error(f"Team {_log_safe(team_id)} has no agents")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Team {team_id} has no agents")

        # Check team mode — supervisor teams use the supervisor team builder
        team_mode = db_team.mode if hasattr(db_team, "mode") else "coordinate"
        logging.info(f"Team mode: {team_mode}, agents count: {len(team_agents_db)}")

        if team_mode == "supervisor":
            from supervisor.team_builder import build_supervisor_team

            logging.info("Building supervisor team...")
            team = await asyncio.to_thread(
                build_supervisor_team,
                team_id=team_id,
                team_name=str(db_team.name),
                team_agents_db=team_agents_db,
                db=db,
                user_id=body.user_id or "anonymous",
                session_id=body.session_id or f"session_{team_id}",
                model_id=body.model.value,
                organizer_email=body.user_profile.email if body.user_profile else "default@example.com",
                tenant_id=body.tenant_profile.tenant_id if body.tenant_profile else "default_tenant",
                fetch_token_func=fetch_access_token,
                debug_mode=False,
            )
        else:
            # Default coordinate mode — existing behavior
            sorted_team_agents = sorted(
                team_agents_db, key=lambda x: int(x.order_index) if x.order_index is not None else 999
            )
            agents = []
            for team_agent in sorted_team_agents:
                try:
                    agent = get_agent(
                        model_id=body.model.value,
                        agent_id=str(team_agent.agent_id),
                        user_id=body.user_id,
                        session_id=body.session_id,
                        organizer_email=body.user_profile.email if body.user_profile else "default@example.com",
                        tenant_id=body.tenant_profile.tenant_id if body.tenant_profile else "default_tenant",
                        debug_mode=None,
                        fetch_token_func=fetch_access_token,
                        db=db,
                    )
                    agents.append(agent)
                except Exception as e:
                    logging.error(f"Failed to create agent {team_agent.agent_id}: {e}")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Failed to create agent {team_agent.agent_id}",
                    )

            db_url = get_db_url()
            team = create_team(
                team_name=str(db_team.name),
                team_id=team_id,
                user_id=body.user_id or "anonymous",
                session_id=body.session_id or f"session_{team_id}",
                members=agents,
                model_id=body.model.value,
                db_url=db_url,
                debug_mode=False,
            )
        logging.debug(f"Successfully created enhanced team: {_log_safe(team_id)}")

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error creating team {_log_safe(team_id)}: {_log_safe(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    if body.stream:
        logging.info(f"Returning v2 streaming response for team: {_log_safe(team_id)}")
        return StreamingResponse(
            team_response_streamer(
                team, body.message, db, usage_service.context_for(team_id, body, api_key, kind="team")
            ),
            media_type="text/event-stream",
        )
    else:
        logging.info(f"Processing v2 non-streaming request for team: {_log_safe(team_id)}")
        collector = usage_service.UsageCollector(usage_service.context_for(team_id, body, api_key, kind="team"))
        collector.set_model(*usage_service.model_of(team))
        collector.input_text = body.message
        try:
            response = await team.arun(body.message, stream=False)
        except Exception as e:
            collector.fail(f"{type(e).__name__}: {e}")
            await record_usage(collector)
            raise
        logging.debug(f"Completed v2 non-streaming request for team: {_log_safe(team_id)}")

        # Metered like every run (this path used to store nothing); the summary goes back.
        collector.observe_response(response)
        token_usage = await record_usage(collector)

        # Extract status and run_id
        response_status = response.status if hasattr(response, "status") else None
        response_run_id = response.run_id if hasattr(response, "run_id") else None

        # Convert tools to dict format
        tools_dict = []
        if hasattr(response, "tools") and response.tools:
            for tool in response.tools:
                tool_dict = {
                    "tool_call_id": getattr(tool, "tool_call_id", None),
                    "tool_name": getattr(tool, "tool_name", None),
                    "requires_confirmation": getattr(tool, "requires_confirmation", False),
                    "tool_args": getattr(tool, "tool_args", None),
                    "result": getattr(tool, "result", None),
                }
                tools_dict.append(tool_dict)

        # Cache the run for later commit if status is paused
        if response_run_id and response_status == "paused":
            with _team_run_cache_lock:
                _team_run_cache[response_run_id] = response
                logging.debug(f"Cached team run {response_run_id} for commit")

            # Register pending tools as approval notifications
            _register_paused_approvals(response_run_id, team_id, tools_dict)

        # Return structured response
        team_response = TeamRunResponse(
            content=response.content or "",  # type: ignore[arg-type]
            team_id=team_id,
            session_id=body.session_id,
            model=body.model.value,
            token_usage=token_usage,  # type: ignore[arg-type]
            status=response_status,  # type: ignore[arg-type]
            run_id=response_run_id,  # type: ignore[arg-type]
            tools=tools_dict if tools_dict else None,
        )

        return team_response


@v2_teams_router.post("/{team_id}/runs/commit", status_code=status.HTTP_200_OK)
async def commit_team_run_v2(
    team_id: str,
    body: TeamCommitRequest,
    db: Session = Depends(get_db),
    api_key: Optional[ApiKeyDB] = Depends(get_api_key),
):
    """
    Resume a paused team run with confirmed/edited tools.

    Args:
        team_id: The ID of the team
        body: TeamCommitRequest with run_id and updated_tools
        db: Database session

    Returns:
        TeamRunResponse with continued execution results
    """
    try:
        logging.info(f"Commit request for team {_log_safe(team_id)}, run_id: {_log_safe(body.run_id)}")

        # Check if any tool has confirmed=false (user denial)
        tools_with_confirmation = [tool for tool in body.updated_tools if tool.get("confirmed") is not None]
        all_denied = len(tools_with_confirmation) > 0 and all(
            tool.get("confirmed") is False for tool in tools_with_confirmation
        )

        # Retrieve cached run
        with _team_run_cache_lock:
            if body.run_id not in _team_run_cache:
                logging.error(f"Run ID {_log_safe(body.run_id)} not found in cache")
                # If all tools are denied and run_id not found, return denial response without error
                if all_denied:
                    logging.info(
                        f"Run ID {_log_safe(body.run_id)} not found but all tools denied - returning denial response"
                    )
                    return TeamRunResponse(
                        content="Tool execution cancelled by user.",
                        team_id=team_id,
                        session_id=body.session_id or "",
                        model=body.model.value,
                        status="cancelled",
                    )
                # Otherwise, the confirmation time window has elapsed
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail="Confirmation time window has elapsed. The run is no longer available for resumption.",
                )

            cached_run = _team_run_cache[body.run_id]

        # Check if user denied all tools
        if all_denied:
            logging.info(f"User denied all tools for run_id: {_log_safe(body.run_id)}")
            # Clean up run cache
            with _team_run_cache_lock:
                if body.run_id in _team_run_cache:
                    del _team_run_cache[body.run_id]
            return TeamRunResponse(
                content="Tool execution cancelled by user.",
                team_id=team_id,
                session_id=body.session_id or "",
                model=body.model.value,
                status="cancelled",
            )

        # Update tools with user confirmations/edits
        UPDATABLE_FIELDS = {"tool_args", "args", "confirmed", "confirmation_note"}
        for updated_tool_dict in body.updated_tools:
            tool_id = updated_tool_dict.get("tool_call_id") or updated_tool_dict.get("id")
            # Find and update the original tool object
            for original_tool in cached_run.tools:
                if getattr(original_tool, "tool_call_id", None) == tool_id:
                    # Update allowed fields
                    for key, value in updated_tool_dict.items():
                        if key in UPDATABLE_FIELDS and hasattr(original_tool, key):
                            setattr(original_tool, key, value)
                    break

        # Recreate the team to continue execution
        db_team = get_team_info(db, team_id)
        if not db_team:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

        # Get team agents
        team_agents_db = get_team_agents(db, team_id)
        if not team_agents_db:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Team {team_id} has no agents")

        # Create agents for the team
        sorted_team_agents = sorted(
            team_agents_db, key=lambda x: int(x.order_index) if x.order_index is not None else 999
        )
        agents = []
        for team_agent in sorted_team_agents:
            agent = get_agent(
                model_id=body.model.value,
                agent_id=str(team_agent.agent_id),
                user_id=body.user_id,
                session_id=body.session_id,
                db=db,
            )
            agents.append(agent)

        # Get database URL and recreate team
        db_url = get_db_url()
        team = create_team(
            team_name=str(db_team.name),
            team_id=team_id,
            user_id=body.user_id or "anonymous",
            session_id=body.session_id or f"session_{team_id}",
            members=agents,
            model_id=body.model.value,
            db_url=db_url,
            debug_mode=False,
        )

        # Continue the run with updated tools
        collector = usage_service.UsageCollector(usage_service.context_for(team_id, body, api_key, kind="team"))
        collector.set_model(*usage_service.model_of(team))
        collector.input_text = "[commit continuation]"
        try:
            response = await team.acontinue_run(  # type: ignore[attr-defined]
                run_id=body.run_id, requirements=requirements_for(cached_run, cached_run.tools), stream=False
            )
        except Exception as e:
            collector.fail(f"{type(e).__name__}: {e}")
            await record_usage(collector)
            raise
        logging.debug(f"Completed commit request for team: {_log_safe(team_id)}")

        # Metered like every run (this path used to store nothing); the summary goes back.
        collector.observe_response(response)
        token_usage = await record_usage(collector)

        # Clean up run cache
        with _team_run_cache_lock:
            if body.run_id in _team_run_cache:
                del _team_run_cache[body.run_id]

        return TeamRunResponse(
            content=response.content or "",  # type: ignore[arg-type]
            team_id=team_id,
            session_id=body.session_id or "",
            model=body.model.value,
            token_usage=token_usage,  # type: ignore[arg-type]
            status="completed",
        )

    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"Unexpected error in commit_team_run_v2 for team {_log_safe(team_id)}: {_log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error occurred while committing team run for team {team_id}",
        )


@v2_teams_router.get("/{team_id}/sessions", response_model=List[TeamSession])
async def get_team_sessions_v2(team_id: str, db: Session = Depends(get_db)):
    """
    Get all sessions for a specific team.

    Args:
        team_id: The ID of the team
        db: Database session

    Returns:
        List[TeamSession]: List of team session objects
    """
    logging.info(f"Request for team sessions: {team_id}")

    # Validate team exists
    db_team = get_team_info(db, team_id)
    if not db_team:
        logging.error(f"Team {_log_safe(team_id)} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

    try:
        # Get database URL for storage configuration
        db_url = get_db_url()

        # Get team storage instance
        team_storage = get_team_storage(team_id, db_url)

        # Get all sessions for the team
        all_sessions = team_storage.get_all_sessions(user_id=None, entity_id=team_id)  # type: ignore[attr-defined]

        # Convert Agno sessions to API response format
        sessions: List[TeamSession] = []
        for session in all_sessions:
            try:
                # Only process team sessions, skip others
                if not (hasattr(session, "team_session_id") and hasattr(session, "team_id")):
                    continue

                # Handle timestamp conversion
                created_at_str = (
                    (
                        session.created_at.isoformat()
                        if hasattr(session.created_at, "isoformat")
                        else str(session.created_at)
                    )
                    if session.created_at is not None
                    else ""
                )
                updated_at_str = (
                    (
                        session.updated_at.isoformat()
                        if hasattr(session.updated_at, "isoformat")
                        else str(session.updated_at)
                    )
                    if session.updated_at is not None
                    else ""
                )

                team_session = TeamSession(
                    id=session.team_session_id,  # type: ignore[arg-type]
                    team_id=session.team_id,  # type: ignore[arg-type]
                    created_at=created_at_str,
                    updated_at=updated_at_str,
                    message_count=len(session.session_data) if session.session_data else 0,
                )
                sessions.append(team_session)
            except Exception as e:
                logging.warning(f"Failed to parse session for team {team_id}: {e}")
                continue

        # Sort sessions by creation time (newest first)
        sessions.sort(key=lambda x: x.created_at, reverse=True)

    except Exception as e:
        logging.error(f"Error retrieving sessions for team {team_id}: {e}")
        sessions = []

    logging.info(f"Returning {len(sessions)} sessions for team: {team_id}")
    return sessions


@v2_teams_router.get("/{team_id}/sessions/{session_id}", response_model=TeamSession)
async def get_team_session_v2(team_id: str, session_id: str, db: Session = Depends(get_db)):
    """
    Get a specific team session.

    Args:
        team_id: The ID of the team
        session_id: The ID of the session
        db: Database session

    Returns:
        TeamSession: Team session object
    """
    logging.info(f"Request for team session: {team_id}/{session_id}")

    # Validate team exists
    db_team = get_team_info(db, team_id)
    if not db_team:
        logging.error(f"Team {_log_safe(team_id)} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

    try:
        # Get database URL for storage configuration
        db_url = get_db_url()

        # Get team storage instance
        team_storage = get_team_storage(team_id, db_url)

        # Get all sessions for the team
        all_sessions = team_storage.get_all_sessions(user_id=None, entity_id=team_id)  # type: ignore[attr-defined]

        # Find the specific session
        target_session = None
        for session in all_sessions:
            if hasattr(session, "team_session_id") and session.team_session_id == session_id:
                target_session = session
                break

        if not target_session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

        # Handle timestamp conversion
        created_at_str = (
            (
                target_session.created_at.isoformat()
                if hasattr(target_session.created_at, "isoformat")
                else str(target_session.created_at)
            )
            if target_session.created_at is not None
            else ""
        )
        updated_at_str = (
            (
                target_session.updated_at.isoformat()
                if hasattr(target_session.updated_at, "isoformat")
                else str(target_session.updated_at)
            )
            if target_session.updated_at is not None
            else ""
        )

        team_session = TeamSession(
            id=target_session.team_session_id,  # type: ignore[arg-type]
            team_id=target_session.team_id,  # type: ignore[arg-type,union-attr]
            created_at=created_at_str,
            updated_at=updated_at_str,
            message_count=len(target_session.session_data) if target_session.session_data else 0,
        )

        logging.info(f"Returning session {session_id} for team: {team_id}")
        return team_session

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error retrieving session {session_id} for team {team_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to retrieve session {session_id}"
        )


@v2_teams_router.delete("/{team_id}/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team_session_v2(team_id: str, session_id: str, db: Session = Depends(get_db)):
    """
    Delete a specific team session.

    Args:
        team_id: The ID of the team
        session_id: The ID of the session to delete
        db: Database session
    """
    logging.info(f"Request to delete team session: {team_id}/{session_id}")

    # Validate team exists
    db_team = get_team_info(db, team_id)
    if not db_team:
        logging.error(f"Team {_log_safe(team_id)} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

    try:
        # Get database URL for storage configuration
        db_url = get_db_url()

        # Get team storage instance
        team_storage = get_team_storage(team_id, db_url)

        # Attempt to delete the session directly
        # If the session doesn't exist, delete_session should handle it gracefully
        try:
            team_storage.delete_session(session_id=session_id)
            logging.info(f"Team session {team_id}/{session_id} deleted successfully")
        except AttributeError as attr_err:
            # If delete_session doesn't exist, the Agno API may have changed
            logging.error(f"PostgresDb API error: {attr_err}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Session deletion not supported in current Agno version",
            )
        except Exception as delete_err:
            # Session might not exist or other deletion error
            logging.warning(f"Failed to delete session {session_id}: {delete_err}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found or could not be deleted",
            )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error deleting session {session_id} for team {team_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete session {session_id}"
        )


@v2_teams_router.post("", response_model=CreateTeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team_v2(body: CreateTeamRequest, db: Session = Depends(get_db)):
    """
    Create a new team with the provided configuration.

    Args:
        body: Team creation request containing id, name, description, and agent_ids
        db: Database session

    Returns:
        CreateTeamResponse: Confirmation of team creation with details
    """
    logging.info(f"Request to create new team: {body.id}")

    # Check if team already exists in database
    if team_info_exists(db, body.id):
        logging.error(f"Team {body.id} already exists in database")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Team {body.id} already exists")

    # Validate team ID format (alphanumeric and underscores only)
    if not body.id.replace("_", "").replace("-", "").isalnum():
        logging.error(f"Invalid team ID format: {body.id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team ID can only contain letters, numbers, underscores, and hyphens",
        )

    # Validate that all specified agents exist
    for assignment in body.agents:
        agent_info = get_agent_info(db, assignment.agent_id)
        if not agent_info:
            logging.error(f"Agent {assignment.agent_id} not found in database")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Agent {assignment.agent_id} not found"
            )

    try:
        # Create team in database
        create_team_info(
            db=db, team_id=body.id, name=body.name, description=body.description, version="2.0", mode=body.mode
        )

        # Add agents to team with roles and order
        for assignment in body.agents:
            try:
                add_agent_to_team(
                    db=db,
                    team_id=body.id,
                    agent_id=assignment.agent_id,
                    role=assignment.role,
                    order_index=assignment.order_index,
                )
            except Exception as e:
                logging.error(f"Failed to add agent {assignment.agent_id} to team {body.id}: {e}")
                # Rollback: delete the team
                soft_delete_team_info(db, body.id)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to add agent {assignment.agent_id} to team",
                )

        logging.info(f"Team {body.id} created successfully with {len(body.agents)} agents")

        response = CreateTeamResponse(
            id=body.id,
            name=body.name,
            description=body.description,
            version="2.0",
            message=f"Team {body.id} created successfully",
        )

        return response

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logging.error(f"Error creating team {body.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create team {body.id}: {str(e)}"
        )


@v2_teams_router.delete("/{team_id}", status_code=status.HTTP_200_OK)
async def delete_team_v2(team_id: str, soft: bool = False, db: Session = Depends(get_db)):
    """
    Delete a team.

    Args:
        team_id: The ID of the team to delete
        soft: If True, perform soft delete (set is_active=False). If False, perform hard delete (remove from DB)
        db: Database session

    Returns:
        JSON response with success message

    Examples:
        - Hard delete: DELETE /v2/teams/my_team
        - Soft delete: DELETE /v2/teams/my_team?soft=true
    """
    delete_type = "soft delete" if soft else "hard delete"
    logging.info(f"Request to {delete_type} team: {team_id}")

    try:
        if soft:
            # Soft delete: mark as inactive
            result = soft_delete_team_info(db, team_id)
            if not result:
                logging.error(f"Team {_log_safe(team_id)} not found in database")
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

            logging.info(f"Team {team_id} soft deleted successfully")
            return {"message": f"Team {team_id} archived (soft deleted) successfully"}
        else:
            # Hard delete: permanently remove from database
            result = delete_team_info(db, team_id)
            if not result:
                logging.error(f"Team {_log_safe(team_id)} not found in database")
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

            logging.info(f"Team {team_id} hard deleted successfully")
            return {"message": f"Team {team_id} deleted permanently"}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error deleting team {team_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete team {team_id}"
        )


@v2_teams_router.get("/{team_id}/memories", response_model=List[TeamMemory])
async def get_team_memories_v2(team_id: str, db: Session = Depends(get_db)):
    """
    Get team memories.

    Args:
        team_id: The ID of the team
        db: Database session

    Returns:
        List[TeamMemory]: List of team memory objects
    """
    logging.info(f"Request for team memories: {team_id}")

    # Validate team exists
    db_team = get_team_info(db, team_id)
    if not db_team:
        logging.error(f"Team {_log_safe(team_id)} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

    try:
        # Get database URL for storage configuration
        db_url = get_db_url()

        # Get team memory database instance
        team_memory_db = get_team_memory_db(team_id, db_url)

        # Read memories from the database
        # Note: We're using a general user_memories table, so we might get memories
        # from multiple teams. In a production system, you might want team-specific
        # memory tables or filter by team context.
        memory_data = team_memory_db.read_memories()  # type: ignore[attr-defined]

        memories: List[TeamMemory] = []
        for memory in memory_data:
            try:
                team_memory = TeamMemory(
                    id=str(memory.id) if hasattr(memory, "id") else f"memory_{len(memories)}",
                    team_id=team_id,
                    session_id=None,  # Memory may not have session context
                    content=str(memory.memory) if hasattr(memory, "memory") else str(memory),
                    created_at=memory.created_at.isoformat() if hasattr(memory, "created_at") else "",
                )
                memories.append(team_memory)
            except Exception as e:
                logging.warning(f"Failed to parse memory for team {team_id}: {e}")
                continue

        # Sort memories by creation time (newest first)
        memories.sort(key=lambda x: x.created_at, reverse=True)

    except Exception as e:
        logging.error(f"Error retrieving memories for team {team_id}: {e}")
        memories = []

    logging.info(f"Returning {len(memories)} memories for team: {team_id}")
    return memories


@v2_teams_router.get("/{team_id}/members", response_model=List[MemberResponse])
async def get_team_members_v2(team_id: str, db: Session = Depends(get_db)):
    """
    Get all members of a team.

    Args:
        team_id: The ID of the team
        db: Database session

    Returns:
        List[MemberResponse]: List of team members with their roles and order
    """
    logging.info(f"Request for team members: {team_id}")

    # Validate team exists
    db_team = get_team_info(db, team_id)
    if not db_team:
        logging.error(f"Team {_log_safe(team_id)} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

    # Get team agents
    team_agents_db = get_team_agents(db, team_id)

    members = []
    for ta in team_agents_db:
        member = MemberResponse(
            agent_id=ta.agent_id,  # type: ignore[arg-type]
            role=ta.role,  # type: ignore[arg-type]
            order_index=ta.order_index,  # type: ignore[arg-type]
            created_at=ta.created_at.isoformat() if ta.created_at else "",  # type: ignore[arg-type]
        )
        members.append(member)

    logging.info(f"Returning {len(members)} members for team: {team_id}")
    return members


@v2_teams_router.post("/{team_id}/members", response_model=AddMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_team_member_v2(team_id: str, body: AddMemberRequest, db: Session = Depends(get_db)):
    """
    Add a member to a team.

    Args:
        team_id: The ID of the team
        body: Member addition request
        db: Database session

    Returns:
        AddMemberResponse: Confirmation of member addition with details
    """
    logging.info(f"Request to add member {body.agent_id} to team {team_id}")

    # Validate team exists
    db_team = get_team_info(db, team_id)
    if not db_team:
        logging.error(f"Team {_log_safe(team_id)} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

    # Validate agent exists
    agent_info = get_agent_info(db, body.agent_id)
    if not agent_info:
        logging.error(f"Agent {body.agent_id} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent {body.agent_id} not found")

    try:
        # Add agent to team
        team_agent = add_agent_to_team(
            db=db,
            team_id=team_id,
            agent_id=body.agent_id,
            role=body.role,
            order_index=body.order_index,
        )

        member_response = MemberResponse(
            agent_id=team_agent.agent_id,  # type: ignore[arg-type]
            role=team_agent.role,  # type: ignore[arg-type]
            order_index=team_agent.order_index,  # type: ignore[arg-type]
            created_at=team_agent.created_at.isoformat() if team_agent.created_at else "",  # type: ignore[arg-type]
        )

        response = AddMemberResponse(
            message=f"Agent {body.agent_id} added to team {team_id} successfully",
            member=member_response,
        )

        logging.info(f"Agent {body.agent_id} added to team {team_id} successfully")
        return response

    except ValueError as e:
        logging.error(f"Error adding agent {body.agent_id} to team {team_id}: {e}")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        logging.error(f"Error adding agent {body.agent_id} to team {team_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add agent {body.agent_id} to team {team_id}",
        )


@v2_teams_router.delete("/{team_id}/members/{agent_id}", status_code=status.HTTP_200_OK)
async def remove_team_member_v2(team_id: str, agent_id: str, db: Session = Depends(get_db)):
    """
    Remove a member from a team.

    Args:
        team_id: The ID of the team
        agent_id: The ID of the agent to remove
        db: Database session

    Returns:
        JSON response with success message
    """
    logging.info(f"Request to remove member {agent_id} from team {team_id}")

    # Validate team exists
    db_team = get_team_info(db, team_id)
    if not db_team:
        logging.error(f"Team {_log_safe(team_id)} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

    try:
        # Remove agent from team
        result = remove_agent_from_team(db=db, team_id=team_id, agent_id=agent_id)

        if not result:
            logging.error(f"Agent {agent_id} not found in team {team_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent {agent_id} is not a member of team {team_id}",
            )

        logging.info(f"Agent {agent_id} removed from team {team_id} successfully")
        return {"message": f"Agent {agent_id} removed from team {team_id} successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error removing agent {agent_id} from team {team_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove agent {agent_id} from team {team_id}",
        )


@v2_teams_router.put("/{team_id}/members/{agent_id}", response_model=MemberResponse)
async def update_team_member_v2(team_id: str, agent_id: str, body: UpdateMemberRequest, db: Session = Depends(get_db)):
    """
    Update a team member's role and/or order.

    Args:
        team_id: The ID of the team
        agent_id: The ID of the agent to update
        body: Member update request
        db: Database session

    Returns:
        MemberResponse: Updated member information
    """
    logging.info(f"Request to update member {agent_id} in team {team_id}")

    # Validate team exists
    db_team = get_team_info(db, team_id)
    if not db_team:
        logging.error(f"Team {_log_safe(team_id)} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Team {team_id} not found")

    # Validate at least one field is provided for update
    if body.role is None and body.order_index is None:
        logging.error("No fields provided for update")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field (role or order_index) must be provided for update",
        )

    try:
        # Update team agent role/order
        updated_team_agent = update_team_agent_role(
            db=db,
            team_id=team_id,
            agent_id=agent_id,
            role=body.role,
            order_index=body.order_index,
        )

        if not updated_team_agent:
            logging.error(f"Agent {agent_id} not found in team {team_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent {agent_id} is not a member of team {team_id}",
            )

        member_response = MemberResponse(
            agent_id=updated_team_agent.agent_id,  # type: ignore[arg-type]
            role=updated_team_agent.role,  # type: ignore[arg-type]
            order_index=updated_team_agent.order_index,  # type: ignore[arg-type]
            created_at=updated_team_agent.created_at.isoformat() if updated_team_agent.created_at else "",  # type: ignore[arg-type]
        )

        logging.info(f"Agent {agent_id} updated in team {team_id} successfully")
        return member_response

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error updating agent {agent_id} in team {team_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update agent {agent_id} in team {team_id}",
        )
