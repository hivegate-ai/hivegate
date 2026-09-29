import datetime
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from api.services.token_security import TokenValidator, get_token_encryption
from db.db_models import UserTokenDB


# Helper function to handle database-specific scopes serialization
def _handle_scopes_for_db(db: Session, scopes: Optional[List[str]]) -> Optional[Any]:
    """Handle scopes based on database engine type."""
    if scopes is None:
        return None

    # Check database engine type - handle case where db.bind might be None or mocked
    engine_name = "postgresql"  # Default to PostgreSQL
    try:
        if hasattr(db, "bind") and db.bind is not None:
            engine_name = db.bind.dialect.name
    except (AttributeError, TypeError):
        # Handle mocked sessions or other edge cases
        pass

    if engine_name == "sqlite":
        # SQLite doesn't support arrays, serialize as JSON string
        return json.dumps(scopes) if scopes else None
    else:
        # PostgreSQL supports ARRAY types
        return scopes


def create_user_token(
    db: Session,
    user_id: str,
    integration_key: str,
    provider: str,
    token_type: str,
    token_data: Dict[str, Any],
    scopes: Optional[List[str]] = None,
    expires_at: Optional[datetime.datetime] = None,
) -> UserTokenDB:
    """
    Create a new UserToken record or update an existing one.

    Args:
        db: Database session
        user_id: User identifier
        integration_key: Integration key (e.g., 'google', 'openai')
        provider: Provider name
        token_type: Type of token ('oauth2', 'api_key', 'jwt')
        token_data: Token data dictionary
        scopes: List of permission scopes
        expires_at: Token expiration datetime

    Returns:
        Created or updated UserToken instance

    Raises:
        ValueError: If validation fails
        TokenSecurityError: If encryption fails
    """
    try:
        # Validate inputs
        if not TokenValidator.validate_token_type(token_type):
            raise ValueError(f"Invalid token type: {token_type}")

        if not TokenValidator.validate_provider(provider):
            raise ValueError(f"Invalid provider: {provider}")

        if not TokenValidator.validate_token_data_structure(token_type, token_data):
            raise ValueError(f"Invalid token data structure for type: {token_type}")

        # Encrypt token data
        encryption = get_token_encryption()
        logging.info(f"Creating/updating token for user {user_id}, integration {integration_key}, provider {provider}")
        encrypted_data = encryption.encrypt_token_data(token_data)

        # Use atomic upsert approach with SQLAlchemy ORM for database compatibility
        current_time = datetime.datetime.utcnow()

        # Handle scopes based on database type
        db_scopes = _handle_scopes_for_db(db, scopes)

        # Use merge() for upsert behavior - PostgreSQL will use ON CONFLICT, SQLite will use INSERT OR REPLACE
        try:
            # Try to get existing token first
            existing_token = (
                db.query(UserTokenDB)
                .filter(
                    and_(
                        UserTokenDB.user_id == user_id,
                        UserTokenDB.integration_key == integration_key,
                    )
                )
                .with_for_update()  # Lock the row to prevent race conditions
                .first()
            )

            if existing_token:
                # Update existing token
                existing_token.provider = provider  # type: ignore[assignment]
                existing_token.token_type = token_type  # type: ignore[assignment]
                existing_token.encrypted_token_data = encrypted_data  # type: ignore[assignment]
                existing_token.scopes = db_scopes  # type: ignore[assignment]
                existing_token.expires_at = expires_at  # type: ignore[assignment]
                existing_token.updated_at = current_time  # type: ignore[assignment]
                existing_token.is_active = True  # type: ignore[assignment]

                db.commit()
                db.refresh(existing_token)

                logging.info(f"Updated user token for user {user_id}, integration {integration_key}")
                return existing_token
            else:
                # Create new token
                user_token = UserTokenDB(
                    user_id=user_id,
                    integration_key=integration_key,
                    provider=provider,
                    token_type=token_type,
                    encrypted_token_data=encrypted_data,
                    scopes=db_scopes,
                    expires_at=expires_at,
                    created_at=current_time,
                    updated_at=current_time,
                    is_active=True,
                )

                db.add(user_token)
                db.commit()
                db.refresh(user_token)

                logging.info(f"Created new user token for user {user_id}, integration {integration_key}")
                return user_token

        except Exception as db_error:
            # If we get a unique constraint violation, it means another thread created the token
            # Try one more time to update the existing token
            if "duplicate key" in str(db_error).lower() or "unique constraint" in str(db_error).lower():
                db.rollback()

                # Get the token that was created by the other thread
                existing_token = (
                    db.query(UserTokenDB)
                    .filter(
                        and_(
                            UserTokenDB.user_id == user_id,
                            UserTokenDB.integration_key == integration_key,
                        )
                    )
                    .first()
                )

                if existing_token:
                    # Update the token created by the other thread
                    existing_token.provider = provider  # type: ignore[assignment]
                    existing_token.token_type = token_type  # type: ignore[assignment]
                    existing_token.encrypted_token_data = encrypted_data  # type: ignore[assignment]
                    existing_token.scopes = db_scopes  # type: ignore[assignment]
                    existing_token.expires_at = expires_at  # type: ignore[assignment]
                    existing_token.updated_at = current_time  # type: ignore[assignment]
                    existing_token.is_active = True  # type: ignore[assignment]

                    db.commit()
                    db.refresh(existing_token)

                    logging.info(
                        f"Updated user token after constraint violation for user {user_id}, integration {integration_key}"
                    )
                    return existing_token

            # Re-raise if it's not a constraint violation or we couldn't recover
            raise db_error
    except Exception as e:
        db.rollback()
        logging.error(f"Error creating/updating user token: {e}")
        raise


def get_user_token(db: Session, user_id: str, integration_key: str) -> Optional[UserTokenDB]:
    """
    Get UserToken by user_id and integration_key.

    Args:
        db: Database session
        user_id: User identifier
        integration_key: Integration key

    Returns:
        UserTokenDB instance or None if not found
    """
    return (
        db.query(UserTokenDB)
        .filter(
            and_(UserTokenDB.user_id == user_id, UserTokenDB.integration_key == integration_key, UserTokenDB.is_active)
        )
        .first()
    )


def get_user_tokens(db: Session, user_id: str) -> List[UserTokenDB]:
    """
    Get all active tokens for a user.

    Args:
        db: Database session
        user_id: User identifier

    Returns:
        List of UserTokenDB instances
    """
    return (
        db.query(UserTokenDB)
        .filter(and_(UserTokenDB.user_id == user_id, UserTokenDB.is_active))
        .order_by(UserTokenDB.integration_key)
        .all()
    )


def get_decrypted_token_data(user_token: UserTokenDB) -> Dict[str, Any]:
    """
    Decrypt and return token data from a UserTokenDB instance.

    Args:
        user_token: UserTokenDB instance

    Returns:
        Decrypted token data dictionary

    Raises:
        TokenSecurityError: If decryption fails
    """
    logging.info(
        f"Decrypting token for user {user_token.user_id}, integration {user_token.integration_key}, provider {user_token.provider}, db_token_type {user_token.token_type}"
    )
    encryption = get_token_encryption()
    token_data = encryption.decrypt_token_data(user_token.encrypted_token_data)  # type: ignore[arg-type]

    # Log client credential presence after decryption for OAuth2 tokens
    if user_token.token_type == "oauth2":
        has_client_id = "client_id" in token_data
        has_client_secret = "client_secret" in token_data
        client_id_prefix = token_data.get("client_id", "")[:10] + "..." if has_client_id else "NOT_FOUND"

        logging.info(
            f"Decrypted OAuth2 token for user {user_token.user_id}, integration {user_token.integration_key} - client_id: {client_id_prefix}, client_secret present: {has_client_secret}"
        )

        if not has_client_id:
            logging.warning(
                f"OAuth2 token missing client_id for user {user_token.user_id}, integration {user_token.integration_key} - token refresh will likely fail"
            )
        if not has_client_secret:
            logging.warning(
                f"OAuth2 token missing client_secret for user {user_token.user_id}, integration {user_token.integration_key} - token refresh may fail"
            )

    return token_data


def get_token_scopes(user_token: UserTokenDB) -> Optional[List[str]]:
    """
    Get scopes from a UserTokenDB instance, handling database-specific deserialization.

    Args:
        user_token: UserTokenDB instance

    Returns:
        List of scopes or None
    """
    if user_token.scopes is None:
        return None

    # If it's already a list (PostgreSQL), return as-is
    if isinstance(user_token.scopes, list):
        return user_token.scopes

    # If it's a string (SQLite), deserialize from JSON
    if isinstance(user_token.scopes, str):
        try:
            return json.loads(user_token.scopes)
        except json.JSONDecodeError:
            logging.warning(f"Failed to decode scopes JSON: {user_token.scopes}")
            return None

    return user_token.scopes  # type: ignore[return-value]


def is_token_expired(user_token: UserTokenDB, buffer_seconds: int = 60) -> bool:
    """
    Check if a token is expired or will expire soon.

    Args:
        user_token: UserTokenDB instance
        buffer_seconds: Seconds before expiry to consider token expired

    Returns:
        True if token is expired or will expire within buffer time
    """
    if user_token.expires_at is None:
        return False
    return TokenValidator.is_token_expired(user_token.expires_at, buffer_seconds)  # type: ignore[arg-type]


def update_user_token_data(
    db: Session,
    user_id: str,
    integration_key: str,
    token_data: Dict[str, Any],
    expires_at: Optional[datetime.datetime] = None,
) -> Optional[UserTokenDB]:
    """
    Update token data for an existing user token.

    Args:
        db: Database session
        user_id: User identifier
        integration_key: Integration key
        token_data: New token data
        expires_at: New expiration datetime

    Returns:
        Updated UserToken instance or None if not found

    Raises:
        TokenSecurityError: If encryption fails
    """
    user_token = get_user_token(db, user_id, integration_key)
    if not user_token:
        return None

    try:
        # Validate token data structure
        if not TokenValidator.validate_token_data_structure(user_token.token_type, token_data):  # type: ignore[arg-type]
            raise ValueError(f"Invalid token data structure for type: {user_token.token_type}")

        # Encrypt new token data
        encryption = get_token_encryption()
        encrypted_data = encryption.encrypt_token_data(token_data)

        user_token.encrypted_token_data = encrypted_data  # type: ignore[assignment]
        if expires_at is not None:
            user_token.expires_at = expires_at  # type: ignore[assignment]
        user_token.updated_at = datetime.datetime.utcnow()  # type: ignore[assignment]

        db.commit()
        db.refresh(user_token)

        logging.info(f"Updated token data for user {user_id}, integration {integration_key}")
        return user_token
    except Exception as e:
        db.rollback()
        logging.error(f"Error updating user token data: {e}")
        raise


def delete_user_token(db: Session, user_id: str, integration_key: str) -> bool:
    """
    Soft delete a user token by setting is_active to False.

    Args:
        db: Database session
        user_id: User identifier
        integration_key: Integration key

    Returns:
        True if deleted, False if not found
    """
    user_token = get_user_token(db, user_id, integration_key)
    if not user_token:
        return False

    user_token.is_active = False  # type: ignore[assignment]
    user_token.updated_at = datetime.datetime.utcnow()  # type: ignore[assignment]
    db.commit()

    logging.info(f"Soft deleted user token for user {user_id}, integration {integration_key}")
    return True


def hard_delete_user_token(db: Session, user_id: str, integration_key: str) -> bool:
    """
    Hard delete a user token from the database.

    Args:
        db: Database session
        user_id: User identifier
        integration_key: Integration key

    Returns:
        True if deleted, False if not found
    """
    try:
        user_token = (
            db.query(UserTokenDB)
            .filter(and_(UserTokenDB.user_id == user_id, UserTokenDB.integration_key == integration_key))
            .first()
        )

        if not user_token:
            logging.warning(f"User token not found for deletion: {user_id}, {integration_key}")
            return False

        db.delete(user_token)
        db.commit()

        logging.info(f"Hard deleted user token for user {user_id}, integration {integration_key}")
        return True
    except Exception as e:
        db.rollback()
        logging.error(f"Error hard deleting user token: {e}")
        raise


def delete_all_user_tokens(db: Session, user_id: str) -> int:
    """
    Soft delete all tokens for a user.

    Args:
        db: Database session
        user_id: User identifier

    Returns:
        Number of tokens deleted
    """
    tokens = get_user_tokens(db, user_id)
    count = 0

    for token in tokens:
        token.is_active = False  # type: ignore[assignment]
        token.updated_at = datetime.datetime.utcnow()  # type: ignore[assignment]
        count += 1

    db.commit()
    logging.info(f"Soft deleted {count} tokens for user {user_id}")
    return count


def user_token_exists(db: Session, user_id: str, integration_key: str) -> bool:
    """
    Check if an active user token exists.

    Args:
        db: Database session
        user_id: User identifier
        integration_key: Integration key

    Returns:
        True if exists and active, False otherwise
    """
    return (
        db.query(UserTokenDB)
        .filter(
            and_(UserTokenDB.user_id == user_id, UserTokenDB.integration_key == integration_key, UserTokenDB.is_active)
        )
        .first()
        is not None
    )


def has_user_token(db: Session, user_id: str, integration_key: str) -> bool:
    """
    Fast check if user has an active token (does not fetch or validate).

    This is a lightweight existence check that queries the database
    but does NOT decrypt, refresh, or validate the token.

    Use this for authentication flow decisions (which toolkit to show).
    Use get_user_token() or get_decrypted_token_data() when you actually need the token data.

    Args:
        db: Database session
        user_id: User identifier
        integration_key: Integration key

    Returns:
        True if active token exists, False otherwise

    Performance:
        This query only selects the ID column and uses indexed filters,
        making it much faster than full token retrieval.
    """
    return (
        db.query(UserTokenDB.id)  # Only query ID column for speed
        .filter(
            and_(
                UserTokenDB.user_id == user_id,
                UserTokenDB.integration_key == integration_key,
                UserTokenDB.is_active == True,  # noqa: E712
            )
        )
        .first()
    ) is not None


def has_user_tokens_batch(db: Session, user_id: str, integration_keys: List[str]) -> Dict[str, bool]:
    """
    Fast batch check if user has active tokens for multiple integrations.

    This performs a single database query to check multiple integration keys at once,
    avoiding the overhead of multiple database sessions.

    Args:
        db: Database session
        user_id: User identifier
        integration_keys: List of integration keys to check

    Returns:
        Dictionary mapping integration_key to boolean (exists or not)

    Performance:
        Single SQL query with IN clause, much faster than multiple individual queries.

    Example:
        >>> has_user_tokens_batch(db, "user123", ["google_calendar", "google_gmail"])
        {"google_calendar": True, "google_gmail": False}
    """
    if not integration_keys:
        return {}

    # Query all matching tokens in one go
    results: List[Any] = (
        db.query(UserTokenDB.integration_key)
        .filter(
            and_(
                UserTokenDB.user_id == user_id,
                UserTokenDB.integration_key.in_(integration_keys),
                UserTokenDB.is_active == True,  # noqa: E712
            )
        )
        .all()
    )

    # Convert to set of found integration keys
    found_keys = {row.integration_key for row in results}

    # Return dict with all requested keys
    return {key: (key in found_keys) for key in integration_keys}


def get_tokens_expiring_soon(db: Session, hours_ahead: int = 1) -> List[UserTokenDB]:
    """
    Get tokens that will expire within the specified number of hours.

    Args:
        db: Database session
        hours_ahead: Hours ahead to check for expiring tokens

    Returns:
        List of UserTokenDB instances that will expire soon
    """
    cutoff_time = datetime.datetime.utcnow() + datetime.timedelta(hours=hours_ahead)

    return (
        db.query(UserTokenDB)
        .filter(
            and_(
                UserTokenDB.is_active,
                UserTokenDB.expires_at.isnot(None),
                # Legacy Column() attributes type as Never under SQLAlchemy 2.1 (no mypy plugin),
                # so the comparison is inferred as bool; it is a SQL expression at runtime.
                UserTokenDB.expires_at <= cutoff_time,  # type: ignore[arg-type]
                UserTokenDB.token_type == "oauth2",  # Only OAuth2 tokens can be refreshed
            )
        )
        .all()
    )
