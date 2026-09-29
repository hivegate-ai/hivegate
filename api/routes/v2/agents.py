import copy
import json
import logging
import os
import time
import uuid
import zlib
from contextlib import AsyncExitStack
from threading import Lock
from typing import Any, AsyncGenerator, Dict, List, Optional

from agno.agent import Agent
from agno.media import Image

# agno.models.metrics.Metrics was only a backward-compat alias of RunMetrics, and
# agno 3 removed it. RunMetrics lives in agno.metrics from 2.6 through 3.x.
from agno.metrics import RunMetrics as Metrics
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from google import genai
from google.genai.types import File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agents import DEFAULT_MODEL, Model
from agents.hitl import requirements_for
from agents.agent import build_mcp_toolkits, ensure_mcp_ready
from agents.agent import get_agent as get_agent_impl
from agents.agent import slug_to_table_name
from agents.model_factory import ProviderNotConfiguredError
from agents.model_resolver import resolve_model_id
from agents.v2_selector import _get_prompt_from_local_storage
from api.services.access_token import fetch_access_token, has_access_tokens_batch  # noqa: F401
from api.services.models import PullPromptResponse
from api.services.prompts_client import prompts_client
from api.settings import api_settings
from cache.prompts_cache import is_cache_initialized
from prompts.storage import get_prompt_storage
from db.agent_info_crud import (
    AgentConfig,
    agent_info_exists,
    create_agent_info,
    get_agent_config,
    get_agent_info,
    get_agent_tags,
    get_agents_by_ids,
    get_all_agent_info,
    soft_delete_agent_info,
)
from db.db_models import TokenUsage
from db.session import get_db

# Agent caching infrastructure
_agent_cache: Dict[str, Agent] = {}
_cache_lock = Lock()
_run_cache: Dict[str, Any] = {}

# Toolkit execution cache for confirmation flow
_toolkit_execution_cache: Dict[str, Dict[str, Any]] = {}
_toolkit_execution_lock = Lock()


class AgnoJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for Agno media objects and other non-serializable types."""

    def default(self, obj: Any) -> Any:
        # Handle agno.media.File objects
        if hasattr(obj, "__class__") and obj.__class__.__name__ == "File":
            return {
                "_type": "File",
                "mime_type": getattr(obj, "mime_type", None),
                "size": len(getattr(obj, "content", b"")) if hasattr(obj, "content") else None,
            }
        # Handle agno.media.Image objects
        if hasattr(obj, "__class__") and obj.__class__.__name__ == "Image":
            return {
                "_type": "Image",
                "mime_type": getattr(obj, "mime_type", None),
                "size": len(getattr(obj, "content", b"")) if hasattr(obj, "content") else None,
            }
        # Handle bytes
        if isinstance(obj, bytes):
            return f"<bytes:{len(obj)}>"
        # Let the base class raise TypeError for other types
        return super().default(obj)


def _event_to_dict(ev: Any) -> Dict[str, Any]:
    """Convert event to dictionary for SSE streaming."""
    if hasattr(ev, "to_dict"):
        return copy.deepcopy(ev.to_dict())
    return {"error": "Could not serialize event"}


def _format_sse_event(event_type: str, data: Dict[str, Any]) -> str:
    """Format data as Server-Sent Event."""
    json_data = json.dumps(data, cls=AgnoJSONEncoder)
    return f"event: {event_type}\ndata: {json_data}\n\n"


def _process_tools_in_event(event_data: Dict[str, Any]) -> None:
    """
    Process tools in event data, converting them to dict format with parsed results.

    Modifies event_data in place, converting "tools" or "tool" to standardized "tools" array.

    Args:
        event_data: Event data dictionary (deep copied from chunk)
    """
    # Convert tools to dict format and add to event data
    # Work with event_data (deep copy) to avoid mutating the original chunk
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


def estimate_tokens(text: str) -> int:
    """
    Estimate the number of tokens in a text.

    Args:
        text: The text to estimate tokens for

    Returns:
        Estimated token count based on character length
    """
    # Rough heuristic: average 3–4 characters per token in English
    return int(len(text) / 4)


def _log_safe(value: object) -> str:
    """Strip CR/LF so request-derived values (agent/user/session ids) can't forge log lines."""
    return str(value).replace("\r", "").replace("\n", "")


def compute_cache_key(prompt_template: str, agent_id: str, model: Model, user_id: str, session_id: str) -> str:
    """
    Compute CRC32-based cache key for agent caching.

    Args:
        prompt_template: The prompt template text
        agent_id: Agent identifier
        model: Model identifier
        user_id: User identifier
        session_id: Session identifier

    Returns:
        Cache key string: "crc32_hash:agent_id:model_id"
    """
    crc32_hash = zlib.crc32(prompt_template.encode("utf-8")) & 0xFFFFFFFF
    # The resolved id, not the alias: when "anthropic:sonnet-latest" moves to a newer
    # model, a cached agent built on the old one must not keep being served.
    return f"{crc32_hash}:{agent_id}:{resolve_model_id(model)}:{user_id}:{session_id}"


def store_token_usage(
    agent: Agent,
    input_text: str,
    output_text: str,
    metrics: Optional[Metrics] = None,
    db: Optional[Session] = None,
) -> None:
    """
    Store token usage information in the database.

    Args:
        agent: The agent instance that processed the request
        input_text: The input text (prompt)
        output_text: The output text (completion)
        metrics: Optional Metrics object from Agno containing token counts
        db: Database session

    Returns:
        None
    """
    if not db:
        logging.warning("No database session provided, skipping token usage storage")
        return

    if metrics and metrics.total_tokens > 0:
        logging.info(
            f"Token usage - Input: {metrics.total_tokens}, Output: {metrics.output_tokens}, Total: {metrics.total_tokens}"
        )

        # Store token usage in database using Agno 2.x Metrics dataclass
        prompt_tokens = metrics.input_tokens
        completion_tokens = metrics.output_tokens
        total_tokens = metrics.total_tokens

        token_usage = TokenUsage(
            agent_id=agent.id,  # type: ignore[attr-defined]
            session_id=agent.session_id,
            user_id=agent.user_id,
            model=agent.model.id if hasattr(agent, "model") and agent.model else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            is_estimated=False,
        )
        db.add(token_usage)
        db.commit()
        logging.debug(f"Token usage data stored in database for session {agent.session_id}")
    elif output_text:
        # Estimate tokens if metrics are not available
        prompt_tokens = estimate_tokens(input_text)
        completion_tokens = estimate_tokens(output_text)
        total_tokens = prompt_tokens + completion_tokens

        logging.info(
            f"Estimated token usage - Input: {prompt_tokens}, Output: {completion_tokens}, Total: {total_tokens}"
        )

        # Store estimated token usage in database
        token_usage = TokenUsage(
            agent_id=agent.id,  # type: ignore[attr-defined]
            session_id=agent.session_id,
            user_id=agent.user_id,
            model=agent.model.id if hasattr(agent, "model") and agent.model else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            is_estimated=True,
        )
        db.add(token_usage)
        db.commit()
        logging.debug(f"Estimated token usage data stored in database for session {agent.session_id}")


def upload_file_to_google(content_bytes: bytes, mime_type: str) -> File:
    """
    Upload a file to Google's File API and return file metadata.

    Args:
        content_bytes: Raw file bytes to upload
        mime_type: MIME type of the file (e.g., "image/jpeg", "application/pdf")

    Returns:
        Dictionary with 'uri' and 'name' fields from the uploaded file

    Raises:
        HTTPException: If upload fails or API key is missing
    """
    import io

    # Get API key from settings
    api_key = api_settings.gemini_api_key
    if not api_key:
        raise HTTPException(
            status_code=500, detail="GOOGLE_API_KEY not configured. Cannot upload files to Google File API."
        )

    try:
        # Initialize Google GenAI client
        client = genai.Client(api_key=api_key)

        # Create file-like object from bytes
        file_obj = io.BytesIO(content_bytes)
        file_obj.name = f"upload.{mime_type.split('/')[-1]}"  # e.g., "upload.jpeg"
        logging.info(
            "Uploading to File API: len=%s, mime_type=%s, first_12_bytes=%s",
            len(content_bytes),
            mime_type,
            content_bytes[:12],
        )
        uploaded_file = client.files.upload(file=file_obj, config={"mime_type": mime_type})

        logging.info(
            f"File uploaded successfully: uri={uploaded_file.uri}, name={uploaded_file.name}, "
            f"expires_at={uploaded_file.expiration_time}"
        )

        # Return both URI and name - we'll use name for Gemini API
        return uploaded_file
    except Exception as e:
        logging.error(f"Failed to upload file to Google File API: {type(e).__name__}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")


######################################################
## V2 Routes for the Agent Interface
######################################################

v2_agents_router = APIRouter(prefix="/agents", tags=["V2 Agents"])


class AgentConfigResponse(BaseModel):
    """Agent configuration in responses."""

    enable_memory: bool = True
    enable_history: bool = True
    num_history_runs: int = 3
    enable_reasoning: bool = False
    reasoning_min_steps: int = 1
    reasoning_max_steps: int = 10


class AgentInfo(BaseModel):
    """Agent information model for v2 API"""

    id: str
    name: str
    description: Optional[str] = None
    version: str = "2.0"
    template: Optional[str] = None  # Template/prompt from prompt service
    tags: Optional[List[str]] = None  # Tags from database or prompt service
    config: Optional[AgentConfigResponse] = None  # Agent configuration


class GetAgentsByIdsRequest(BaseModel):
    """Request model for getting agents by IDs"""

    agent_ids: List[str]


@v2_agents_router.get("", response_model=List[AgentInfo])
async def list_agents_v2(db: Session = Depends(get_db)):
    """
    Returns a list of all available agents from database with prompt service integration.

    Returns:
        List[AgentInfo]: List of agent information objects
    """
    logging.info("Request to list all available agents (v2) from database")

    if not is_cache_initialized:
        logging.info("Cache not yet initialized, may have slower response times")

    # Get all active agents from database
    db_agents = get_all_agent_info(db, include_inactive=False)

    # Transform database models to API response models with prompt service data
    agents = []
    for db_agent in db_agents:
        # Fetch template from prompt service
        template = None
        prompt_tags = None
        try:
            prompt_data = prompts_client.get_prompt(db_agent.prompt_service_id)  # type: ignore[arg-type]
            if prompt_data:
                template = prompt_data.template
                prompt_tags = prompt_data.tags
        except Exception as e:
            logging.warning(f"Failed to fetch template for agent {db_agent.id}: {e}")

        # Get tags from database, fallback to prompt service tags
        tags = get_agent_tags(db_agent) or prompt_tags

        # Get config from database
        agent_config = get_agent_config(db_agent)

        agents.append(
            AgentInfo(  # type: ignore[arg-type]
                id=db_agent.id,  # type: ignore[arg-type]
                name=db_agent.name,  # type: ignore[arg-type]
                description=db_agent.description,  # type: ignore[arg-type]
                version=db_agent.version,  # type: ignore[arg-type]
                template=template,
                tags=tags,
                config=AgentConfigResponse(**agent_config.model_dump()),
            )
        )

    logging.info(f"Returning {len(agents)} available agents (v2) from database")
    return agents


@v2_agents_router.post("/batch", response_model=List[AgentInfo])
async def get_agents_by_ids_v2(body: GetAgentsByIdsRequest, db: Session = Depends(get_db)):
    """
    Get multiple agents by their IDs from database with prompt service integration.

    Args:
        body: Request containing list of agent IDs
        db: Database session

    Returns:
        List[AgentInfo]: List of agent information objects for the specified IDs
    """
    logging.info(f"Request to get agents by IDs: {body.agent_ids} from database")

    # Get active agents from database by IDs
    db_agents = get_agents_by_ids(db, body.agent_ids)

    # Transform database models to API response models with prompt service data
    agents = []
    for db_agent in db_agents:
        # Fetch template from prompt service
        template = None
        prompt_tags = None
        try:
            prompt_data = prompts_client.get_prompt(db_agent.prompt_service_id)  # type: ignore[arg-type]
            if prompt_data:
                template = prompt_data.template
                prompt_tags = prompt_data.tags
        except Exception as e:
            logging.warning(f"Failed to fetch template for agent {db_agent.id}: {e}")

        # Get tags from database, fallback to prompt service tags
        tags = get_agent_tags(db_agent) or prompt_tags

        # Get config from database
        agent_config = get_agent_config(db_agent)

        agents.append(
            AgentInfo(  # type: ignore[arg-type]
                id=db_agent.id,  # type: ignore[arg-type]
                name=db_agent.name,  # type: ignore[arg-type]
                description=db_agent.description,  # type: ignore[arg-type]
                version=db_agent.version,  # type: ignore[arg-type]
                template=template,
                tags=tags,
                config=AgentConfigResponse(**agent_config.model_dump()),
            )
        )

    logging.info(f"Returning {len(agents)} agents for requested IDs from database")
    return agents


@v2_agents_router.get("/{agent_id}", response_model=AgentInfo)
async def get_agent_info_v2(agent_id: str, db: Session = Depends(get_db)):
    """
    Get detailed information about a specific agent from database.

    Args:
        agent_id: The ID of the agent to get information for
        db: Database session

    Returns:
        AgentInfo: Agent information object
    """
    logging.info(f"Request for agent info: {agent_id} from database")

    # Get agent data from database
    db_agent = get_agent_info(db, agent_id)
    if not db_agent:
        logging.error(f"Agent {agent_id} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent {agent_id} not found")

    # Fetch template from prompt service
    template = None
    prompt_tags = None
    try:
        prompt_data = prompts_client.get_prompt(db_agent.prompt_service_id)  # type: ignore[arg-type]
        if prompt_data:
            template = prompt_data.template
            prompt_tags = prompt_data.tags
    except Exception as e:
        logging.warning(f"Failed to fetch template for agent {db_agent.id}: {e}")

    # Get tags from database, fallback to prompt service tags
    tags = get_agent_tags(db_agent) or prompt_tags

    # Get config from database
    agent_config = get_agent_config(db_agent)

    agent_info = AgentInfo(  # type: ignore[arg-type]
        id=db_agent.id,  # type: ignore[arg-type]
        name=db_agent.name,  # type: ignore[arg-type]
        description=db_agent.description,  # type: ignore[arg-type]
        version=db_agent.version,  # type: ignore[arg-type]
        template=template,
        tags=tags,
        config=AgentConfigResponse(**agent_config.model_dump()),
    )

    logging.info(f"Returning agent info for: {agent_id} from database")
    return agent_info


class UserProfile(BaseModel):
    """User profile information"""

    profile_id: str
    email: str
    full_name: str
    role: str  # Role serves as the position/title
    department: Optional[str] = None
    skills: Optional[str] = None
    tools: Optional[str] = None
    tenant_id: str


class TenantProfile(BaseModel):
    """Tenant profile information"""

    tenant_id: str
    name: str
    description: Optional[str] = None
    website: str


class ChatRequest(BaseModel):
    """Chat request model for v2 API"""

    message: str
    stream: bool = True
    # Default when a caller omits `model`: the DEFAULT_CHAT_MODEL env var, else
    # gemini-3-flash-preview (see agents.DEFAULT_MODEL). A caller can always pass `model`.
    model: Model = DEFAULT_MODEL
    user_id: str
    session_id: str
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    user_profile: Optional[UserProfile] = None
    tenant_profile: Optional[TenantProfile] = None
    timezone: str  # User timezone (e.g., "America/New_York", "Asia/Jerusalem")
    locale: str  # User locale (e.g., "en-US", "he-IL")
    images: Optional[List[Dict[str, Any]]] = None  # Image file data for multimodal messages
    system_prompt: Optional[str] = None  # Custom system prompt (defaults to template from prompts service)
    tools: Optional[List[str]] = None  # Optional list of tool identifiers for cache key


class CommitRequest(BaseModel):
    """Request model for resuming paused run with confirmed/edited tools"""

    run_id: str
    stream: bool = True
    model: Model = DEFAULT_MODEL  # see ChatRequest
    user_id: str
    session_id: str
    updated_tools: List[Dict[str, Any]]
    user_profile: Optional[UserProfile] = None


class ChatResponse(BaseModel):
    """Chat response model for v2 API"""

    content: Optional[str] = None
    agent_id: str
    session_id: Optional[str] = None
    model: Model = DEFAULT_MODEL
    token_usage: Optional[dict] = None
    status: Optional[str] = None
    run_id: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None


class AgentConfigRequest(BaseModel):
    """Agent configuration options for creation/update."""

    # Memory settings
    enable_memory: bool = True  # Controls MemoryManager creation

    # History settings
    enable_history: bool = True  # Controls read_chat_history + add_history_to_context
    num_history_runs: int = 3  # Number of history runs to include

    # Reasoning settings (Agno native reasoning support)
    enable_reasoning: bool = False  # Maps to Agent(reasoning=True)
    reasoning_min_steps: int = 1  # Minimum reasoning steps
    reasoning_max_steps: int = 10  # Maximum reasoning steps

    # Supervisor worker settings (optional)
    worker_config: Optional[Dict[str, Any]] = None


class CreateAgentRequest(BaseModel):
    """Request model for creating a new agent"""

    id: str = Field(..., max_length=255)
    name: str = Field(..., max_length=255)
    template: str
    description: Optional[str] = Field(default=None, max_length=1000)
    tags: Optional[List[str]] = None
    config: Optional[AgentConfigRequest] = None  # Agent configuration


class CreateAgentResponse(BaseModel):
    """Response model for agent creation"""

    id: str
    name: str
    description: Optional[str] = None
    version: str = "2.0"
    message: str
    tags: Optional[List[str]] = None


class DeleteAgentResponse(BaseModel):
    """Response model for agent deletion"""

    id: str
    message: str


class ToolkitExecutionResponse(BaseModel):
    """Response model for direct toolkit execution"""

    status: str
    message: str
    result: Optional[Dict[str, Any]] = None


class ToolkitConfirmRequest(BaseModel):
    """Request model for toolkit confirmation"""

    execution_id: str
    toolkit_name: str
    method_name: str
    confirmed: Optional[bool] = None
    confirmation_note: Optional[str] = None
    args: Optional[Dict[str, Any]] = None


class ClearSessionRequest(BaseModel):
    """Request model for clearing agent session"""

    message: str = ""  # Can be empty
    user_id: Optional[str] = None
    session_id: Optional[str] = None


class ClearSessionResponse(BaseModel):
    """Response model for clearing agent session"""

    status: str
    message: str


async def commit_response_streamer_v2(
    agent: Agent, body: CommitRequest, cached_run: Any, db: Session = Depends(get_db)
) -> AsyncGenerator:
    """
    Stream agent responses for commit/continue run chunk by chunk.

    Args:
        agent: The agent instance to interact with
        body: CommitRequest with run_id and updated_tools
        cached_run: Cached run output with updated tools
        db: Database session

    Yields:
        SSE-formatted event strings with agent response chunks including status, run_id, and tools
    """
    logging.debug(f"Starting v2 streaming commit response for run_id: {body.run_id}")

    # agno 3 resumes from `requirements` and ignores `updated_tools` - see agents/hitl.py.
    run_response = agent.acontinue_run(
        run_id=body.run_id,
        requirements=requirements_for(cached_run, cached_run.tools),
        stream=True,
    )  # type: ignore[misc]
    chunk_count = 0
    full_output_text = ""
    last_metrics = None

    try:
        async for chunk in run_response:
            chunk_count += 1
            if chunk_count % 10 == 0:  # Log every 10 chunks to avoid excessive logging
                logging.debug(f"V2 streaming commit chunk #{chunk_count}")

            # Convert chunk to dict
            event_data = _event_to_dict(chunk)

            # Accumulate text and keep track of the latest metrics
            if "content" in event_data:
                # Only text content is part of the answer. Reasoning (and some other)
                # events can carry a dict `content`; str-concatenating those raised
                # "can only concatenate str (not dict) to str" and aborted the stream.
                if isinstance(event_data["content"], str):
                    full_output_text += event_data["content"]
                if "metrics" in event_data:
                    last_metrics = Metrics(**event_data["metrics"])

            # Process tools using shared helper function
            _process_tools_in_event(event_data)

            yield _format_sse_event("message", event_data)

    except Exception as e:
        # Log the error and send error event to client
        logging.error(f"Error during streaming commit response: {type(e).__name__}: {str(e)}")
        error_data = {
            "status": "error",
            "error": f"Streaming error: {type(e).__name__}: {str(e)}",
            "content": full_output_text if full_output_text else None,
        }
        yield _format_sse_event("error", error_data)
    finally:
        # Store token usage after all chunks are processed (or on error)
        if full_output_text or last_metrics:
            store_token_usage(
                agent=agent,
                input_text="[commit continuation]",
                output_text=full_output_text,
                metrics=last_metrics,
                db=db,
            )
        logging.debug(f"Completed v2 streaming commit response with {chunk_count} chunks")

        # Clean up run cache
        if body.run_id in _run_cache:
            del _run_cache[body.run_id]


async def chat_response_streamer_v2(agent: Agent, body: ChatRequest, db: Session = Depends(get_db)) -> AsyncGenerator:
    """
    Stream agent responses chunk by chunk (v2 implementation).

    Args:
        agent: The agent instance to interact with
        body: User message to process
        db: Database session

    Yields:
        SSE-formatted event strings with agent response chunks including status, run_id, and tools
    """
    logging.debug(f"Starting v2 streaming response for message: {body.message[:50]}...")

    # Build knowledge_filters safely (handle None tenant_profile)
    # Prefix with "meta_data." since Qdrant stores metadata as nested field
    # DISABLE knowledge search for multimodal messages to reduce token count
    knowledge_filters = {"meta_data.tenant_id": body.tenant_profile.tenant_id} if body.tenant_profile else None

    # Build session_state safely (handle None attributes)
    session_state = {
        "user_name": body.user_profile.full_name if body.user_profile else None,
        "user_profile_json": body.user_profile.model_dump_json() if body.user_profile else None,
        "timezone": body.timezone,
        "locale": body.locale,
    }

    # Build multimodal message if images are provided
    image_objects = []
    file_objects = []
    if body.images:
        import base64
        import hashlib

        from agno.media import File

        for img_data in body.images:
            # Decode base64 content from client
            content_bytes = base64.b64decode(img_data["content"])
            mime_type = img_data.get("mime_type", "")

            # Verify data
            img_hash = hashlib.md5(content_bytes).hexdigest()
            img_size_mb = len(content_bytes) / (1024 * 1024)
            first_bytes = content_bytes[:20].hex()

            logging.info(f"[STREAMING] File data: size={img_size_mb:.2f}MB, hash={img_hash}, first_bytes={first_bytes}")
            logging.info(f"[STREAMING] File mime_type={mime_type}")

            # Check if it's an image or PDF and create appropriate object with raw bytes
            if mime_type.startswith("image/"):
                # Create Image object with raw bytes
                img_obj = Image(content=content_bytes, mime_type=mime_type)
                image_objects.append(img_obj)
                logging.info(f"[STREAMING] Created Image object with raw bytes (size={img_size_mb:.2f}MB)")
            elif mime_type == "application/pdf":
                # Create File object with raw bytes
                file_obj = File(content=content_bytes, mime_type=mime_type)
                file_objects.append(file_obj)
                logging.info(f"[STREAMING] Created File object with raw bytes (size={img_size_mb:.2f}MB)")

        logging.debug(f"Built multimodal content with {len(image_objects)} images and {len(file_objects)} files")

    run_config = {
        "knowledge_filters": knowledge_filters,
        "session_state": session_state,
    }

    run_response = agent.arun(
        body.message,
        images=image_objects if image_objects else None,
        files=file_objects if file_objects else None,
        stream=True,
        **run_config,
    )  # type: ignore[call-overload]
    chunk_count = 0
    full_output_text = ""
    last_metrics = None

    try:
        async for chunk in run_response:
            chunk_count += 1
            if chunk_count % 10 == 0:  # Log every 10 chunks to avoid excessive logging
                logging.debug(f"V2 streaming chunk #{chunk_count}")

            # Convert chunk to dict
            event_data = _event_to_dict(chunk)

            # Accumulate text and keep track of the latest metrics
            if "content" in event_data:
                # Only text content is part of the answer. Reasoning (and some other)
                # events can carry a dict `content`; str-concatenating those raised
                # "can only concatenate str (not dict) to str" and aborted the stream.
                if isinstance(event_data["content"], str):
                    full_output_text += event_data["content"]
                if "metrics" in event_data:
                    last_metrics = Metrics(**event_data["metrics"])

            # Cache the run for later commit if run_id is present
            if "run_id" in event_data and event_data["run_id"]:
                _run_cache[event_data["run_id"]] = chunk

            # Process tools using shared helper function
            _process_tools_in_event(event_data)

            yield _format_sse_event("message", event_data)

    except Exception as e:
        # Log the error and send error event to client
        logging.error(f"Error during streaming response: {type(e).__name__}: {str(e)}")
        error_data = {
            "status": "error",
            "error": f"Streaming error: {type(e).__name__}: {str(e)}",
            "content": full_output_text if full_output_text else None,
        }
        yield _format_sse_event("error", error_data)
    finally:
        # Store token usage after all chunks are processed (or on error)
        if full_output_text or last_metrics:
            store_token_usage(
                agent=agent, input_text=body.message, output_text=full_output_text, metrics=last_metrics, db=db
            )
        logging.debug(f"Completed v2 streaming response with {chunk_count} chunks")


async def _mcp_chat_streamer(build_agent_fn, mcp_toolkits, body: "ChatRequest", db: Session) -> AsyncGenerator:
    """Stream an MCP-tool agent: connect the MCP session(s), build the agent with the
    connected toolkits, then delegate to chat_response_streamer_v2. The AsyncExitStack
    stays open for the whole stream because this generator runs after the route returns.
    """
    async with AsyncExitStack() as stack:
        try:
            for toolkit in mcp_toolkits:
                await stack.enter_async_context(toolkit)
            ensure_mcp_ready(mcp_toolkits)
            agent = build_agent_fn(mcp_toolkits)
        except Exception:
            # The stream's status (200) is already sent, so surface a structured SSE
            # error event (the route's try/except can't catch failures here — this
            # generator runs after the route frame exits). Log the detail server-side
            # only; the client gets a generic message (no exception data leaked).
            logging.error("MCP connect failed (streaming)", exc_info=True)
            yield _format_sse_event(
                "error",
                {"status": "error", "error": "MCP tool server unavailable"},
            )
            return
        async for event in chat_response_streamer_v2(agent, body, db):
            yield event


async def get_agent(
    db: Session, agent_id: str, model: Model, user_id: str, session_id: str
) -> tuple[Optional[Agent], PullPromptResponse, str, AgentConfig]:
    # Validate agent exists in database first
    db_agent = get_agent_info(db, agent_id)
    if not db_agent:
        logging.error(f"Agent {agent_id} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent {agent_id} not found")
    # Fetch prompt template, respecting PROMPT_STORAGE_BACKEND (mirrors
    # agents.v2_selector.get_v2_agent): use local postgres storage when that's
    # the backend, with a prompts-service fallback otherwise. Previously this
    # always hit the prompts service client, which 500s whenever the prompts
    # microservice isn't configured -- even with the documented postgres backend.
    prompt_data: Optional[PullPromptResponse] = None
    if os.environ.get("PROMPT_STORAGE_BACKEND", "postgres").lower() == "postgres":
        prompt_data = _get_prompt_from_local_storage(db, str(db_agent.prompt_service_id))
    else:
        prompt_data = prompts_client.get_prompt(db_agent.prompt_service_id)  # type: ignore[arg-type]
        if not prompt_data:
            prompt_data = _get_prompt_from_local_storage(db, str(db_agent.prompt_service_id))
    if not prompt_data:
        logging.error(f"Failed to fetch prompt for agent {agent_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch prompt template for agent {agent_id}",
        )
    # Compute CRC32 cache key
    cache_key = compute_cache_key(prompt_data.template, agent_id, model, user_id, session_id)
    logging.debug(f"Computed cache key for agent {_log_safe(agent_id)}: {_log_safe(cache_key)}")
    # Get agent config from database
    agent_config = get_agent_config(db_agent)
    # Get or create cached agent
    agent: Optional[Agent] = None
    with _cache_lock:
        if cache_key in _agent_cache:
            logging.debug(f"Using cached agent for {_log_safe(cache_key)}")
            agent = _agent_cache[cache_key]
    return agent, prompt_data, cache_key, agent_config


@v2_agents_router.post("/{agent_id}/chat", status_code=status.HTTP_200_OK)
async def chat_with_agent_v2(agent_id: str, body: ChatRequest, db: Session = Depends(get_db)):
    """
    Chat with a specific agent using the v2 API with toolkit support.

    Args:
        agent_id: The ID of the agent to chat with
        body: Chat request parameters with optional user_profile and org_profile
        db: Database session

    Returns:
        Either a streaming response or a complete ChatResponse with optional paused status
    """
    try:
        logging.info(f"Creating v2 chat request for agent_id: {agent_id}")
        logging.debug(f"ChatRequest: {body}")

        # Get or create cached agent (same for all messages including multimodal)
        agent, prompt_data, cache_key, agent_config = await get_agent(
            db, agent_id, body.model, body.user_id, body.session_id
        )

        # MCP-tool agents (config.worker_config.mcp_servers): build a fresh agent per
        # request and run it inside the live MCP session(s). We bypass the agent cache
        # because the agno MCP toolkit holds a connection that isn't safe to reuse across
        # requests. Non-MCP agents fall through to the cached path below, unchanged.
        mcp_toolkits = build_mcp_toolkits(agent_config)
        if mcp_toolkits:

            def _build_mcp_agent(connected_toolkits):
                return get_agent_impl(
                    prompt=prompt_data,
                    model_id=body.model.value,
                    user_id=body.user_id,
                    session_id=body.session_id,
                    organizer_email=body.user_profile.email if body.user_profile else "",
                    fetch_token_func=fetch_access_token,
                    tenant_id=body.tenant_profile.tenant_id if body.tenant_profile else "default_tenant",
                    timezone=body.timezone,
                    config=agent_config,
                    extra_tools=connected_toolkits,
                )

            if body.stream:
                logging.info(f"Returning v2 streaming response (MCP) for agent: {agent_id}")
                return StreamingResponse(
                    _mcp_chat_streamer(_build_mcp_agent, mcp_toolkits, body, db),
                    media_type="text/event-stream",
                )

            logging.info(f"Processing v2 non-streaming request (MCP) for agent: {agent_id}")
            async with AsyncExitStack() as stack:
                try:
                    for toolkit in mcp_toolkits:
                        await stack.enter_async_context(toolkit)
                    ensure_mcp_ready(mcp_toolkits)
                except Exception:
                    logging.error("MCP connect failed (non-streaming)", exc_info=True)
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="MCP tool server unavailable",
                    )
                mcp_agent = _build_mcp_agent(mcp_toolkits)
                run_config = {
                    "knowledge_filters": (
                        {"meta_data.tenant_id": body.tenant_profile.tenant_id} if body.tenant_profile else None
                    ),
                    "session_state": {
                        "user_name": body.user_profile.full_name if body.user_profile else None,
                        "user_profile_json": body.user_profile.model_dump_json() if body.user_profile else None,
                        "timezone": body.timezone,
                        "locale": body.locale,
                    },
                }
                response = await mcp_agent.arun(body.message, stream=False, **run_config)
                if hasattr(response, "run_id") and response.run_id:
                    _run_cache[response.run_id] = response
                tools_dict = []
                if hasattr(response, "tools") and response.tools:
                    for tool in response.tools:
                        tools_dict.append(
                            {
                                "tool_call_id": getattr(tool, "tool_call_id", None),
                                "tool_name": getattr(tool, "tool_name", None),
                                "requires_confirmation": getattr(tool, "requires_confirmation", False),
                                "tool_args": getattr(tool, "tool_args", None),
                                "result": _parse_result(getattr(tool, "result", None)),
                            }
                        )
                if hasattr(response, "content"):
                    store_token_usage(
                        agent=mcp_agent,
                        input_text=body.message,
                        output_text=response.content,
                        metrics=response.metrics if hasattr(response, "metrics") else None,
                        db=db,
                    )
                return ChatResponse(
                    content=response.content if hasattr(response, "content") else None,
                    agent_id=agent_id,
                    session_id=body.session_id,
                    model=body.model,
                    status=response.status,
                    run_id=response.run_id if hasattr(response, "run_id") else None,
                    tools=tools_dict,
                )

        if not agent:
            logging.info(f"Creating new agent for {_log_safe(cache_key)}")

            agent = get_agent_impl(
                prompt=prompt_data,
                model_id=body.model.value,
                user_id=body.user_id,
                session_id=body.session_id,
                organizer_email=body.user_profile.email if body.user_profile else "",
                fetch_token_func=fetch_access_token,
                tenant_id=body.tenant_profile.tenant_id if body.tenant_profile else "default_tenant",
                timezone=body.timezone,
                config=agent_config,
            )
            _agent_cache[cache_key] = agent
            logging.info(f"Agent cached with key: {_log_safe(cache_key)}")

        # Execute agent run
        if body.stream:
            logging.info(f"Returning v2 streaming response for agent: {agent_id}")
            return StreamingResponse(
                chat_response_streamer_v2(agent, body, db),
                media_type="text/event-stream",
            )
        else:
            logging.info(f"Processing v2 non-streaming request for agent: {agent_id}")

            # Build knowledge_filters safely (handle None tenant_profile)
            # Prefix with "meta_data." since Qdrant stores metadata as nested field
            # DISABLE knowledge search for multimodal messages to reduce token count
            knowledge_filters = {"meta_data.tenant_id": body.tenant_profile.tenant_id} if body.tenant_profile else None

            # Build session_state safely (handle None attributes)
            session_state = {
                "user_name": body.user_profile.full_name if body.user_profile else None,
                "user_profile_json": body.user_profile.model_dump_json() if body.user_profile else None,
                "timezone": body.timezone,
                "locale": body.locale,
            }

            # Build multimodal message if images are provided
            image_objects = []
            file_objects = []
            if body.images:
                import base64
                import hashlib

                from agno.media import File

                for img_data in body.images:
                    # Decode base64 content from client
                    content_bytes = base64.b64decode(img_data["content"])
                    mime_type = img_data.get("mime_type", "")

                    # Verify data
                    img_hash = hashlib.md5(content_bytes).hexdigest()
                    img_size_mb = len(content_bytes) / (1024 * 1024)
                    first_bytes = content_bytes[:20].hex()

                    logging.info(f"File data: size={img_size_mb:.2f}MB, hash={img_hash}, first_bytes={first_bytes}")
                    logging.info(f"File mime_type={mime_type}")

                    # Check if it's an image or PDF and create appropriate object with raw bytes
                    if mime_type.startswith("image/"):
                        # Create Image object with raw bytes
                        img_obj = Image(content=content_bytes, mime_type=mime_type)
                        image_objects.append(img_obj)
                        logging.info(f"Created Image object with raw bytes (size={img_size_mb:.2f}MB)")
                    elif mime_type == "application/pdf":
                        # Create File object with raw bytes
                        file_obj = File(content=content_bytes, mime_type=mime_type)
                        file_objects.append(file_obj)
                        logging.info(f"Created File object with raw bytes (size={img_size_mb:.2f}MB)")

                logging.debug(
                    f"Built multimodal content with {len(image_objects)} images and {len(file_objects)} files (non-streaming)"
                )

            run_config = {
                "knowledge_filters": knowledge_filters,
                "session_state": session_state,
            }

            response = await agent.arun(
                body.message,
                images=image_objects if image_objects else None,
                files=file_objects if file_objects else None,
                stream=False,
                **run_config,
            )  # type: ignore[call-overload]
            logging.debug(f"Completed v2 non-streaming request for agent: {agent_id}")

            # Cache the run for later commit
            if hasattr(response, "run_id") and response.run_id:
                _run_cache[response.run_id] = response

            # Convert tools to dict format
            tools_dict = []
            if hasattr(response, "tools") and response.tools:
                for tool in response.tools:
                    tool_dict = {
                        "tool_call_id": getattr(tool, "tool_call_id", None),
                        "tool_name": getattr(tool, "tool_name", None),
                        "requires_confirmation": getattr(tool, "requires_confirmation", False),
                        "tool_args": getattr(tool, "tool_args", None),
                        "result": _parse_result(getattr(tool, "result", None)),
                    }
                    tools_dict.append(tool_dict)

            # Log and store token usage information
            if hasattr(response, "content"):
                metrics = response.metrics if hasattr(response, "metrics") else None
                store_token_usage(
                    agent=agent,
                    input_text=body.message,
                    output_text=response.content,  # type: ignore[arg-type]
                    metrics=metrics,  # type: ignore[arg-type]
                    db=db,
                )

            return ChatResponse(
                content=response.content if hasattr(response, "content") else None,  # type: ignore[arg-type]
                agent_id=agent_id,
                session_id=body.session_id,
                model=body.model,
                status=response.status,
                run_id=response.run_id if hasattr(response, "run_id") else None,  # type: ignore[arg-type]
                tools=tools_dict,
            )

    except HTTPException:
        # Re-raise HTTP exceptions to maintain proper error responses
        raise
    except ProviderNotConfiguredError as e:
        # The model is valid but this deployment has no key for its provider -
        # say which, rather than a 500 that reads like a gateway bug.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except Exception as e:
        logging.exception(f"Unexpected error in chat_with_agent_v2 for agent {agent_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error occurred while chatting with agent {agent_id}",
        )


@v2_agents_router.post("", response_model=CreateAgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_v2(body: CreateAgentRequest, db: Session = Depends(get_db)):
    """
    Create a new agent with the provided configuration in both prompts service and database.

    Args:
        body: Agent creation request containing id, name, template, and optional fields
        db: Database session

    Returns:
        CreateAgentResponse: Confirmation of agent creation with details
    """
    logging.info(f"Request to create new agent: {body.id}")

    # Check if agent already exists in database
    if agent_info_exists(db, body.id):
        logging.error(f"Agent {body.id} already exists in database")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Agent {body.id} already exists")

    # Validate agent ID format (alphanumeric and underscores only)
    if not body.id.replace("_", "").replace("-", "").isalnum():
        logging.error(f"Invalid agent ID format: {body.id}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Agent ID can only contain letters, numbers, underscores, and hyphens",
        )

    try:
        # Create prompt using the configured storage backend (postgres, langsmith, or service)
        prompt_storage = get_prompt_storage(db)
        prompt_id = slug_to_table_name(body.name)

        try:
            # Hard delete existing prompt if it exists (cleanup from previous agent with same name)
            if hasattr(prompt_storage, "exists") and prompt_storage.exists(prompt_id):
                logging.info(f"Prompt '{prompt_id}' already exists, hard deleting before recreate")
                if hasattr(prompt_storage, "hard_delete"):
                    prompt_storage.hard_delete(prompt_id)
                else:
                    prompt_storage.delete(prompt_id)

            prompt_storage.create(
                prompt_id=prompt_id,
                name=body.name,
                template=body.template,
                description=body.description,
                tags=body.tags,
            )
            logging.info(f"Created prompt '{prompt_id}' in storage backend")
        except Exception as storage_error:
            logging.error(f"Failed to create agent {body.id} in storage backend: {storage_error}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create agent {body.id} in storage backend: {storage_error}",
            )

        # Create AgentInfo record in database
        try:
            # Convert API config to database config model if provided
            agent_config = None
            if body.config:
                config_kwargs: Dict[str, Any] = {
                    "enable_memory": body.config.enable_memory,
                    "enable_history": body.config.enable_history,
                    "num_history_runs": body.config.num_history_runs,
                    "enable_reasoning": body.config.enable_reasoning,
                    "reasoning_min_steps": body.config.reasoning_min_steps,
                    "reasoning_max_steps": body.config.reasoning_max_steps,
                }
                if body.config.worker_config:
                    from supervisor.models import WorkerConfig

                    config_kwargs["worker_config"] = WorkerConfig(**body.config.worker_config)
                agent_config = AgentConfig(**config_kwargs)

            create_agent_info(
                db=db,
                agent_id=body.id,
                name=body.name,
                prompt_service_id=slug_to_table_name(body.name),
                description=body.description,
                tags=body.tags,
                version="2.0",
                config=agent_config,
            )
            logging.info(f"Agent {body.id} created successfully in database and storage backend")
        except Exception as db_error:
            # Rollback: Try to delete from storage backend if database creation fails
            logging.error(f"Failed to create agent {body.id} in database: {db_error}")
            try:
                prompt_storage.delete(prompt_id)
                logging.info(f"Rolled back agent {body.id} from storage backend")
            except Exception as rollback_error:
                logging.error(f"Failed to rollback agent {body.id} from storage backend: {rollback_error}")

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create agent {body.id} in database",
            )

        response = CreateAgentResponse(
            id=body.id,
            name=body.name,
            description=body.description,
            version="2.0",
            message=f"Agent {body.name} created successfully",
            tags=body.tags,
        )

        return response

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logging.exception(f"Error creating agent {body.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create agent {body.id}: {str(e)}"
        )


@v2_agents_router.delete("/{agent_id}", response_model=DeleteAgentResponse, status_code=status.HTTP_200_OK)
async def delete_agent_v2(agent_id: str, db: Session = Depends(get_db)):
    """
    Delete an existing agent from both database and prompts service.

    Args:
        agent_id: The ID of the agent to delete
        db: Database session

    Returns:
        DeleteAgentResponse: Confirmation of agent deletion
    """
    logging.info(f"Request to delete agent: {agent_id}")

    # Check if agent exists in database
    db_agent = get_agent_info(db, agent_id)
    if not db_agent:
        logging.error(f"Agent {agent_id} not found in database")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent {agent_id} not found")

    try:
        # Soft delete from database first
        success = soft_delete_agent_info(db, agent_id)
        if not success:
            logging.error(f"Failed to soft delete agent {agent_id} from database")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete agent {agent_id} from database",
            )

        # Delete from prompts service
        try:
            prompts_success = prompts_client.delete_prompt(db_agent.prompt_service_id)  # type: ignore[arg-type]
            if not prompts_success:
                logging.warning(
                    f"Failed to delete agent {agent_id} from prompts service, but database deletion succeeded"
                )
            else:
                logging.info(f"Agent {agent_id} deleted successfully from prompts service")
        except Exception as prompts_error:
            logging.warning(
                f"Error deleting agent {agent_id} from prompts service: {prompts_error}, but database deletion succeeded"
            )

        logging.info(f"Agent {agent_id} deleted successfully from database")

        response = DeleteAgentResponse(id=agent_id, message=f"Agent {agent_id} deleted successfully")

        return response

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logging.exception(f"Error deleting agent {agent_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete agent {agent_id}: {str(e)}"
        )


@v2_agents_router.post("/{agent_id}/chat/commit", status_code=status.HTTP_200_OK)
async def commit_agent_chat_v2(agent_id: str, body: CommitRequest, db: Session = Depends(get_db)):
    """
    Resume a paused agent run with confirmed/edited tools.

    Args:
        agent_id: The ID of the agent
        body: CommitRequest with run_id and updated_tools
        db: Database session

    Returns:
        ChatResponse with continued execution results
    """
    try:
        logging.info(f"Commit request for agent {agent_id}, run_id: {body.run_id}")

        # Check if any tool has confirmed=false (user denial)
        # Only consider tools that have an explicit confirmed field (ignore None/missing)
        tools_with_confirmation = [tool for tool in body.updated_tools if tool.get("confirmed") is not None]
        all_denied = len(tools_with_confirmation) > 0 and all(
            tool.get("confirmed") is False for tool in tools_with_confirmation
        )

        # Retrieve cached run
        if body.run_id not in _run_cache:
            logging.error(f"Run ID {body.run_id} not found in cache")
            # If all tools are denied and run_id not found, return denial response without error
            if all_denied:
                logging.info(f"Run ID {body.run_id} not found but all tools denied - returning denial response")
                return ChatResponse(
                    content="Tool execution cancelled by user.",
                    agent_id=agent_id,
                    session_id=body.session_id,
                    model=body.model,
                    status="cancelled",
                )
            # Otherwise, the confirmation time window has elapsed
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Confirmation time window has elapsed. The run is no longer available for resumption.",
            )

        cached_run = _run_cache[body.run_id]

        # Check if user denied all tools (confirmed=false)
        if all_denied:
            logging.info(f"User denied all tools for run_id: {body.run_id}")
            # Clean up run cache
            if body.run_id in _run_cache:
                del _run_cache[body.run_id]
            return ChatResponse(
                content="Tool execution cancelled by user.",
                agent_id=agent_id,
                session_id=body.session_id,
                model=body.model,
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

        # Find agent in cache.
        agent, _, _, _ = await get_agent(db, agent_id, body.model, body.user_id, body.session_id)
        if not agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent {agent_id} not found in cache")

        # Continue the run with updated tools
        if body.stream:
            logging.info(f"Returning v2 streaming response for commit: {body.run_id}")
            return StreamingResponse(
                commit_response_streamer_v2(agent, body, cached_run, db),
                media_type="text/event-stream",
            )
        else:
            response = await agent.acontinue_run(
                run_id=body.run_id, requirements=requirements_for(cached_run, cached_run.tools), stream=False
            )
            logging.debug(f"Completed commit request for agent: {agent_id}")

            # Extract token usage metrics
            token_usage = None
            if hasattr(response, "metrics") and response.metrics:
                token_usage = response.metrics.to_dict()

            # Log and store token usage information
            if hasattr(response, "content"):
                metrics = response.metrics if hasattr(response, "metrics") else None
                store_token_usage(
                    agent=agent,
                    input_text="[commit continuation]",
                    output_text=response.content,  # type: ignore[arg-type]
                    metrics=metrics,  # type: ignore[arg-type]
                    db=db,
                )

            # Clean up run cache
            if body.run_id in _run_cache:
                del _run_cache[body.run_id]

            return ChatResponse(
                content=response.content,  # type: ignore[arg-type]
                agent_id=agent_id,
                session_id=getattr(agent, "session_id", None),
                model=body.model,
                token_usage=token_usage,  # type: ignore[arg-type]
                status="completed",
            )

    except HTTPException:
        raise
    except ProviderNotConfiguredError as e:
        # The model is valid but this deployment has no key for its provider -
        # say which, rather than a 500 that reads like a gateway bug.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except Exception as e:
        logging.exception(f"Unexpected error in commit_agent_chat_v2 for agent {agent_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error occurred while committing chat with agent {agent_id}",
        )


# ---------------------------
# Toolkit Execution Helpers
# ---------------------------
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


def _parse_query_params(request: Request) -> Dict[str, Any]:
    """
    Parse query parameters from request, handling multi-value parameters.

    Args:
        request: FastAPI Request object

    Returns:
        Dictionary of parsed parameters with multi-value params as lists
    """
    params: Dict[str, Any] = {}
    query_params = request.query_params

    # Get all keys including duplicates
    for key in query_params.keys():
        values = query_params.getlist(key)
        if len(values) == 1:
            params[key] = values[0]
        else:
            params[key] = values

    return params


def _convert_param_types(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert string query parameters to appropriate Python types.

    Handles:
    - Boolean strings: "true"/"false" -> True/False
    - Integer strings: "42" -> 42
    - List values remain as lists
    - Special handling for attendees to ensure it's always a list

    Args:
        params: Dictionary of string parameters

    Returns:
        Dictionary with converted types
    """
    converted: Dict[str, Any] = {}

    for key, value in params.items():
        # Handle lists (multi-value params like attendees)
        if isinstance(value, list):
            converted[key] = value
            continue

        # Special handling for attendees - always convert to list even if single value
        if key == "attendees" and isinstance(value, str):
            converted[key] = [value]
            continue

        # Handle boolean strings
        if isinstance(value, str):
            if value.lower() == "true":
                converted[key] = True
            elif value.lower() == "false":
                converted[key] = False
            # Try to convert to int for numeric params like duration_minutes
            elif value.isdigit():
                converted[key] = int(value)
            else:
                converted[key] = value
        else:
            converted[key] = value

    return converted


@v2_agents_router.get("/{agent_id}/toolkit/run", response_model=ToolkitExecutionResponse | ToolkitConfirmRequest)
async def run_toolkit_method_v2(
    request: Request,
    agent_id: str,
    toolkit_name: str,
    method_name: str,
    user_id: str,
    session_id: str,
    organizer_email: Optional[str] = None,
    model: Model = DEFAULT_MODEL,
    skip_confirmation: bool = False,
    db: Session = Depends(get_db),
):
    """
    Direct toolkit method execution endpoint.

    Args:
        agent_id: The ID of the agent
        toolkit_name: Name of the toolkit (e.g., "CalendarToolkit")
        method_name: Method name to execute (e.g., "cancel_meeting")
        user_id: User identifier
        session_id: Session identifier
        organizer_email: Optional organizer email
        model: The model identifier
        skip_confirmation: Whether to skip confirmation requirement
        db: Database session

    Returns:
        ToolkitExecutionResponse with execution results
    """
    try:
        logging.info(f"Toolkit execution request: {toolkit_name}.{method_name} for agent {agent_id}")

        # Parse query parameters
        raw_params = _parse_query_params(request)
        # Remove toolkit parameters (they're not method arguments)
        raw_params.pop("toolkit_name", None)
        raw_params.pop("method_name", None)
        raw_params.pop("user_id", None)
        raw_params.pop("session_id", None)
        raw_params.pop("organizer_email", None)
        raw_params.pop("model", None)
        raw_params.pop("skip_confirmation", None)

        # Convert types (booleans, integers, lists)
        method_kwargs = _convert_param_types(raw_params)
        logging.info(f"[toolkit/run] Method kwargs: {method_kwargs}")

        # Get agent from cache
        agent, _, _, _ = await get_agent(db, agent_id, model, user_id, session_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Find toolkit in agent.tools
        if not agent.tools:
            raise HTTPException(
                status_code=404,
                detail="Agent has no tools configured",
            )

        toolkit = None
        tools_list = agent.tools if isinstance(agent.tools, list) else []
        for tool_item in tools_list:
            # Check if tool is a Toolkit instance and matches the requested name
            if hasattr(tool_item, "__class__") and tool_item.__class__.__name__ == toolkit_name:
                toolkit = tool_item
                break

        if toolkit is None:
            raise HTTPException(
                status_code=404,
                detail=f"Toolkit '{toolkit_name}' not found in agent tools. Available: {[t.__class__.__name__ for t in tools_list]}",
            )

        # Check if method exists
        if not hasattr(toolkit, method_name):
            raise HTTPException(
                status_code=404,
                detail=f"Method '{method_name}' not found in toolkit '{toolkit_name}'. Available methods: {[m for m in dir(toolkit) if not m.startswith('_')]}",
            )

        # Check if method requires confirmation
        requires_confirmation = False
        if hasattr(toolkit, "requires_confirmation_tools"):
            requires_confirmation = method_name in toolkit.requires_confirmation_tools

        # Override confirmation requirement if skip_confirmation flag is set
        if skip_confirmation:
            logging.info(f"[toolkit/run] Skipping confirmation for {method_name} due to skip_confirmation flag")
            requires_confirmation = False

        logging.info(f"[toolkit/run] Method {method_name} requires_confirmation={requires_confirmation}")

        # If confirmation required, cache execution context
        if requires_confirmation:
            execution_id = str(uuid.uuid4())

            with _toolkit_execution_lock:
                _toolkit_execution_cache[execution_id] = {
                    "toolkit": toolkit,
                    "method_name": method_name,
                    "method_kwargs": method_kwargs,
                    "user_id": user_id,
                    "session_id": session_id,
                    "organizer_email": organizer_email,
                    "timestamp": time.time(),
                }

            logging.info(f"[toolkit/run] Cached execution {execution_id} for confirmation")

            return ToolkitConfirmRequest(
                execution_id=execution_id, toolkit_name=toolkit_name, method_name=method_name, args=method_kwargs
            )

        method = getattr(toolkit, method_name)
        result = method(**method_kwargs)
        logging.info(f"[toolkit/run] Method {method_name} executed successfully")
        return ToolkitExecutionResponse(
            status="success", message=f"[toolkit/run] Method {method_name} executed successfully", result=result
        )

    except Exception as e:
        logging.exception(f"Error executing toolkit method: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute toolkit method: {str(e)}",
        )


@v2_agents_router.post("/{agent_id}/toolkit/confirm", response_model=ToolkitExecutionResponse)
async def confirm_toolkit_method_v2(agent_id: str, body: ToolkitConfirmRequest, db: Session = Depends(get_db)):
    """
    Confirm toolkit method execution.

    Args:
        agent_id: The ID of the agent
        body: ToolkitConfirmRequest with confirmation details
        db: Database session

    Returns:
        ToolkitExecutionResponse with confirmation results
    """
    try:
        logging.info(f"Toolkit confirmation request: {body.toolkit_name}.{body.method_name} for agent {agent_id}")

        # Retrieve cached execution
        with _toolkit_execution_lock:
            execution = _toolkit_execution_cache.get(body.execution_id)

        if execution is None:
            raise HTTPException(
                status_code=404,
                detail=f"Execution {body.execution_id} not found in cache. It may have expired or been already executed.",
            )

        # Extract cached data
        toolkit = execution["toolkit"]
        method_name = execution["method_name"]
        method_kwargs = execution["method_kwargs"].copy()  # Copy to avoid modifying cache
        timestamp = execution.get("timestamp", 0)

        # Check if execution has expired (e.g., 1 hour TTL)
        ttl_seconds = 3600  # 1 hour
        if time.time() - timestamp > ttl_seconds:
            with _toolkit_execution_lock:
                _toolkit_execution_cache.pop(body.execution_id, None)
            raise HTTPException(
                status_code=410,
                detail=f"Execution {body.execution_id} has expired. Please initiate a new execution.",
            )

        # Update parameters if provided
        if body.args:
            logging.info(f"[toolkit/confirm] Updating args: {body.args}")
            method_kwargs.update(body.args)

        # Execute the method
        method = getattr(toolkit, method_name)
        result = method(**method_kwargs)
        logging.info(f"[toolkit/confirm] Method {method_name} executed successfully")

        # Remove from cache on success
        with _toolkit_execution_lock:
            _toolkit_execution_cache.pop(body.execution_id, None)

        return ToolkitExecutionResponse(
            status="status", message=f"[toolkit/confirm] Method {method_name} executed successfully", result=result
        )

    except Exception as e:
        logging.exception(f"Error confirming toolkit method: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to confirm toolkit method: {str(e)}",
        )


@v2_agents_router.delete("/{agent_id}/cache", status_code=status.HTTP_200_OK)
async def clear_agent_cache_v2(agent_id: str, user_id: Optional[str] = None, session_id: Optional[str] = None):
    """
    Clear agent cache for a specific agent or all agents.

    Args:
        agent_id: The ID of the agent (or "*" for all agents)
        user_id: Optional user ID filter
        session_id: Optional session ID filter

    Returns:
        dict with status and message
    """
    try:
        logging.info(f"Cache clear request for agent: {agent_id}")

        with _cache_lock:
            if agent_id == "*":
                # Clear all caches
                _agent_cache.clear()
                _run_cache.clear()
                _toolkit_execution_cache.clear()
                logging.info("Cleared all agent, run caches and toolkit execution cache.")
                return {"status": "success", "message": "All caches cleared"}
            else:
                # Clear specific agent caches
                keys_to_remove = [key for (key, agent) in _agent_cache.items() if agent_id == agent.id]
                for key in keys_to_remove:
                    del _agent_cache[key]
                logging.info(f"Cleared {len(keys_to_remove)} cache entries for agent {agent_id}")
                return {
                    "status": "success",
                    "message": f"Cleared {len(keys_to_remove)} cache entries for agent {agent_id}",
                }

    except Exception as e:
        logging.exception(f"Error clearing cache for agent {agent_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clear cache: {str(e)}"
        )


@v2_agents_router.get("/{agent_id}/cache/info", status_code=status.HTTP_200_OK)
async def get_cache_info_v2(agent_id: str):
    """
    Get cache information for a specific agent or all agents.

    Args:
        agent_id: The ID of the agent (or "*" for all agents)

    Returns:
        dict with cache statistics
    """
    try:
        logging.info(f"Cache info request for agent: {agent_id}")

        with _cache_lock:
            if agent_id == "*":
                # Return all cache info
                return {
                    "agent_cache_count": len(_agent_cache),
                    "run_cache_count": len(_run_cache),
                    "agent_cache_keys": list(_agent_cache.keys()),
                    "run_cache_keys": list(_run_cache.keys()),
                }
            else:
                # Return specific agent cache info
                matching_keys = [key for (key, agent) in _agent_cache.items() if agent_id == agent.id]
                return {
                    "agent_id": agent_id,
                    "agent_cache_count": len(matching_keys),
                    "agent_cache_keys": matching_keys,
                }

    except Exception as e:
        logging.exception(f"Error getting cache info for agent {agent_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get cache info: {str(e)}"
        )


@v2_agents_router.post("/{agent_id}/session/clear", response_model=ClearSessionResponse, status_code=status.HTTP_200_OK)
async def clear_session_v2(agent_id: str, body: ClearSessionRequest, db: Session = Depends(get_db)):
    """
    Clear agent session history from database.

    Args:
        agent_id: The ID of the agent
        body: ClearSessionRequest with optional user_id and session_id
        db: Database session

    Returns:
        ClearSessionResponse with status and message
    """
    try:
        logging.info(f"Session clear request for agent: {agent_id}, user: {body.user_id}, session: {body.session_id}")

        # Validate agent exists
        db_agent = get_agent_info(db, agent_id)
        if not db_agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent {agent_id} not found")

        # Get the session table name
        session_table_name = slug_to_table_name(f"a_{agent_id}_agent")

        # TODO: Implement actual session clearing from database
        # This would involve:
        # 1. Get PostgresDb instance
        # 2. Delete session records based on user_id and/or session_id
        # 3. Confirm deletion

        logging.info(f"Session cleared for agent {agent_id}, table: {session_table_name}")

        return ClearSessionResponse(status="success", message=f"Session cleared for agent {agent_id}")

    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"Error clearing session for agent {agent_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clear session: {str(e)}"
        )
