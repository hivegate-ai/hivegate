import datetime
import logging
from typing import Any, Dict, Optional, Tuple

import httpx
from sqlalchemy.orm import Session
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from api.services.token_security import TokenSecurityError
from db.user_token_crud import get_decrypted_token_data, get_user_token, is_token_expired, update_user_token_data


def _log_safe(value: object) -> str:
    """Strip CR/LF so request-derived values (user ids, integration keys) can't forge log lines."""
    return str(value).replace("\r", "").replace("\n", "")


class TokenRefreshError(Exception):
    """Exception raised for token refresh operations."""

    pass


def _should_retry_on_status(response: httpx.Response) -> bool:
    """
    Determine if HTTP response status should trigger a retry.

    Retryable status codes:
    - 429: Rate limited (should retry with backoff)
    - 500-504: Server errors (transient failures)

    Non-retryable status codes:
    - 400: Bad request (invalid data)
    - 401: Unauthorized (invalid/expired refresh token)
    - 403: Forbidden (insufficient permissions)
    - Other 4xx: Client errors (won't fix with retry)
    """
    return response.status_code in {429, 500, 502, 503, 504}


class OAuth2TokenRefresher:
    """Handles OAuth2 token refresh operations."""

    # OAuth2 provider configurations
    PROVIDER_CONFIGS = {
        "google": {
            "token_url": "https://oauth2.googleapis.com/token",
            "grant_type": "refresh_token",
        },
        "slack": {
            "token_url": "https://slack.com/api/oauth.v2.access",
            "grant_type": "refresh_token",
        },
        "microsoft": {
            "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            "grant_type": "refresh_token",
        },
        "github": {
            "token_url": "https://github.com/login/oauth/access_token",
            "grant_type": "refresh_token",
        },
    }

    def __init__(self, timeout: int = 30):
        """
        Initialize the token refresher.

        Args:
            timeout: HTTP request timeout in seconds
        """
        self.timeout = timeout

    async def _make_refresh_request(
        self,
        url: str,
        data: Dict[str, str],
        headers: Dict[str, str],
        user_id: str,
        integration_key: str,
    ) -> httpx.Response:
        """
        Make HTTP POST request to refresh token with retry logic.

        Retries on:
        - Network errors (timeout, connection failures)
        - Transient server errors (429, 500-504)

        Does NOT retry on:
        - Authentication errors (401, 403)
        - Bad request errors (400)
        - Other client errors (4xx)

        Args:
            url: OAuth2 token endpoint URL
            data: Request payload
            headers: Request headers
            user_id: User identifier (for logging)
            integration_key: Integration key (for logging)

        Returns:
            HTTP response

        Raises:
            httpx exceptions or RetryError after exhausting retries
        """
        attempt_number = 0

        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(
                (
                    httpx.TimeoutException,
                    httpx.ConnectError,
                    httpx.NetworkError,
                    httpx.RemoteProtocolError,
                )
            ),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        ):
            with attempt:
                attempt_number = attempt.retry_state.attempt_number
                if attempt_number > 1:
                    logging.info(
                        f"Token refresh retry attempt {attempt_number} for user {user_id}, "
                        f"integration {integration_key}"
                    )

                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, data=data, headers=headers)

                # Check if we should retry based on status code
                if _should_retry_on_status(response):
                    error_msg = (
                        f"Token refresh received retryable status {response.status_code} "
                        f"for user {user_id}, integration {integration_key}"
                    )
                    logging.warning(error_msg)

                    # For rate limiting, check Retry-After header
                    if response.status_code == 429:
                        retry_after = response.headers.get("Retry-After")
                        if retry_after:
                            logging.info(f"Rate limited. Retry-After: {retry_after} seconds")

                    # Raise exception to trigger retry
                    raise httpx.HTTPStatusError(
                        message=error_msg,
                        request=response.request,
                        response=response,
                    )

                # Success or non-retryable error - return response
                return response

        # This should never be reached due to reraise=True, but satisfies type checker
        raise RuntimeError("Retry logic failed unexpectedly")

    async def refresh_oauth2_token(
        self, db: Session, user_id: str, integration_key: str, force_refresh: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        Refresh an OAuth2 token if needed.

        Args:
            db: Database session
            user_id: User identifier
            integration_key: Integration key
            force_refresh: Force refresh even if not expired

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Get the token from database
            user_token = get_user_token(db, user_id, integration_key)
            if not user_token:
                return False, f"Token not found for user {user_id}, integration {integration_key}"

            # Check if token is OAuth2
            if user_token.token_type != "oauth2":
                return False, f"Token type {user_token.token_type} does not support refresh"

            # Check if refresh is needed
            if not force_refresh and not is_token_expired(user_token, buffer_seconds=300):  # 5 min buffer
                logging.debug(f"Token for user {user_id}, integration {integration_key} is still valid")
                return True, None

            # Get decrypted token data
            try:
                token_data = get_decrypted_token_data(user_token)
            except TokenSecurityError as e:
                return False, f"Failed to decrypt token data: {e}"

            # Check if refresh token exists
            refresh_token = token_data.get("refresh_token")
            if not refresh_token:
                return False, "No refresh token available"

            # Get provider configuration
            provider_config = self.PROVIDER_CONFIGS.get(user_token.provider.lower())  # type: ignore[union-attr]
            if not provider_config:
                return False, f"Unsupported provider for refresh: {user_token.provider}"

            # Prepare refresh request
            refresh_data = {
                "grant_type": provider_config["grant_type"],
                "refresh_token": refresh_token,
            }

            # Add client credentials if available
            client_id = token_data.get("client_id")
            client_secret = token_data.get("client_secret")

            # Log client credential availability during refresh
            client_id_prefix = client_id[:10] + "..." if client_id else "NOT_FOUND"
            logging.info(
                f"Token refresh for user {user_id}, integration {integration_key} - client_id: {client_id_prefix}, client_secret present: {bool(client_secret)}"
            )

            if client_id:
                refresh_data["client_id"] = client_id
                logging.info(f"Added client_id to refresh request for user {user_id}, integration {integration_key}")
            else:
                logging.warning(
                    f"client_id not found in token data for user {user_id}, integration {integration_key} - refresh may fail"
                )

            if client_secret:
                refresh_data["client_secret"] = client_secret
                logging.info(
                    f"Added client_secret to refresh request for user {user_id}, integration {integration_key}"
                )
            else:
                logging.warning(
                    f"client_secret not found in token data for user {user_id}, integration {integration_key} - refresh may fail"
                )

            # Make refresh request with retry logic
            try:
                response = await self._make_refresh_request(
                    url=provider_config["token_url"],
                    data=refresh_data,
                    headers={"Accept": "application/json"},
                    user_id=user_id,
                    integration_key=integration_key,
                )
            except httpx.HTTPStatusError as e:
                # The request helper raises for some HTTP statuses. Handle them with returned rejections.
                response = e.response
            except (httpx.HTTPError, RetryError) as e:
                error_msg = f"Token refresh failed after retries for user {user_id}, integration {integration_key}: {e}"
                logging.error(error_msg)
                return False, error_msg

            if response.status_code != 200:
                error_msg = f"Token refresh failed with status {response.status_code}"
                logging.error(
                    "%s for user %s, integration %s", error_msg, _log_safe(user_id), _log_safe(integration_key)
                )
                return False, error_msg

            # Parse response
            try:
                refresh_response = response.json()
            except Exception as e:
                return False, f"Failed to parse refresh response: {e}"

            # Validate response contains access token
            new_access_token = refresh_response.get("access_token")
            if not new_access_token:
                return False, "No access token in refresh response"

            # Update token data
            updated_token_data = token_data.copy()
            updated_token_data["access_token"] = new_access_token

            # Update refresh token if provided
            new_refresh_token = refresh_response.get("refresh_token")
            if new_refresh_token:
                updated_token_data["refresh_token"] = new_refresh_token

            # Update token type if provided
            token_type = refresh_response.get("token_type")
            if token_type:
                updated_token_data["token_type"] = token_type

            # Calculate new expiration
            new_expires_at = None
            expires_in = refresh_response.get("expires_in")
            if expires_in:
                try:
                    expires_in_seconds = int(expires_in)
                    new_expires_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=expires_in_seconds)
                except (ValueError, TypeError):
                    logging.warning(f"Invalid expires_in value: {expires_in}")

            # Update token in database
            updated_token = update_user_token_data(db, user_id, integration_key, updated_token_data, new_expires_at)

            if updated_token:
                logging.info(f"Successfully refreshed token for user {user_id}, integration {integration_key}")
                return True, None
            else:
                return False, "Failed to update token in database"

        except Exception as e:
            logging.error(f"Unexpected error refreshing token: {e}")
            return False, f"Unexpected error: {e}"

    async def get_valid_token(
        self, db: Session, user_id: str, integration_key: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Get a valid token, refreshing if necessary.

        Args:
            db: Database session
            user_id: User identifier
            integration_key: Integration key

        Returns:
            Tuple of (token_data, error_message)
        """
        try:
            # Get the token from database
            user_token = get_user_token(db, user_id, integration_key)
            if not user_token:
                return None, f"Token not found for user {user_id}, integration {integration_key}"

            # For non-OAuth2 tokens, just return the data
            if user_token.token_type != "oauth2":
                try:
                    token_data = get_decrypted_token_data(user_token)
                    return token_data, None
                except TokenSecurityError as e:
                    return None, f"Failed to decrypt token data: {e}"

            # For OAuth2 tokens, check if refresh is needed
            if is_token_expired(user_token, buffer_seconds=60):
                success, error = await self.refresh_oauth2_token(db, user_id, integration_key)
                if not success:
                    return None, f"Failed to refresh token: {error}"

                # Get updated token
                user_token = get_user_token(db, user_id, integration_key)
                if not user_token:
                    return None, "Token disappeared after refresh"

            # Return decrypted token data
            try:
                token_data = get_decrypted_token_data(user_token)
                return token_data, None
            except TokenSecurityError as e:
                return None, f"Failed to decrypt token data: {e}"

        except Exception as e:
            logging.error(f"Unexpected error getting valid token: {e}")
            return None, f"Unexpected error: {e}"


# Global refresher instance
_refresher_instance: Optional[OAuth2TokenRefresher] = None


def get_token_refresher() -> OAuth2TokenRefresher:
    """Get or create the global token refresher instance."""
    global _refresher_instance
    if _refresher_instance is None:
        _refresher_instance = OAuth2TokenRefresher()
    return _refresher_instance


async def get_user_tokens_for_agent(
    db: Session, user_id: str, integration_key: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Get valid tokens for agent tool usage with auto-refresh.

    This is the main function that agent tools should call to get tokens.

    Args:
        db: Database session
        user_id: User identifier
        integration_key: Integration key (e.g., 'google', 'openai')

    Returns:
        Tuple of (token_data, error_message)
    """
    refresher = get_token_refresher()
    return await refresher.get_valid_token(db, user_id, integration_key)
