import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import requests

from api.services.models import PullPromptResponse, PushPromptRequest
from api.services.prompts_client import PromptsServiceClient
from cache.prompts_cache import prompt_cache


class TestPromptsServiceClient(unittest.TestCase):
    """Test the prompts service client."""

    def setUp(self):
        """Set up test fixtures."""
        # Clear the cache before each test
        prompt_cache.clear()

        self.client = PromptsServiceClient()
        self.client.prompts_service_url = "https://test-prompts-service.com"
        self.client.service_key_path = "agents/service-account.json"

    @patch("os.path.exists")
    def test_is_configured_true(self, mock_exists):
        """Test client is configured correctly."""
        mock_exists.return_value = True

        self.assertTrue(self.client._is_configured())

    @patch("os.path.exists")
    def test_is_configured_false_no_service_account(self, mock_exists):
        """Test client is not configured due to missing service account."""
        mock_exists.return_value = False

        self.assertFalse(self.client._is_configured())

    def test_is_configured_false_no_service_url(self):
        """Test client is not configured due to missing service URL."""
        self.client.prompts_service_url = None

        self.assertFalse(self.client._is_configured())

    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_list_prompts_success(self, mock_exists, mock_call_service):
        """Test successful listing of prompts."""
        mock_exists.return_value = True
        mock_response = MagicMock()
        mock_response.json.return_value = {"prompts": ["prompt1", "prompt2", "prompt3"]}
        mock_call_service.return_value = mock_response

        prompts = self.client.list_prompts()

        self.assertEqual(prompts, ["prompt1", "prompt2", "prompt3"])
        mock_call_service.assert_called_once_with(
            service_url=self.client.prompts_service_url, service_key_path=self.client.service_key_path
        )

    @patch("os.path.exists")
    def test_list_prompts_not_configured(self, mock_exists):
        """Test list prompts when client is not configured."""
        mock_exists.return_value = False

        prompts = self.client.list_prompts()

        self.assertEqual(prompts, [])

    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_list_prompts_service_error(self, mock_exists, mock_call_service):
        """Test list prompts when service throws an error."""
        mock_exists.return_value = True
        mock_call_service.side_effect = requests.exceptions.RequestException("Service error")

        prompts = self.client.list_prompts()

        self.assertEqual(prompts, [])

    @patch("api.services.prompts_client.prompt_cache")
    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_get_prompt_success(self, mock_exists, mock_call_service, mock_cache):
        """Test successful prompt retrieval."""
        mock_exists.return_value = True
        mock_cache.get.return_value = None  # Force cache miss
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "name": "test-prompt",
            "template": "You are a helpful assistant.",
            "description": "Test description",
        }
        mock_call_service.return_value = mock_response

        prompt = self.client.get_prompt("test-prompt")

        self.assertIsInstance(prompt, PullPromptResponse)
        assert prompt is not None
        self.assertEqual(prompt.name, "test-prompt")
        self.assertEqual(prompt.template, "You are a helpful assistant.")
        mock_call_service.assert_called_once_with(
            service_url=f"{self.client.prompts_service_url}/test-prompt", service_key_path=self.client.service_key_path
        )

    @patch("api.services.prompts_client.prompt_cache")
    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_get_prompt_not_found(self, mock_exists, mock_call_service, mock_cache):
        """Test prompt retrieval when prompt is not found."""
        mock_exists.return_value = True
        mock_cache.get.return_value = None  # Force cache miss
        mock_response = MagicMock()

        # Create a proper HTTPError with a response attribute
        http_error = requests.exceptions.HTTPError()
        http_error.response = MagicMock()
        http_error.response.status_code = 404

        mock_response.raise_for_status.side_effect = http_error
        mock_call_service.return_value = mock_response

        prompt = self.client.get_prompt("non-existent-prompt")

        self.assertIsNone(prompt)

    @patch("api.services.prompts_client.prompt_cache")
    @patch("os.path.exists")
    def test_get_prompt_not_configured(self, mock_exists, mock_cache):
        """Test get prompt when client is not configured."""
        mock_exists.return_value = False
        mock_cache.get.return_value = None  # Force cache miss

        prompt = self.client.get_prompt("test-prompt")

        self.assertIsNone(prompt)

    @patch("requests.post")
    @patch("google.oauth2.service_account.IDTokenCredentials.from_service_account_file")
    @patch("os.path.exists")
    def test_create_prompt_success(self, mock_exists, mock_creds_from_file, mock_post):
        """Test successful prompt creation."""
        mock_exists.return_value = True

        # Mock credentials
        mock_creds = MagicMock()
        mock_creds.token = "test-token"
        mock_creds_from_file.return_value = mock_creds

        # Mock successful response
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        prompt_request = PushPromptRequest(
            name="test-prompt", raw_template="You are helpful", description="Test prompt"
        )

        result = self.client.create_prompt(prompt_request)

        self.assertTrue(result)
        mock_post.assert_called_once()

    @patch("requests.post")
    @patch("google.oauth2.service_account.IDTokenCredentials.from_service_account_file")
    @patch("os.path.exists")
    def test_create_prompt_already_exists(self, mock_exists, mock_creds_from_file, mock_post):
        """Test prompt creation when prompt already exists."""
        mock_exists.return_value = True

        # Mock credentials
        mock_creds = MagicMock()
        mock_creds.token = "test-token"
        mock_creds_from_file.return_value = mock_creds

        # Mock conflict response
        mock_response = MagicMock()
        mock_response.status_code = 409
        http_error = requests.exceptions.HTTPError()
        http_error.response = mock_response
        mock_response.raise_for_status.side_effect = http_error
        mock_post.return_value = mock_response

        prompt_request = PushPromptRequest(
            name="existing-prompt", raw_template="You are helpful", description="Test prompt"
        )

        result = self.client.create_prompt(prompt_request)

        self.assertTrue(result)

    @patch("os.path.exists")
    def test_create_prompt_not_configured(self, mock_exists):
        """Test create prompt when client is not configured."""
        mock_exists.return_value = False

        prompt_request = PushPromptRequest(
            name="test-prompt", raw_template="You are helpful", description="Test prompt"
        )

        result = self.client.create_prompt(prompt_request)

        self.assertFalse(result)

    @patch("requests.delete")
    @patch("google.oauth2.service_account.IDTokenCredentials.from_service_account_file")
    @patch("os.path.exists")
    def test_delete_prompt_success(self, mock_exists, mock_creds_from_file, mock_delete):
        """Test successful prompt deletion."""
        mock_exists.return_value = True

        # Mock credentials
        mock_creds = MagicMock()
        mock_creds.token = "test-token"
        mock_creds_from_file.return_value = mock_creds

        # Mock successful response
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_delete.return_value = mock_response

        result = self.client.delete_prompt("test-prompt")

        self.assertTrue(result)
        mock_delete.assert_called_once()

    @patch("requests.delete")
    @patch("google.oauth2.service_account.IDTokenCredentials.from_service_account_file")
    @patch("os.path.exists")
    def test_delete_prompt_not_found(self, mock_exists, mock_creds_from_file, mock_delete):
        """Test prompt deletion when prompt is not found."""
        mock_exists.return_value = True

        # Mock credentials
        mock_creds = MagicMock()
        mock_creds.token = "test-token"
        mock_creds_from_file.return_value = mock_creds

        # Mock not found response
        mock_response = MagicMock()
        mock_response.status_code = 404
        http_error = requests.exceptions.HTTPError()
        http_error.response = mock_response
        mock_response.raise_for_status.side_effect = http_error
        mock_delete.return_value = mock_response

        result = self.client.delete_prompt("non-existent-prompt")

        self.assertFalse(result)

    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_fetch_single_prompt_success(self, mock_exists, mock_call_service):
        """Test successful single prompt fetch for batch operations."""
        mock_exists.return_value = True
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "name": "test-prompt",
            "template": "You are helpful.",
            "description": "Test prompt",
        }
        mock_call_service.return_value = mock_response

        prompt = self.client._fetch_single_prompt("test-prompt")

        self.assertIsInstance(prompt, PullPromptResponse)
        assert prompt is not None
        self.assertEqual(prompt.name, "test-prompt")

    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_fetch_single_prompt_not_found(self, mock_exists, mock_call_service):
        """Test single prompt fetch when prompt not found."""
        mock_exists.return_value = True

        # Mock 404 error
        mock_response = MagicMock()
        http_error = requests.exceptions.HTTPError()
        http_error.response = MagicMock()
        http_error.response.status_code = 404
        mock_response.raise_for_status.side_effect = http_error
        mock_call_service.return_value = mock_response

        prompt = self.client._fetch_single_prompt("non-existent")
        self.assertIsNone(prompt)

    @patch.object(PromptsServiceClient, "_fetch_single_prompt")
    @patch.object(PromptsServiceClient, "list_prompts")
    def test_load_all_prompts_success(self, mock_list_prompts, mock_fetch_single):
        """Test successful loading of all prompts."""
        # Mock prompt list
        mock_list_prompts.return_value = ["prompt1", "prompt2", "prompt3"]

        # Mock individual prompt fetches
        mock_prompts = [
            PullPromptResponse(name="prompt1", template="Template 1", description="Desc 1"),
            PullPromptResponse(name="prompt2", template="Template 2", description="Desc 2"),
            PullPromptResponse(name="prompt3", template="Template 3", description="Desc 3"),
        ]
        mock_fetch_single.side_effect = mock_prompts

        # Run async test
        async def run_test():
            result = await self.client.load_all_prompts()
            self.assertTrue(result)

            # Verify all prompts were fetched
            self.assertEqual(mock_fetch_single.call_count, 3)
            mock_list_prompts.assert_called_once()

        asyncio.run(run_test())

    @patch.object(PromptsServiceClient, "list_prompts")
    def test_load_all_prompts_no_prompts(self, mock_list_prompts):
        """Test loading when no prompts are available."""
        mock_list_prompts.return_value = []

        async def run_test():
            result = await self.client.load_all_prompts()
            self.assertFalse(result)

        asyncio.run(run_test())

    @patch.object(PromptsServiceClient, "_fetch_single_prompt")
    @patch.object(PromptsServiceClient, "list_prompts")
    def test_load_all_prompts_partial_failure(self, mock_list_prompts, mock_fetch_single):
        """Test loading prompts with some failures."""
        mock_list_prompts.return_value = ["prompt1", "prompt2", "prompt3"]

        # Mock some successes and some failures
        def mock_fetch_side_effect(name):
            if name == "prompt1":
                return PullPromptResponse(name="prompt1", template="Template 1", description="Desc 1")
            elif name == "prompt2":
                return None  # Simulate failure
            else:
                return PullPromptResponse(name="prompt3", template="Template 3", description="Desc 3")

        mock_fetch_single.side_effect = mock_fetch_side_effect

        async def run_test():
            result = await self.client.load_all_prompts()
            self.assertTrue(result)  # Should still succeed if at least one prompt loads

        asyncio.run(run_test())

    @patch.object(PromptsServiceClient, "_fetch_single_prompt")
    @patch.object(PromptsServiceClient, "list_prompts")
    @patch("cache.prompts_cache.prompt_cache")
    def test_refresh_all_prompts_success(self, mock_cache, mock_list_prompts, mock_fetch_single):
        """Test successful refresh of all prompts."""
        # Setup existing cache
        mock_cache.keys.return_value = ["prompt1", "prompt2"]

        # Mock current prompts list
        mock_list_prompts.return_value = ["prompt1", "prompt3"]  # prompt2 removed, prompt3 added

        # Mock prompt fetches
        mock_prompts = [
            PullPromptResponse(name="prompt1", template="Updated Template 1", description="Updated Desc 1"),
            PullPromptResponse(name="prompt3", template="Template 3", description="Desc 3"),
        ]
        mock_fetch_single.side_effect = mock_prompts

        async def run_test():
            result = await self.client.refresh_all_prompts()
            self.assertTrue(result)

            # Verify prompts were fetched
            self.assertEqual(mock_fetch_single.call_count, 2)
            mock_list_prompts.assert_called_once()

        asyncio.run(run_test())

    @patch.object(PromptsServiceClient, "list_prompts")
    def test_refresh_all_prompts_no_prompts(self, mock_list_prompts):
        """Test refresh when no prompts are available."""
        mock_list_prompts.return_value = []

        async def run_test():
            result = await self.client.refresh_all_prompts()
            self.assertFalse(result)

        asyncio.run(run_test())

    @patch("cache.prompts_cache.wait_for_cache_ready")
    def test_wait_for_cache_ready(self, mock_wait):
        """Test waiting for cache to be ready."""
        mock_wait.return_value = AsyncMock(return_value=True)()

        async def run_test():
            result = await self.client.wait_for_cache_ready(timeout_seconds=30)
            self.assertTrue(result)
            mock_wait.assert_called_once_with(30)

        asyncio.run(run_test())

    @patch("api.services.prompts_client.record_cache_hit")
    @patch("cache.prompts_cache.extend_cache_ttl")
    @patch("cache.prompts_cache.get_cached_prompt")
    def test_get_prompt_cache_hit_tracking(self, mock_get_cached, mock_extend_ttl, mock_record_hit):
        """Test that cache hits are properly tracked."""
        # Mock cache hit
        cached_prompt = PullPromptResponse(name="test-prompt", template="Cached template", description="Cached desc")
        mock_get_cached.return_value = cached_prompt

        prompt = self.client.get_prompt("test-prompt")

        # Verify cache tracking functions were called
        mock_record_hit.assert_called_once_with("test-prompt")
        mock_extend_ttl.assert_called_once_with("test-prompt")
        mock_get_cached.assert_called_once_with("test-prompt")
        self.assertEqual(prompt, cached_prompt)

    @patch("api.services.prompts_client.record_cache_miss")
    @patch("api.services.prompts_client.prompt_cache")
    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_get_prompt_cache_miss_tracking(self, mock_exists, mock_call_service, mock_cache, mock_record_miss):
        """Test that cache misses are properly tracked."""
        mock_exists.return_value = True
        mock_cache.get.return_value = None  # Cache miss

        # Mock successful service call
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "name": "test-prompt",
            "template": "Service template",
            "description": "Service desc",
        }
        mock_call_service.return_value = mock_response

        prompt = self.client.get_prompt("test-prompt")

        # Verify cache miss was recorded
        mock_record_miss.assert_called_once_with("test-prompt")
        self.assertIsNotNone(prompt)


class TestPromptsClientExtended(unittest.TestCase):
    """Extended Prompts Client tests (PRM-011 to PRM-024)."""

    def setUp(self):
        """Set up test fixtures."""
        # Clear the cache before each test
        prompt_cache.clear()

        self.client = PromptsServiceClient()
        self.client.prompts_service_url = "https://test-prompts-service.com"
        self.client.service_key_path = "agents/service-account.json"

    @patch("api.services.prompts_client.prompt_cache")
    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_prm_011_invalid_response_handling(self, mock_exists, mock_call_service, mock_cache):
        """PRM-011: Handle invalid/malformed JSON response."""
        mock_exists.return_value = True
        mock_cache.get.return_value = None

        # Mock malformed JSON response (missing required fields)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "unexpected_field": "value",
            # Missing name, template, description
        }
        mock_call_service.return_value = mock_response

        # Should handle gracefully and return None or raise
        prompt = self.client.get_prompt("malformed-prompt")

        # With pydantic validation, missing required fields should fail
        self.assertIsNone(prompt)

    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_prm_012_timeout_retry(self, mock_exists, mock_call_service):
        """PRM-012: Test retry logic on timeout errors."""
        mock_exists.return_value = True

        # First call times out, second succeeds
        mock_response_success = MagicMock()
        mock_response_success.json.return_value = {
            "name": "test-prompt",
            "template": "Test template",
            "description": "Test description",
        }
        mock_response_success.raise_for_status.return_value = None

        # Simulate timeout on first call, success on retry
        mock_call_service.side_effect = [
            requests.exceptions.Timeout("Connection timed out"),
            mock_response_success,
        ]

        # Use the retry version directly
        prompt = self.client._fetch_single_prompt_with_retry("test-prompt")

        # Should succeed after retry
        self.assertIsNotNone(prompt)
        assert prompt is not None  # Type narrowing for mypy
        self.assertEqual(prompt.name, "test-prompt")
        self.assertEqual(mock_call_service.call_count, 2)  # Initial + 1 retry

    @patch("api.services.prompts_client.prompt_cache")
    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_prm_013_auth_error_401(self, mock_exists, mock_call_service, mock_cache):
        """PRM-013: Handle 401 authentication error (non-retryable)."""
        mock_exists.return_value = True
        mock_cache.get.return_value = None

        # Mock 401 error
        http_error = requests.exceptions.HTTPError()
        http_error.response = MagicMock()
        http_error.response.status_code = 401

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = http_error
        mock_call_service.return_value = mock_response

        prompt = self.client.get_prompt("test-prompt")

        # 401 is non-retryable, should return None
        self.assertIsNone(prompt)

    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_prm_014_server_error_retry(self, mock_exists, mock_call_service):
        """PRM-014: Retry on 500 server errors."""
        mock_exists.return_value = True

        # Mock 500 errors followed by success
        http_error = requests.exceptions.HTTPError()
        http_error.response = MagicMock()
        http_error.response.status_code = 500

        mock_response_fail = MagicMock()
        mock_response_fail.raise_for_status.side_effect = http_error

        mock_response_success = MagicMock()
        mock_response_success.json.return_value = {
            "name": "test-prompt",
            "template": "Test template",
            "description": "Test desc",
        }
        mock_response_success.raise_for_status.return_value = None

        # First two calls fail with 500, third succeeds
        mock_call_service.side_effect = [
            mock_response_fail,
            mock_response_fail,
            mock_response_success,
        ]

        prompt = self.client._fetch_single_prompt_with_retry("test-prompt")

        # Should succeed after retries
        self.assertIsNotNone(prompt)
        self.assertEqual(mock_call_service.call_count, 3)

    def test_prm_022_cache_ttl_expiry(self):
        """PRM-022: Test that cache entries expire after TTL."""
        # Create a cache with very short TTL for testing
        from cachetools import TTLCache

        from api.services.models import PullPromptResponse

        test_cache: TTLCache[str, Any] = TTLCache(maxsize=10, ttl=0.1)  # 100ms TTL

        # Store a prompt
        cached_prompt = PullPromptResponse(name="expiring-prompt", template="Template", description="Desc")
        test_cache["expiring-prompt"] = cached_prompt

        # Verify it's in cache
        self.assertIn("expiring-prompt", test_cache)

        # Wait for TTL to expire
        import time

        time.sleep(0.15)

        # Verify it's expired (not in cache)
        self.assertNotIn("expiring-prompt", test_cache)

    def test_prm_023_cache_invalidation_specific(self):
        """PRM-023: Test specific prompt cache invalidation."""
        from cache.prompts_cache import invalidate_prompt_cache

        # Add prompts to cache
        prompt_cache["prompt-a"] = {"prompt": "A", "_cache_hash": "hash-a"}
        prompt_cache["prompt-b"] = {"prompt": "B", "_cache_hash": "hash-b"}

        self.assertIn("prompt-a", prompt_cache)
        self.assertIn("prompt-b", prompt_cache)

        # Invalidate specific prompt
        invalidate_prompt_cache("prompt-a")

        # prompt-a should be removed, prompt-b should remain
        self.assertNotIn("prompt-a", prompt_cache)
        self.assertIn("prompt-b", prompt_cache)

    def test_prm_024_cache_clear_all(self):
        """PRM-024: Test clearing all cache entries."""
        from cache.prompts_cache import invalidate_prompt_cache, prompts_list_cache

        # Add prompts to cache
        prompt_cache["prompt-1"] = {"prompt": "1", "_cache_hash": "hash-1"}
        prompt_cache["prompt-2"] = {"prompt": "2", "_cache_hash": "hash-2"}
        prompt_cache["prompt-3"] = {"prompt": "3", "_cache_hash": "hash-3"}
        prompts_list_cache["all"] = ["prompt-1", "prompt-2", "prompt-3"]

        self.assertEqual(len(prompt_cache), 3)
        self.assertIn("all", prompts_list_cache)

        # Clear all caches
        invalidate_prompt_cache(None)

        # All should be cleared
        self.assertEqual(len(prompt_cache), 0)
        self.assertNotIn("all", prompts_list_cache)

    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_prm_025_connection_error_retry(self, mock_exists, mock_call_service):
        """PRM-025: Retry on connection errors."""
        mock_exists.return_value = True

        # Mock connection error followed by success
        mock_response_success = MagicMock()
        mock_response_success.json.return_value = {
            "name": "test-prompt",
            "template": "Test template",
            "description": "Test desc",
        }
        mock_response_success.raise_for_status.return_value = None

        mock_call_service.side_effect = [
            requests.exceptions.ConnectionError("Connection refused"),
            mock_response_success,
        ]

        prompt = self.client._fetch_single_prompt_with_retry("test-prompt")

        # Should succeed after retry
        self.assertIsNotNone(prompt)
        self.assertEqual(mock_call_service.call_count, 2)

    @patch("api.services.prompts_client.call_cloud_run_service")
    @patch("os.path.exists")
    def test_prm_026_max_retries_exceeded(self, mock_exists, mock_call_service):
        """PRM-026: Test that retries stop after max attempts."""
        mock_exists.return_value = True

        # All calls fail with retryable error
        mock_call_service.side_effect = requests.exceptions.Timeout("Timeout")

        prompt = self.client._fetch_single_prompt_with_retry("test-prompt")

        # Should fail after max retries (3)
        self.assertIsNone(prompt)
        self.assertEqual(mock_call_service.call_count, 3)  # MAX_RETRY_ATTEMPTS = 3


class TestPromptsClientUnconfiguredLogging(unittest.TestCase):
    """PRM-027..029: SERVICE_PROMPTS is optional, so leaving it unset must not log errors (#137)."""

    def setUp(self):
        prompt_cache.clear()

    def _unconfigured_client(self) -> PromptsServiceClient:
        with patch.dict("os.environ", {"SERVICE_PROMPTS": ""}):
            return PromptsServiceClient()

    def test_prm_027_unset_url_logs_info_once_and_no_key_file_warning(self):
        """PRM-027: no SERVICE_PROMPTS gives one INFO line and no service-account warning."""
        with patch.dict("os.environ", {"SERVICE_PROMPTS": ""}), patch("os.path.exists", return_value=False):
            with self.assertLogs(level="DEBUG") as logs:
                PromptsServiceClient()
        self.assertEqual(
            [(r.levelname, r.getMessage()) for r in logs.records],
            [("INFO", "Prompts service disabled: SERVICE_PROMPTS is not set")],
        )

    def test_prm_028_set_url_without_key_file_still_warns(self):
        """PRM-028: a set URL with no service account file is a misconfiguration and warns."""
        with patch.dict("os.environ", {"SERVICE_PROMPTS": "https://prompts.example"}):
            with patch("os.path.exists", return_value=False), self.assertLogs(level="WARNING") as logs:
                PromptsServiceClient()
        self.assertEqual([r.levelname for r in logs.records], ["WARNING"])
        self.assertIn("Service account file not found", logs.records[0].getMessage())

    def test_prm_029_unconfigured_calls_log_nothing_above_debug(self):
        """PRM-029: list, get, create and delete on an unconfigured client stay below INFO."""
        client = self._unconfigured_client()
        with self.assertLogs(level="DEBUG") as logs:
            self.assertEqual(client.list_prompts(), [])
            self.assertIsNone(client.get_prompt("missing"))
            self.assertFalse(client.create_prompt(PushPromptRequest(name="p", raw_template="t")))
            self.assertFalse(client.delete_prompt("p"))
        above_debug = [(r.levelname, r.getMessage()) for r in logs.records if r.levelno > 10]
        self.assertEqual(above_debug, [])
        self.assertEqual(sum("PromptsServiceClient not configured" in r.getMessage() for r in logs.records), 4)


if __name__ == "__main__":
    unittest.main()
