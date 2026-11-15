import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.services import token_refresh
from api.services.token_refresh import OAuth2TokenRefresher, get_token_refresher, get_user_tokens_for_agent


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "status_code"), [("google", 400), ("microsoft", 401), ("github", 503)])
async def test_provider_rejection_omits_response_body(monkeypatch, caplog, provider, status_code):
    token = SimpleNamespace(provider=provider, token_type="oauth2")
    monkeypatch.setattr(token_refresh, "get_user_token", lambda *_: token)
    monkeypatch.setattr(token_refresh, "get_decrypted_token_data", lambda _: {"refresh_token": "test-refresh"})
    provider_detail = "synthetic provider response body"
    response = httpx.Response(
        status_code, text=provider_detail, request=httpx.Request("POST", "https://example.invalid/token")
    )
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))

    success, error = await OAuth2TokenRefresher().refresh_oauth2_token(
        MagicMock(), "test-user", f"{provider}_test", force_refresh=True
    )

    assert (success, error) == (False, f"Token refresh failed with status {status_code}")
    assert f"{error} for user test-user, integration {provider}_test" in caplog.messages
    assert provider_detail not in caplog.text


class TestOAuth2TokenRefresher(unittest.TestCase):
    """Test OAuth2 token refresh operations."""

    def assertErrorContains(self, error: str | None, expected_message: str):
        """Helper to assert error message with proper type checking."""
        self.assertIsNotNone(error)
        assert error is not None  # For type checker
        self.assertIn(expected_message, error)

    def setUp(self):
        """Set up test database and refresher."""
        # Create in-memory SQLite database
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # Create SQLite-compatible user_tokens table (avoid ARRAY/JSONB compatibility issues)
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                CREATE TABLE user_tokens (
                    id INTEGER PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    integration_key VARCHAR(100) NOT NULL,
                    provider VARCHAR(50) NOT NULL,
                    token_type VARCHAR(20) NOT NULL,
                    encrypted_token_data TEXT NOT NULL,
                    scopes TEXT,  -- Store as JSON string for SQLite compatibility
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    UNIQUE(user_id, integration_key)
                )
            """)
            )
            conn.commit()

        # Create session
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = SessionLocal()

        # Create refresher
        self.refresher = OAuth2TokenRefresher(timeout=10)

        # Test data
        self.user_id = "test_user_123"
        self.integration_key = "google"

    def tearDown(self):
        """Clean up test database."""
        self.db.close()

    @patch("db.user_token_crud.get_user_token")
    async def test_refresh_oauth2_token_not_found(self, mock_get_token):
        """Test refreshing non-existent token."""
        mock_get_token.return_value = None

        success, error = await self.refresher.refresh_oauth2_token(self.db, self.user_id, self.integration_key)

        self.assertFalse(success)
        self.assertErrorContains(error, "Token not found")

    @patch("db.user_token_crud.get_user_token")
    async def test_refresh_oauth2_token_wrong_type(self, mock_get_token):
        """Test refreshing non-OAuth2 token."""
        # Mock token with wrong type
        mock_token = MagicMock()
        mock_token.token_type = "api_key"
        mock_get_token.return_value = mock_token

        success, error = await self.refresher.refresh_oauth2_token(self.db, self.user_id, self.integration_key)

        self.assertFalse(success)
        self.assertErrorContains(error, "does not support refresh")

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    async def test_refresh_oauth2_token_not_expired(self, mock_is_expired, mock_get_token):
        """Test refreshing token that is not expired."""
        # Mock token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_get_token.return_value = mock_token

        # Mock not expired
        mock_is_expired.return_value = False

        success, error = await self.refresher.refresh_oauth2_token(
            self.db, self.user_id, self.integration_key, force_refresh=False
        )

        self.assertTrue(success)
        self.assertIsNone(error)

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    @patch("db.user_token_crud.get_decrypted_token_data")
    async def test_refresh_oauth2_token_no_refresh_token(self, mock_get_decrypted, mock_is_expired, mock_get_token):
        """Test refreshing token with no refresh token."""
        # Mock token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_get_token.return_value = mock_token

        # Mock expired
        mock_is_expired.return_value = True

        # Mock token data without refresh token
        mock_get_decrypted.return_value = {"access_token": "test_token"}

        success, error = await self.refresher.refresh_oauth2_token(self.db, self.user_id, self.integration_key)

        self.assertFalse(success)
        self.assertErrorContains(error, "No refresh token available")

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    @patch("db.user_token_crud.get_decrypted_token_data")
    async def test_refresh_oauth2_token_unsupported_provider(self, mock_get_decrypted, mock_is_expired, mock_get_token):
        """Test refreshing token for unsupported provider."""
        # Mock token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_token.provider = "unsupported_provider"
        mock_get_token.return_value = mock_token

        # Mock expired
        mock_is_expired.return_value = True

        # Mock token data with refresh token
        mock_get_decrypted.return_value = {"access_token": "test_token", "refresh_token": "refresh_token"}

        success, error = await self.refresher.refresh_oauth2_token(self.db, self.user_id, self.integration_key)

        self.assertFalse(success)
        self.assertErrorContains(error, "Unsupported provider for refresh")

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    @patch("db.user_token_crud.get_decrypted_token_data")
    @patch("db.user_token_crud.update_user_token_data")
    async def test_refresh_oauth2_token_success(
        self, mock_update_token, mock_get_decrypted, mock_is_expired, mock_get_token
    ):
        """Test successful OAuth2 token refresh."""
        # Mock token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_token.provider = "google"
        mock_get_token.return_value = mock_token

        # Mock expired
        mock_is_expired.return_value = True

        # Mock token data
        mock_get_decrypted.return_value = {
            "access_token": "old_access_token",
            "refresh_token": "refresh_token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }

        # Mock successful update
        mock_update_token.return_value = MagicMock()

        # Mock HTTP response
        mock_response_data = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_response_data
            mock_client_instance.post.return_value = mock_response

            success, error = await self.refresher.refresh_oauth2_token(self.db, self.user_id, self.integration_key)

            # Verify success
            self.assertTrue(success)
            self.assertIsNone(error)

            # Verify HTTP call was made
            mock_client_instance.post.assert_called_once()
            call_args = mock_client_instance.post.call_args
            self.assertEqual(call_args[0][0], "https://oauth2.googleapis.com/token")

            # Verify token was updated
            mock_update_token.assert_called_once()
            update_call_args = mock_update_token.call_args
            updated_token_data = update_call_args[0][3]  # token_data argument
            self.assertEqual(updated_token_data["access_token"], "new_access_token")
            self.assertEqual(updated_token_data["refresh_token"], "new_refresh_token")

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    @patch("db.user_token_crud.get_decrypted_token_data")
    async def test_refresh_oauth2_token_http_error(self, mock_get_decrypted, mock_is_expired, mock_get_token):
        """Test OAuth2 token refresh with HTTP error."""
        # Mock token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_token.provider = "google"
        mock_get_token.return_value = mock_token

        # Mock expired
        mock_is_expired.return_value = True

        # Mock token data
        mock_get_decrypted.return_value = {"access_token": "old_access_token", "refresh_token": "refresh_token"}

        # Mock HTTP error response
        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.text = "Invalid refresh token"
            mock_client_instance.post.return_value = mock_response

            success, error = await self.refresher.refresh_oauth2_token(self.db, self.user_id, self.integration_key)

            # Verify failure
            self.assertFalse(success)
            self.assertErrorContains(error, "Token refresh failed with status 400")

    @patch("db.user_token_crud.get_user_token")
    async def test_get_valid_token_not_found(self, mock_get_token):
        """Test getting valid token when token doesn't exist."""
        mock_get_token.return_value = None

        token_data, error = await self.refresher.get_valid_token(self.db, self.user_id, self.integration_key)

        self.assertIsNone(token_data)
        self.assertErrorContains(error, "Token not found")

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.get_decrypted_token_data")
    async def test_get_valid_token_api_key(self, mock_get_decrypted, mock_get_token):
        """Test getting valid API key token (no refresh needed)."""
        # Mock API key token
        mock_token = MagicMock()
        mock_token.token_type = "api_key"
        mock_get_token.return_value = mock_token

        # Mock decrypted data
        token_data = {"api_key": "sk-test123"}
        mock_get_decrypted.return_value = token_data

        result_data, error = await self.refresher.get_valid_token(self.db, self.user_id, self.integration_key)

        self.assertEqual(result_data, token_data)
        self.assertIsNone(error)

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    @patch("db.user_token_crud.get_decrypted_token_data")
    async def test_get_valid_token_oauth2_not_expired(self, mock_get_decrypted, mock_is_expired, mock_get_token):
        """Test getting valid OAuth2 token that's not expired."""
        # Mock OAuth2 token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_get_token.return_value = mock_token

        # Mock not expired
        mock_is_expired.return_value = False

        # Mock decrypted data
        token_data = {"access_token": "valid_token"}
        mock_get_decrypted.return_value = token_data

        result_data, error = await self.refresher.get_valid_token(self.db, self.user_id, self.integration_key)

        self.assertEqual(result_data, token_data)
        self.assertIsNone(error)

    @patch("api.services.token_refresh.OAuth2TokenRefresher.refresh_oauth2_token")
    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    @patch("db.user_token_crud.get_decrypted_token_data")
    async def test_get_valid_token_oauth2_refresh_success(
        self, mock_get_decrypted, mock_is_expired, mock_get_token, mock_refresh
    ):
        """Test getting valid OAuth2 token with successful refresh."""
        # Mock OAuth2 token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_get_token.side_effect = [mock_token, mock_token]  # Called twice

        # Mock expired first, then valid after refresh
        mock_is_expired.return_value = True

        # Mock successful refresh
        mock_refresh.return_value = (True, None)

        # Mock decrypted data
        token_data = {"access_token": "refreshed_token"}
        mock_get_decrypted.return_value = token_data

        result_data, error = await self.refresher.get_valid_token(self.db, self.user_id, self.integration_key)

        self.assertEqual(result_data, token_data)
        self.assertIsNone(error)
        mock_refresh.assert_called_once()

    @patch("api.services.token_refresh.OAuth2TokenRefresher.refresh_oauth2_token")
    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    async def test_get_valid_token_oauth2_refresh_failure(self, mock_is_expired, mock_get_token, mock_refresh):
        """Test getting valid OAuth2 token with refresh failure."""
        # Mock OAuth2 token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_get_token.return_value = mock_token

        # Mock expired
        mock_is_expired.return_value = True

        # Mock refresh failure
        mock_refresh.return_value = (False, "Refresh failed")

        result_data, error = await self.refresher.get_valid_token(self.db, self.user_id, self.integration_key)

        self.assertIsNone(result_data)
        self.assertErrorContains(error, "Failed to refresh token")

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    @patch("db.user_token_crud.get_decrypted_token_data")
    async def test_refresh_token_retry_on_timeout(self, mock_get_decrypted, mock_is_expired, mock_get_token):
        """Test token refresh retries on timeout exception."""
        # Mock token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_token.provider = "google"
        mock_get_token.return_value = mock_token

        # Mock expired
        mock_is_expired.return_value = True

        # Mock token data
        mock_get_decrypted.return_value = {
            "access_token": "old_access_token",
            "refresh_token": "refresh_token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }

        # Mock HTTP timeout
        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            # Raise TimeoutException on all attempts
            mock_client_instance.post.side_effect = httpx.TimeoutException("Request timeout")

            success, error = await self.refresher.refresh_oauth2_token(self.db, self.user_id, self.integration_key)

            # Verify failure after retries
            self.assertFalse(success)
            self.assertErrorContains(error, "Token refresh failed after retries")

            # Verify retried 3 times
            self.assertEqual(mock_client_instance.post.call_count, 3)

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    @patch("db.user_token_crud.get_decrypted_token_data")
    async def test_refresh_token_retry_on_network_error(self, mock_get_decrypted, mock_is_expired, mock_get_token):
        """Test token refresh retries on network error."""
        # Mock token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_token.provider = "google"
        mock_get_token.return_value = mock_token

        # Mock expired
        mock_is_expired.return_value = True

        # Mock token data
        mock_get_decrypted.return_value = {
            "access_token": "old_access_token",
            "refresh_token": "refresh_token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }

        # Mock network error
        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            # Raise NetworkError on all attempts
            mock_client_instance.post.side_effect = httpx.NetworkError("Network unreachable")

            success, error = await self.refresher.refresh_oauth2_token(self.db, self.user_id, self.integration_key)

            # Verify failure after retries
            self.assertFalse(success)
            self.assertErrorContains(error, "Token refresh failed after retries")

            # Verify retried 3 times
            self.assertEqual(mock_client_instance.post.call_count, 3)

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    @patch("db.user_token_crud.get_decrypted_token_data")
    async def test_refresh_token_retry_on_server_error(self, mock_get_decrypted, mock_is_expired, mock_get_token):
        """Test token refresh retries on HTTP 503 server error."""
        # Mock token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_token.provider = "google"
        mock_get_token.return_value = mock_token

        # Mock expired
        mock_is_expired.return_value = True

        # Mock token data
        mock_get_decrypted.return_value = {
            "access_token": "old_access_token",
            "refresh_token": "refresh_token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }

        # Mock 503 response
        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_response.text = "Service Unavailable"
            mock_response.request = MagicMock()
            mock_client_instance.post.return_value = mock_response

            success, error = await self.refresher.refresh_oauth2_token(self.db, self.user_id, self.integration_key)

            # Verify failure after retries
            self.assertFalse(success)
            self.assertErrorContains(error, "Token refresh failed after retries")

            # Verify retried 3 times
            self.assertEqual(mock_client_instance.post.call_count, 3)

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    @patch("db.user_token_crud.get_decrypted_token_data")
    async def test_refresh_token_no_retry_on_auth_error(self, mock_get_decrypted, mock_is_expired, mock_get_token):
        """Test token refresh does NOT retry on HTTP 401 authentication error."""
        # Mock token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_token.provider = "google"
        mock_get_token.return_value = mock_token

        # Mock expired
        mock_is_expired.return_value = True

        # Mock token data
        mock_get_decrypted.return_value = {
            "access_token": "old_access_token",
            "refresh_token": "invalid_refresh_token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }

        # Mock 401 response (should NOT retry)
        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = "Invalid refresh token"
            mock_client_instance.post.return_value = mock_response

            success, error = await self.refresher.refresh_oauth2_token(self.db, self.user_id, self.integration_key)

            # Verify immediate failure without retries
            self.assertFalse(success)
            self.assertErrorContains(error, "Token refresh failed with status 401")

            # Verify only called once (no retries)
            self.assertEqual(mock_client_instance.post.call_count, 1)

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    @patch("db.user_token_crud.get_decrypted_token_data")
    @patch("db.user_token_crud.update_user_token_data")
    async def test_refresh_token_success_after_retry(
        self, mock_update_token, mock_get_decrypted, mock_is_expired, mock_get_token
    ):
        """Test successful token refresh after initial failure."""
        # Mock token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_token.provider = "google"
        mock_get_token.return_value = mock_token

        # Mock expired
        mock_is_expired.return_value = True

        # Mock token data
        mock_get_decrypted.return_value = {
            "access_token": "old_access_token",
            "refresh_token": "refresh_token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }

        # Mock successful update
        mock_update_token.return_value = MagicMock()

        # Mock HTTP response - fail first, succeed second
        mock_response_success = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            # First attempt: timeout, second attempt: success
            mock_response_obj = MagicMock()
            mock_response_obj.status_code = 200
            mock_response_obj.json.return_value = mock_response_success

            mock_client_instance.post.side_effect = [
                httpx.TimeoutException("Request timeout"),  # First attempt fails
                mock_response_obj,  # Second attempt succeeds
            ]

            success, error = await self.refresher.refresh_oauth2_token(self.db, self.user_id, self.integration_key)

            # Verify success after retry
            self.assertTrue(success)
            self.assertIsNone(error)

            # Verify retried (called twice)
            self.assertEqual(mock_client_instance.post.call_count, 2)

            # Verify token was updated
            mock_update_token.assert_called_once()

    @patch("db.user_token_crud.get_user_token")
    @patch("db.user_token_crud.is_token_expired")
    @patch("db.user_token_crud.get_decrypted_token_data")
    async def test_refresh_token_retry_on_rate_limit(self, mock_get_decrypted, mock_is_expired, mock_get_token):
        """Test token refresh retries on HTTP 429 rate limit."""
        # Mock token
        mock_token = MagicMock()
        mock_token.token_type = "oauth2"
        mock_token.provider = "google"
        mock_get_token.return_value = mock_token

        # Mock expired
        mock_is_expired.return_value = True

        # Mock token data
        mock_get_decrypted.return_value = {
            "access_token": "old_access_token",
            "refresh_token": "refresh_token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }

        # Mock 429 response with Retry-After header
        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_response.text = "Rate limit exceeded"
            mock_response.headers = {"Retry-After": "5"}
            mock_response.request = MagicMock()
            mock_client_instance.post.return_value = mock_response

            success, error = await self.refresher.refresh_oauth2_token(self.db, self.user_id, self.integration_key)

            # Verify failure after retries
            self.assertFalse(success)
            self.assertErrorContains(error, "Token refresh failed after retries")

            # Verify retried 3 times
            self.assertEqual(mock_client_instance.post.call_count, 3)


class TestGlobalFunctions(unittest.TestCase):
    """Test global functions."""

    def test_get_token_refresher(self):
        """Test getting global token refresher instance."""
        # Should return the same instance
        refresher1 = get_token_refresher()
        refresher2 = get_token_refresher()
        self.assertIs(refresher1, refresher2)

    @patch("api.services.token_refresh.get_token_refresher")
    async def test_get_user_tokens_for_agent(self, mock_get_refresher):
        """Test main function for getting tokens for agent tools."""
        # Mock refresher
        mock_refresher = AsyncMock()
        mock_token_data = {"access_token": "test_token"}
        mock_refresher.get_valid_token.return_value = (mock_token_data, None)
        mock_get_refresher.return_value = mock_refresher

        # Mock database session
        mock_db = MagicMock()

        # Call function
        result_data, error = await get_user_tokens_for_agent(mock_db, "test_user", "google")

        # Verify
        self.assertEqual(result_data, mock_token_data)
        self.assertIsNone(error)
        mock_refresher.get_valid_token.assert_called_once_with(mock_db, "test_user", "google")


if __name__ == "__main__":
    unittest.main()
