"""MCP server registry (#91): define a server once, reference it by id from agents.

Header values are write-only: requests may set them, responses only list their names.
Agents reference them by id in config.mcp_server_ids; chat connects them.
"""

import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agents.mcp_validation import SUPPORTED_MCP_TRANSPORTS, mcp_url_problem, normalize_mcp_transport
from api.services.logging import log_safe
from api.services.token_security import TokenSecurityError
from db.agent_info_crud import get_agents_using_mcp_server
from db.mcp_server_crud import (
    UNSET,
    MCPServerExistsError,
    create_mcp_server,
    get_all_mcp_servers,
    get_mcp_server,
    get_mcp_server_header_names,
    soft_delete_mcp_server,
    update_mcp_server,
)
from db.session import get_db

v2_mcp_servers_router = APIRouter(prefix="/mcp-servers", tags=["V2 MCP Servers"])

# Lowercase so the id can prefix tool names (#95) without colliding by case.
SERVER_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"
# RFC 9110 field-name token. Used with fullmatch: in Python `$` also matches before a
# trailing newline, so "X-Key\n" would pass a ^...$ pattern.
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
# Visible ASCII with inner spaces: what HTTP clients can send as-is. No leading or trailing
# whitespace, which would otherwise surface as a confusing 401 only once chat connects.
_HEADER_VALUE = re.compile(r"[\x21-\x7e](?:[\x20-\x7e]*[\x21-\x7e])?")
# Set by the HTTP client or the MCP transport; a static value would break or pin the session.
RESERVED_HEADERS = frozenset(
    {
        "accept",
        "connection",
        "content-length",
        "content-type",
        "host",
        "keep-alive",
        "last-event-id",
        "mcp-protocol-version",
        "mcp-session-id",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
MAX_HEADERS = 20
ENCRYPTION_NOT_CONFIGURED = (
    "Header encryption is not configured on this server: set SECRET_TOKEN_ENC_KEY "
    "(the key user tokens use) and restart."
)

URL_DESCRIPTION = (
    "Absolute http(s) URL. Shown in API responses, so it is not treated as secret: put "
    "credentials in headers. user:password@ URLs are refused for that reason."
)
HEADERS_DESCRIPTION = "Static headers sent on every request (e.g. Authorization). Stored encrypted, never returned."


def _check_url(value: str) -> str:
    problem = mcp_url_problem(value, allow_userinfo=False)
    if problem == "userinfo":
        raise ValueError("url must not contain credentials (user:password@); send them as headers instead")
    if problem is not None:
        raise ValueError("url must be an absolute http:// or https:// URL with a valid port")
    return value.strip()


def _check_transport(value: str) -> str:
    transport = normalize_mcp_transport(value)
    if transport is None:
        supported = ", ".join(repr(t) for t in SUPPORTED_MCP_TRANSPORTS)
        raise ValueError(f"transport must be one of {supported}")
    return transport


def _check_headers(value: Dict[str, str]) -> Dict[str, str]:
    # Values are never put in a message: they may be secrets. (422s do not echo the input
    # either; see api/validation_errors.py.)
    if len(value) > MAX_HEADERS:
        raise ValueError(f"at most {MAX_HEADERS} headers")
    lowered = set()
    for name, header_value in value.items():
        if not _HEADER_NAME.fullmatch(name):
            raise ValueError(f"invalid header name {name!r}")
        if name.lower() in RESERVED_HEADERS:
            raise ValueError(f"header {name!r} is set by the MCP transport and cannot be configured")
        if name.lower() in lowered:
            raise ValueError(f"header {name!r} is given twice")
        lowered.add(name.lower())
        if not _HEADER_VALUE.fullmatch(header_value):
            raise ValueError(
                f"header {name!r} needs a non-empty value of printable ASCII, with no leading or trailing whitespace"
            )
    return value


class _MCPServerFields(BaseModel):
    """Fields and validation shared by create and update; Create makes some required."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    url: Optional[str] = Field(default=None, description=URL_DESCRIPTION)
    transport: Optional[str] = None
    description: Optional[str] = Field(default=None, max_length=1000)
    headers: Optional[Dict[str, str]] = Field(default=None, description=HEADERS_DESCRIPTION)

    @field_validator("url")
    @classmethod
    def _valid_url(cls, v: Optional[str]) -> Optional[str]:
        return v if v is None else _check_url(v)

    @field_validator("transport")
    @classmethod
    def _valid_transport(cls, v: Optional[str]) -> Optional[str]:
        return v if v is None else _check_transport(v)

    @field_validator("headers")
    @classmethod
    def _valid_headers(cls, v: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        return v if v is None else _check_headers(v)


class MCPServerCreate(_MCPServerFields):
    id: str = Field(..., pattern=SERVER_ID_PATTERN)
    name: str = Field(..., min_length=1, max_length=255)  # type: ignore[assignment]
    url: str = Field(..., description=URL_DESCRIPTION)  # type: ignore[assignment]
    transport: str = "streamable-http"  # type: ignore[assignment]


class MCPServerUpdate(_MCPServerFields):
    """Only fields present in the body change. description: null clears it; headers: {} removes them."""

    @model_validator(mode="after")
    def _no_null_for_required_fields(self) -> "MCPServerUpdate":
        for field in ("name", "url", "transport", "headers"):
            if field in self.model_fields_set and getattr(self, field) is None:
                hint = " (use {} to remove all headers)" if field == "headers" else ""
                raise ValueError(f"{field} cannot be null{hint}")
        return self


class MCPServerResponse(BaseModel):
    id: str
    name: str
    url: str
    transport: str
    description: Optional[str] = None
    auth_type: str
    header_names: List[str] = []
    tenant_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_active: bool = True


def _to_response(server) -> MCPServerResponse:  # type: ignore[no-untyped-def]
    return MCPServerResponse(
        id=server.id,
        name=server.name,
        url=server.url,
        transport=server.transport,
        description=server.description,
        auth_type=server.auth_type,
        header_names=get_mcp_server_header_names(server),
        tenant_id=server.tenant_id,
        created_at=server.created_at,
        updated_at=server.updated_at,
        is_active=server.is_active,
    )


@v2_mcp_servers_router.post("", response_model=MCPServerResponse, status_code=status.HTTP_201_CREATED)
async def create_mcp_server_v2(body: MCPServerCreate, db: Session = Depends(get_db)):
    """Register an MCP server. 409 if an active server already has this id."""
    conflict = HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"MCP server {body.id} already exists")
    try:
        server = create_mcp_server(
            db=db,
            server_id=body.id,
            name=body.name,
            url=body.url,
            transport=body.transport,
            description=body.description,
            headers=body.headers,
        )
    except MCPServerExistsError:
        raise conflict
    except IntegrityError:
        # Lost a race with a concurrent insert of a brand-new id.
        raise conflict
    except TokenSecurityError:
        logging.exception("Cannot encrypt MCP server headers")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=ENCRYPTION_NOT_CONFIGURED)
    except Exception as e:
        logging.exception(f"Error creating MCP server {log_safe(body.id)}: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create MCP server {body.id}"
        )
    return _to_response(server)


@v2_mcp_servers_router.get("", response_model=List[MCPServerResponse])
async def list_mcp_servers_v2(include_inactive: bool = False, db: Session = Depends(get_db)):
    """List registered MCP servers. Header values are never returned."""
    return [_to_response(s) for s in get_all_mcp_servers(db, include_inactive=include_inactive)]


@v2_mcp_servers_router.get("/{server_id}", response_model=MCPServerResponse)
async def get_mcp_server_v2(server_id: str, db: Session = Depends(get_db)):
    server = get_mcp_server(db, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server {server_id} not found")
    return _to_response(server)


@v2_mcp_servers_router.put("/{server_id}", response_model=MCPServerResponse)
async def update_mcp_server_v2(server_id: str, body: MCPServerUpdate, db: Session = Depends(get_db)):
    """Update the fields present in the body; see MCPServerUpdate for null and headers."""
    try:
        server = update_mcp_server(
            db=db,
            server_id=server_id,
            name=body.name,
            url=body.url,
            transport=body.transport,
            description=body.description if "description" in body.model_fields_set else UNSET,
            headers=body.headers,
        )
    except TokenSecurityError:
        logging.exception("Cannot encrypt MCP server headers")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=ENCRYPTION_NOT_CONFIGURED)
    except Exception as e:
        logging.exception(f"Error updating MCP server {log_safe(server_id)}: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update MCP server {server_id}"
        )
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server {server_id} not found")
    return _to_response(server)


@v2_mcp_servers_router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server_v2(server_id: str, db: Session = Depends(get_db)):
    """Soft delete; the server's headers are wiped. 409 while an agent still uses it (its
    chats would start failing): detach it with PUT /v2/agents/{agent_id}/mcp-servers first."""
    # Locked until the delete commits, so an attach running now waits and then sees the
    # server gone (see get_mcp_servers_by_ids).
    if get_mcp_server(db, server_id, lock=True) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server {server_id} not found")
    users = get_agents_using_mcp_server(db, server_id)
    if users:
        db.rollback()  # release the lock
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MCP server {server_id} is used by agents: {', '.join(users)}. Remove it from them first.",
        )
    try:
        deleted = soft_delete_mcp_server(db, server_id)
    except Exception as e:
        logging.exception(f"Error deleting MCP server {log_safe(server_id)}: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete MCP server {server_id}"
        )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server {server_id} not found")
    return None
