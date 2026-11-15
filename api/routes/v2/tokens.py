import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from api.services.token_refresh import get_token_refresher, get_user_tokens_for_agent
from api.services.token_security import TokenSecurityError, TokenValidator
from db.session import get_db
from db.user_token_crud import (
    create_user_token,
    delete_user_token,
    get_token_scopes,
    get_user_token,
    get_user_tokens,
)
from toolkits import token_cache

tokens_router = APIRouter(prefix="/users/{user_id}/tokens", tags=["V2 Token Management"])


class TokenData(BaseModel):
    """Base token data model."""

    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    token: Optional[str] = None
    token_type: Optional[str] = None
    expires_in: Optional[int] = None
    scope: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    algorithm: Optional[str] = None
    public_key: Optional[str] = None
    issuer: Optional[str] = None
    audience: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class StoreTokenRequest(BaseModel):
    """Request model for storing user tokens."""

    integration_key: str = Field(..., description="Integration key (e.g., 'google', 'openai')")
    provider: str = Field(..., description="Provider name")
    token_type: str = Field(..., description="Token type: 'oauth2', 'api_key', or 'jwt'")
    token_data: TokenData = Field(..., description="Token data")
    scopes: Optional[List[str]] = Field(None, description="Permission scopes")
    expires_at: Optional[datetime] = Field(None, description="Token expiration datetime")

    @field_validator("token_type")
    @classmethod
    def validate_token_type(cls, v):
        if not TokenValidator.validate_token_type(v):
            raise ValueError(f"Invalid token type: {v}. Must be one of: oauth2, api_key, jwt")
        return v.lower()

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v):
        if not TokenValidator.validate_provider(v):
            raise ValueError(f"Invalid provider: {v}")
        return v.lower()

    @field_validator("integration_key")
    @classmethod
    def validate_integration_key(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError("Integration key cannot be empty")
        if len(v) > 100:
            raise ValueError("Integration key too long (max 100 characters)")
        return v.strip().lower()


class TokenInfo(BaseModel):
    """Response model for token information."""

    integration_key: str
    provider: str
    token_type: str
    scopes: Optional[List[str]] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    is_expired: bool


class StoreTokenResponse(BaseModel):
    """Response model for token storage."""

    integration_key: str
    provider: str
    token_type: str
    message: str
    created_at: datetime


class GetTokenResponse(BaseModel):
    """Response model for getting tokens."""

    integration_key: str
    provider: str
    token_type: str
    token_data: Dict[str, Any]
    scopes: Optional[List[str]] = None
    expires_at: Optional[datetime] = None
    is_expired: bool
    refreshed: bool = False


class RefreshTokenResponse(BaseModel):
    """Response model for token refresh."""

    integration_key: str
    success: bool
    message: str
    refreshed_at: Optional[datetime] = None


class DeleteTokenResponse(BaseModel):
    """Response model for token deletion."""

    integration_key: str
    message: str


@tokens_router.post("", response_model=StoreTokenResponse, status_code=status.HTTP_201_CREATED)
async def store_user_token(user_id: str, body: StoreTokenRequest, db: Session = Depends(get_db)):
    """
    Store encrypted user tokens securely.

    Args:
        user_id: User identifier
        body: Token storage request
        db: Database session

    Returns:
        StoreTokenResponse: Confirmation of token storage
    """
    logging.info(f"Request to store token for user {user_id}, integration {body.integration_key}")

    try:
        # Convert token data to dictionary, filtering out None values
        token_data_dict = {k: v for k, v in body.token_data.model_dump().items() if v is not None}

        # Log client credential presence before validation
        has_client_id = "client_id" in token_data_dict
        has_client_secret = "client_secret" in token_data_dict
        client_id_value = (
            token_data_dict.get("client_id", "NOT_PROVIDED")[:10] + "..." if has_client_id else "NOT_PROVIDED"
        )

        logging.info(
            f"Token storage for user {user_id}, integration {body.integration_key} - client_id present: {has_client_id} (value: {client_id_value}), client_secret present: {has_client_secret}"
        )

        if not token_data_dict:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token data cannot be empty")

        # Validate token data structure
        if not TokenValidator.validate_token_data_structure(body.token_type, token_data_dict):
            # Log what failed validation for OAuth2 tokens
            if body.token_type == "oauth2":
                logging.error(
                    f"Token validation failed for OAuth2 token - user {user_id}, integration {body.integration_key}. Token had client_id: {has_client_id}, client_secret: {has_client_secret}"
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid token data structure for type: {body.token_type}",
            )

        # Calculate expires_at from expires_in if provided
        expires_at = body.expires_at
        if not expires_at and body.token_data.expires_in:
            from datetime import timedelta

            expires_at = datetime.utcnow() + timedelta(seconds=body.token_data.expires_in)

        # Store token
        user_token = create_user_token(
            db=db,
            user_id=user_id,
            integration_key=body.integration_key,
            provider=body.provider,
            token_type=body.token_type,
            token_data=token_data_dict,
            scopes=body.scopes,
            expires_at=expires_at,
        )
        # The save has committed. Drop any cached access token from the previous grant.
        token_cache.invalidate(body.integration_key, user_id)

        response = StoreTokenResponse(
            integration_key=body.integration_key,
            provider=body.provider,
            token_type=body.token_type,
            message=f"Token stored successfully for {body.integration_key}",
            created_at=user_token.created_at,  # type: ignore[arg-type]
        )

        logging.info(f"Successfully stored token for user {user_id}, integration {body.integration_key}")
        return response

    except HTTPException:
        raise
    except TokenSecurityError as e:
        logging.error(f"Token security error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Token encryption failed: {str(e)}"
        )
    except ValueError as e:
        logging.error(f"Validation error: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logging.error(f"Unexpected error storing token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to store token: {str(e)}"
        )


@tokens_router.get("/{integration_key}", response_model=GetTokenResponse)
async def get_user_token_with_refresh(user_id: str, integration_key: str, db: Session = Depends(get_db)):
    """
    Get valid tokens (with auto-refresh for OAuth2).

    Args:
        user_id: User identifier
        integration_key: Integration key
        db: Database session

    Returns:
        GetTokenResponse: Valid token data
    """
    logging.info(f"Request to get token for user {user_id}, integration {integration_key}")

    try:
        # Use the auto-refresh function
        token_data, error = await get_user_tokens_for_agent(db, user_id, integration_key)

        if error:
            if "not found" in error.lower():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Token not found for integration {integration_key}"
                )
            else:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

        if not token_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Token not found for integration {integration_key}"
            )

        # Get the updated token record for metadata
        user_token = get_user_token(db, user_id, integration_key)
        if not user_token:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Token record not found for integration {integration_key}",
            )

        # Check if token was refreshed (compare updated_at with a small time window)
        from datetime import timedelta

        recently_updated = (
            datetime.utcnow() - user_token.updated_at  # type: ignore[operator]
        ) < timedelta(minutes=1)

        from db.user_token_crud import is_token_expired

        response = GetTokenResponse(
            integration_key=user_token.integration_key,  # type: ignore[arg-type]
            provider=user_token.provider,  # type: ignore[arg-type]
            token_type=user_token.token_type,  # type: ignore[arg-type]
            token_data=token_data,
            scopes=get_token_scopes(user_token),
            expires_at=user_token.expires_at,  # type: ignore[arg-type]
            is_expired=is_token_expired(user_token),
            refreshed=recently_updated and user_token.token_type == "oauth2",  # type: ignore[arg-type]
        )

        logging.info(f"Successfully retrieved token for user {user_id}, integration {integration_key}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Unexpected error getting token: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get token: {str(e)}")


@tokens_router.get("", response_model=List[TokenInfo])
async def list_user_tokens(user_id: str, db: Session = Depends(get_db)):
    """
    List all tokens for a user (metadata only, no token data).

    Args:
        user_id: User identifier
        db: Database session

    Returns:
        List[TokenInfo]: List of token metadata
    """
    logging.info(f"Request to list tokens for user {user_id}")

    try:
        user_tokens = get_user_tokens(db, user_id)

        from db.user_token_crud import is_token_expired

        tokens = []
        for token in user_tokens:
            tokens.append(
                TokenInfo(
                    integration_key=token.integration_key,  # type: ignore[arg-type]
                    provider=token.provider,  # type: ignore[arg-type]
                    token_type=token.token_type,  # type: ignore[arg-type]
                    scopes=get_token_scopes(token),
                    expires_at=token.expires_at,  # type: ignore[arg-type]
                    created_at=token.created_at,  # type: ignore[arg-type]
                    updated_at=token.updated_at,  # type: ignore[arg-type]
                    is_expired=is_token_expired(token),
                )
            )

        logging.info(f"Returning {len(tokens)} tokens for user {user_id}")
        return tokens

    except Exception as e:
        logging.error(f"Unexpected error listing tokens: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list tokens: {str(e)}"
        )


@tokens_router.post("/{integration_key}/refresh", response_model=RefreshTokenResponse)
async def refresh_user_token(user_id: str, integration_key: str, db: Session = Depends(get_db)):
    """
    Manually refresh an OAuth2 token.

    Args:
        user_id: User identifier
        integration_key: Integration key
        db: Database session

    Returns:
        RefreshTokenResponse: Refresh operation result
    """
    logging.info(f"Request to refresh token for user {user_id}, integration {integration_key}")

    try:
        # Check if token exists
        user_token = get_user_token(db, user_id, integration_key)
        if not user_token:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Token not found for integration {integration_key}"
            )

        # Check if token type supports refresh
        if user_token.token_type != "oauth2":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Token type {user_token.token_type} does not support refresh",
            )

        # Attempt refresh
        refresher = get_token_refresher()
        success, error = await refresher.refresh_oauth2_token(db, user_id, integration_key, force_refresh=True)

        if not success:
            logging.warning(f"Failed to refresh token for user {user_id}, integration {integration_key}: {error}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error or "Failed to refresh token")

        response = RefreshTokenResponse(
            integration_key=integration_key,
            success=success,
            message="Token refreshed successfully",
            refreshed_at=datetime.utcnow(),
        )

        logging.info(f"Successfully refreshed token for user {user_id}, integration {integration_key}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Unexpected error refreshing token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to refresh token: {str(e)}"
        )


@tokens_router.delete("/{integration_key}", response_model=DeleteTokenResponse)
async def delete_user_token_endpoint(user_id: str, integration_key: str, db: Session = Depends(get_db)):
    """
    Remove a user token.

    Args:
        user_id: User identifier
        integration_key: Integration key
        db: Database session

    Returns:
        DeleteTokenResponse: Deletion confirmation
    """
    logging.info(f"Request to delete token for user {user_id}, integration {integration_key}")

    try:
        # Delete token (returns False if not found)
        success = delete_user_token(db, user_id, integration_key)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Token not found for integration {integration_key}"
            )

        response = DeleteTokenResponse(
            integration_key=integration_key, message=f"Token for {integration_key} deleted successfully"
        )

        logging.info(f"Successfully deleted token for user {user_id}, integration {integration_key}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Unexpected error deleting token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete token: {str(e)}"
        )
