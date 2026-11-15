"""CRUD for the MCP server registry (#91).

Header values are secrets (API keys, bearer tokens), so they are stored Fernet-encrypted with
the same key as user tokens (SECRET_TOKEN_ENC_KEY) and only decrypted by
get_mcp_server_headers, which the chat path will call when it connects. header_names is
stored in the clear so the API can show which headers are configured without the key.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from api.services.logging import log_safe
from api.services.token_security import get_token_encryption
from db.db_models import MCPServerDB

AUTH_NONE = "none"
AUTH_STATIC_HEADERS = "static_headers"

# Marks an update argument that was not given, so None can mean "clear" for nullable fields.
UNSET: Any = object()


class MCPServerExistsError(Exception):
    """An active server already has this id."""


def _apply_headers(server: MCPServerDB, headers: Optional[Dict[str, str]]) -> None:
    """Set auth_type, encrypted_headers and header_names from a header dict (empty = none)."""
    if headers:
        server.auth_type = AUTH_STATIC_HEADERS  # type: ignore[assignment]
        # encrypt_secret_json, not encrypt_token_data: the latter logs a prefix of keys named
        # token/api_key/access_token, and those are valid header names.
        server.encrypted_headers = get_token_encryption().encrypt_secret_json(dict(headers))  # type: ignore[assignment]
        server.header_names = json.dumps(sorted(headers))  # type: ignore[assignment]
    else:
        server.auth_type = AUTH_NONE  # type: ignore[assignment]
        server.encrypted_headers = None  # type: ignore[assignment]
        server.header_names = None  # type: ignore[assignment]


def create_mcp_server(
    db: Session,
    server_id: str,
    name: str,
    url: str,
    transport: str = "streamable-http",
    description: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> MCPServerDB:
    """Create a server, or reactivate a soft-deleted one with the same id (overwriting it).

    Raises MCPServerExistsError when an active server has the id. The row is read with
    FOR UPDATE, so two concurrent creates of a soft-deleted id serialize: the second one
    blocks, then sees the row active and raises, instead of both reactivating it and the
    last write silently winning. (Two creates of a brand-new id race on the insert, and the
    loser gets an IntegrityError.)

    A reactivated row is a new server in every way that matters (URL and credentials may
    differ), so its created_at is reset too, from the database clock like the updated_at
    trigger.
    """
    try:
        server = (
            db.query(MCPServerDB)
            .filter(MCPServerDB.id == server_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if server is not None and server.is_active:
            raise MCPServerExistsError(server_id)
        if server is None:
            server = MCPServerDB(id=server_id)
            db.add(server)
        else:
            server.created_at = func.now()  # type: ignore[assignment]
            server.updated_at = func.now()  # type: ignore[assignment]

        server.name = name  # type: ignore[assignment]
        server.url = url  # type: ignore[assignment]
        server.transport = transport  # type: ignore[assignment]
        server.description = description  # type: ignore[assignment]
        server.tenant_id = None  # type: ignore[assignment]  # shared until v0.9.0 (#226)
        server.is_active = True  # type: ignore[assignment]
        _apply_headers(server, headers)

        db.commit()
        db.refresh(server)
        logging.info(f"Saved MCP server {log_safe(server_id)}")
        return server
    except MCPServerExistsError:
        db.rollback()  # release the row lock
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Error creating MCP server {log_safe(server_id)}: {log_safe(e)}")
        raise


def get_mcp_server(db: Session, server_id: str) -> Optional[MCPServerDB]:
    """Get an active server by id."""
    return db.query(MCPServerDB).filter(MCPServerDB.id == server_id, MCPServerDB.is_active).first()


def get_all_mcp_servers(db: Session, include_inactive: bool = False) -> List[MCPServerDB]:
    """List servers, ordered by id."""
    query = db.query(MCPServerDB)
    if not include_inactive:
        query = query.filter(MCPServerDB.is_active)
    return query.order_by(MCPServerDB.id).all()


def update_mcp_server(
    db: Session,
    server_id: str,
    name: Optional[str] = None,
    url: Optional[str] = None,
    transport: Optional[str] = None,
    description: Optional[str] = UNSET,
    headers: Optional[Dict[str, str]] = None,
) -> Optional[MCPServerDB]:
    """Update an active server.

    description: UNSET keeps it, None clears it. headers: None keeps them, {} removes them,
    a dict replaces them.
    """
    server = get_mcp_server(db, server_id)
    if server is None:
        return None
    try:
        if name is not None:
            server.name = name  # type: ignore[assignment]
        if url is not None:
            server.url = url  # type: ignore[assignment]
        if transport is not None:
            server.transport = transport  # type: ignore[assignment]
        if description is not UNSET:
            server.description = description  # type: ignore[assignment]
        if headers is not None:
            _apply_headers(server, headers)
        db.commit()
        db.refresh(server)
        logging.info(f"Updated MCP server {log_safe(server_id)}")
        return server
    except Exception as e:
        db.rollback()
        logging.error(f"Error updating MCP server {log_safe(server_id)}: {log_safe(e)}")
        raise


def soft_delete_mcp_server(db: Session, server_id: str) -> bool:
    """Mark a server inactive and wipe its headers. False when there is no active server with that id.

    The headers are deleted, not kept: someone deleting a server because its token leaked
    expects the token to be gone. Re-creating the id starts from scratch anyway.
    """
    server = get_mcp_server(db, server_id)
    if server is None:
        return False
    try:
        server.is_active = False  # type: ignore[assignment]
        _apply_headers(server, None)
        db.commit()
    except Exception as e:
        db.rollback()
        logging.error(f"Error deleting MCP server {log_safe(server_id)}: {log_safe(e)}")
        raise
    logging.info(f"Soft deleted MCP server {log_safe(server_id)}")
    return True


def get_mcp_server_header_names(server: MCPServerDB) -> List[str]:
    """The configured header names, without decrypting anything."""
    if not server.header_names:
        return []
    try:
        return list(json.loads(server.header_names))  # type: ignore[arg-type]
    except (json.JSONDecodeError, TypeError):
        return []


def get_mcp_server_headers(server: MCPServerDB) -> Dict[str, str]:
    """Decrypt the server's headers for connecting. Raises TokenSecurityError on a bad key."""
    if server.auth_type != AUTH_STATIC_HEADERS or not server.encrypted_headers:
        return {}
    return get_token_encryption().decrypt_secret_json(server.encrypted_headers)  # type: ignore[arg-type]
