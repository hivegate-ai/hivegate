import logging
import os
from typing import List, Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from db.api_key_crud import get_api_key_scopes, validate_api_key
from db.db_models import ApiKeyDB
from db.session import get_db

# API key header name
API_KEY_HEADER = "X-API-Key"

# Admin secret header name
ADMIN_SECRET_HEADER = "X-Admin-Secret"

# Environment variable to bypass authentication (for development/testing)
AUTH_DISABLED_ENV = "AUTH_DISABLED"

# Environment variable for admin secret
ADMIN_SECRET_ENV = "ADMIN_SECRET"

# API key header security scheme
api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

# Admin secret header security scheme
admin_secret_header = APIKeyHeader(name=ADMIN_SECRET_HEADER, auto_error=False)


def is_auth_disabled() -> bool:
    """Check if authentication is disabled via environment variable."""
    return os.environ.get(AUTH_DISABLED_ENV, "").lower() in ("true", "1", "yes")


async def get_api_key(
    api_key: Optional[str] = Security(api_key_header),
    db=Depends(get_db),
) -> Optional[ApiKeyDB]:
    """
    Validate API key from request header.

    This dependency extracts the API key from the X-API-Key header,
    validates it against the database, and returns the ApiKeyDB record.

    Args:
        api_key: API key from header
        db: Database session

    Returns:
        ApiKeyDB record if valid

    Raises:
        HTTPException: If API key is missing or invalid
    """
    # Check if auth is disabled (for development)
    if is_auth_disabled():
        logging.debug("Authentication disabled via environment variable")
        return None

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Include X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # Validate the API key
    api_key_record = validate_api_key(db, api_key)

    if not api_key_record:
        logging.warning(f"Invalid API key attempted: {api_key[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return api_key_record


async def get_optional_api_key(
    api_key: Optional[str] = Security(api_key_header),
    db=Depends(get_db),
) -> Optional[ApiKeyDB]:
    """
    Optionally validate API key - doesn't fail if missing.

    Use this for endpoints that work with or without authentication.
    """
    if is_auth_disabled():
        return None

    if not api_key:
        return None

    return validate_api_key(db, api_key)


def require_scopes(required_scopes: List[str]):
    """
    Dependency factory that requires specific scopes.

    Usage:
        @router.get("/admin/resource")
        async def admin_resource(
            api_key: ApiKeyDB = Depends(require_scopes(["admin"]))
        ):
            ...

    Args:
        required_scopes: List of required scope names

    Returns:
        Dependency function that validates scopes
    """

    async def scope_validator(api_key: Optional[ApiKeyDB] = Depends(get_api_key)) -> Optional[ApiKeyDB]:
        # If auth is disabled, allow access
        if api_key is None and is_auth_disabled():
            return None

        if api_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        # Get the API key's scopes
        key_scopes = get_api_key_scopes(api_key)

        # Check if key has "admin" scope (admin has access to everything)
        if "admin" in key_scopes:
            return api_key

        # Check if key has all required scopes
        missing_scopes = set(required_scopes) - set(key_scopes)
        if missing_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scopes: {', '.join(missing_scopes)}",
            )

        return api_key

    return scope_validator


async def reject_usage_only_keys(api_key: Optional[ApiKeyDB] = Depends(get_api_key)) -> None:
    """Keep read-only usage keys (scopes == ["usage:read"]) off the agent routes.

    /v2 checks only that a key is valid, not its scopes, so without this a key minted
    for the usage dashboard could call agents and spend tokens. Keys with no scopes, or
    with any other scope, are unaffected.
    """
    if api_key is None:
        return
    scopes = get_api_key_scopes(api_key)
    if scopes and set(scopes) <= {"usage:read"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API key can only read usage (/usage); it cannot call agents",
        )


# Pre-built scope validators for common use cases
require_read = require_scopes(["read"])
require_write = require_scopes(["write"])
require_admin = require_scopes(["admin"])


# =============================================================================
# Admin Secret Authentication
# =============================================================================


def get_admin_secret() -> Optional[str]:
    """Get the admin secret from environment variable."""
    return os.environ.get(ADMIN_SECRET_ENV)


async def verify_admin_secret(
    admin_secret: Optional[str] = Security(admin_secret_header),
) -> bool:
    """
    Verify admin secret from request header.

    This dependency checks the X-Admin-Secret header against the ADMIN_SECRET
    environment variable for admin endpoint access.

    Args:
        admin_secret: Admin secret from header

    Returns:
        True if valid

    Raises:
        HTTPException: If admin secret is missing, not configured, or invalid
    """
    # Check if auth is disabled (for development)
    if is_auth_disabled():
        logging.debug("Admin authentication disabled via AUTH_DISABLED")
        return True

    # Get expected secret from environment
    expected_secret = get_admin_secret()

    if not expected_secret:
        logging.error("ADMIN_SECRET environment variable not set")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin authentication not configured. Set ADMIN_SECRET environment variable.",
        )

    if not admin_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing admin secret. Include X-Admin-Secret header.",
            headers={"WWW-Authenticate": "AdminSecret"},
        )

    # Constant-time comparison to prevent timing attacks
    import secrets

    if not secrets.compare_digest(admin_secret, expected_secret):
        logging.warning("Invalid admin secret attempted")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin secret",
            headers={"WWW-Authenticate": "AdminSecret"},
        )

    return True
