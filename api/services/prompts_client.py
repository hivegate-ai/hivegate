import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import requests

from agents.agent_utils import call_cloud_run_service, service_account_key_path
from api.services.models import PullPromptResponse, PushPromptRequest
from cache.prompts_cache import (
    CACHE_TTL,
    extend_cache_ttl,
    invalidate_prompt_cache,
    prompt_cache,
    prompts_list_cache,
    prompts_list_timestamp,
    record_cache_hit,
    record_cache_miss,
)

# Configuration constants
DEFAULT_BATCH_CONCURRENCY = 5
MAX_RETRY_ATTEMPTS = 3
RETRY_WAIT_MIN = 1  # seconds
RETRY_WAIT_MAX = 4  # seconds

def _is_retryable_error(exception: Exception) -> bool:
    """
    Determine if an error should be retried.

    Retryable errors:
    - Network/connection errors
    - HTTP 429 (rate limit)
    - HTTP 500-504 (server errors)

    Non-retryable errors:
    - HTTP 400, 401, 403, 404 (client errors)
    - Other exceptions
    """
    if isinstance(exception, requests.exceptions.HTTPError):
        status_code = exception.response.status_code
        # Retry on rate limit and server errors
        return status_code == 429 or (500 <= status_code <= 504)

    # Retry on connection/timeout errors
    if isinstance(
        exception,
        (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.RequestException,
        ),
    ):
        return True

    return False


class PromptsServiceClient:
    """Client for interacting with the prompts microservice"""

    def __init__(self):
        self.service_key_path = service_account_key_path()
        self.prompts_service_url = os.environ.get("SERVICE_PROMPTS")
        if not os.path.exists(self.service_key_path):
            logging.warning(f"Service account file not found at {self.service_key_path}")

        if not self.prompts_service_url:
            logging.warning("SERVICE_PROMPTS environment variable not set")

    def _is_configured(self) -> bool:
        """Check if the client is properly configured"""
        return bool(self.prompts_service_url and os.path.exists(self.service_key_path))

    def list_prompts(self) -> List[str]:
        """
        Get list of all prompt names from the prompts service.
        Results are cached for CACHE_TTL seconds.

        Returns:
            List[str]: List of prompt names
        """
        global prompts_list_timestamp

        # Check if cache is valid
        current_time = time.time()
        if "all" in prompts_list_cache and (current_time - prompts_list_timestamp) < CACHE_TTL:
            logging.debug("Returning prompts list from cache")
            return prompts_list_cache["all"]

        if not self._is_configured():
            logging.debug("PromptsServiceClient not properly configured")
            return []

        if not self.prompts_service_url:
            logging.error("prompts_service_url is None")
            return []

        try:
            response = call_cloud_run_service(
                service_url=self.prompts_service_url, service_key_path=self.service_key_path
            )
            response.raise_for_status()
            prompts = response.json().get("prompts", [])
            logging.info(f"Retrieved {len(prompts)} prompts from service")
            # Update cache
            prompts_list_cache["all"] = prompts
            prompts_list_timestamp = int(current_time)

            return prompts
        except Exception as e:
            logging.error(f"Error listing prompts: {e}")
            return []

# NOTE: This is a partial file for testing the commit mechanism. Full file will follow.
