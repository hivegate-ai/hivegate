import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

import requests

from agents.agent_utils import call_cloud_run_service
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
        self.service_key_path = str(Path(__file__).resolve().parent.parent.parent / "agents" / "service-account.json")
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

    def get_prompt(self, name: str) -> Optional[PullPromptResponse]:
        """
        Get a specific prompt by name from the prompts service.
        Results are cached for CACHE_TTL seconds.

        Args:
            name: The name of the prompt to retrieve

        Returns:
            Optional[PullPromptResponse]: The prompt data or None if not found
        """
        from cache.prompts_cache import (
            extend_cache_ttl,
            get_cached_prompt,
            store_prompt_with_hash,
        )

        # Try to get from cache first
        cached_prompt = get_cached_prompt(name)
        if cached_prompt is not None:
            record_cache_hit(name)
            extend_cache_ttl(name)
            logging.debug(f"Returning prompt '{name}' from cache")
            return cached_prompt

        record_cache_miss(name)

        if not self._is_configured():
            logging.debug("PromptsServiceClient not properly configured")
            return None

        try:
            response = call_cloud_run_service(
                service_url=f"{self.prompts_service_url}/{name}", service_key_path=self.service_key_path
            )
            response.raise_for_status()

            data = response.json()
            prompt = PullPromptResponse.model_validate(data)
            logging.info(f"Retrieved prompt '{name}' from service")

            # Cache the result with hash for change detection
            store_prompt_with_hash(name, prompt)

            return prompt
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logging.warning(f"Prompt '{name}' not found in service")
                return None
            logging.error(f"HTTP error getting prompt '{name}': {e}")
            return None
        except Exception as e:
            logging.error(f"Error getting prompt '{name}': {e}")
            return None

    def _fetch_single_prompt_with_retry(self, name: str, attempt: int = 1) -> Optional[PullPromptResponse]:
        """
        Fetch a single prompt with retry logic for transient failures.

        Args:
            name: The prompt name to fetch
            attempt: Current attempt number (for logging)

        Returns:
            Optional[PullPromptResponse]: The prompt data or None
        """
        if not self._is_configured():
            return None

        max_attempts = MAX_RETRY_ATTEMPTS
        wait_seconds = RETRY_WAIT_MIN

        for current_attempt in range(1, max_attempts + 1):
            try:
                if current_attempt > 1:
                    logging.info(f"Retrying prompt fetch for '{name}' (attempt {current_attempt}/{max_attempts})")
                    time.sleep(wait_seconds)
                    wait_seconds = min(wait_seconds * 2, RETRY_WAIT_MAX)  # Exponential backoff

                response = call_cloud_run_service(
                    service_url=f"{self.prompts_service_url}/{name}", service_key_path=self.service_key_path
                )
                response.raise_for_status()

                data = response.json()
                prompt = PullPromptResponse.model_validate(data)

                if current_attempt > 1:
                    logging.info(f"Successfully fetched prompt '{name}' after {current_attempt} attempts")
                else:
                    logging.debug(f"Fetched prompt '{name}' for batch operation")

                return prompt

            except requests.exceptions.HTTPError as e:
                # Non-retryable errors (404, 400, 401, 403)
                if e.response.status_code == 404:
                    logging.warning(f"Prompt '{name}' not found in service (HTTP 404)")
                    return None
                elif e.response.status_code in (400, 401, 403):
                    logging.error(f"HTTP {e.response.status_code} error fetching prompt '{name}': {e}")
                    return None

                # Retryable errors (429, 500-504)
                if _is_retryable_error(e):
                    if current_attempt < max_attempts:
                        logging.warning(
                            f"Retryable HTTP error fetching prompt '{name}' (HTTP {e.response.status_code}), "
                            f"will retry (attempt {current_attempt}/{max_attempts})"
                        )
                        continue
                    else:
                        logging.error(
                            f"Failed to fetch prompt '{name}' after {max_attempts} attempts (HTTP {e.response.status_code}): {e}"
                        )
                        return None
                else:
                    logging.error(f"HTTP error fetching prompt '{name}': {e}")
                    return None

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                # Retryable network errors
                if current_attempt < max_attempts:
                    logging.warning(
                        f"Network error fetching prompt '{name}': {type(e).__name__}, "
                        f"will retry (attempt {current_attempt}/{max_attempts})"
                    )
                    continue
                else:
                    logging.error(
                        f"Failed to fetch prompt '{name}' after {max_attempts} attempts due to network error: {e}"
                    )
                    return None

            except Exception as e:
                # Non-retryable errors
                logging.error(f"Error fetching prompt '{name}': {e}")
                return None

        # Should not reach here, but just in case
        return None

    def _fetch_single_prompt(self, name: str) -> Optional[PullPromptResponse]:
        """Fetch a single prompt without cache interaction (for batch operations). Includes retry logic."""
        return self._fetch_single_prompt_with_retry(name)

    async def load_all_prompts(self) -> bool:
        """
        Load all prompts from the service and cache them.
        Concurrency limited to avoid overwhelming the prompts service.

        Returns:
            bool: True if successful, False otherwise
        """
        logging.info("Starting batch load of all prompts")

        try:
            # Get list of all prompt names
            prompt_names = self.list_prompts()
            if not prompt_names:
                logging.warning("No prompts found or failed to fetch prompt list")
                return False

            concurrency = int(os.environ.get("PROMPTS_BATCH_CONCURRENCY", DEFAULT_BATCH_CONCURRENCY))
            logging.info(f"Found {len(prompt_names)} prompts to load (concurrency: {concurrency})")

            # Use ThreadPoolExecutor for concurrent fetching with reduced concurrency
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                # Submit all fetch operations
                future_to_name = {executor.submit(self._fetch_single_prompt, name): name for name in prompt_names}

                # Collect results
                successful_loads = 0
                for future in future_to_name:
                    name = future_to_name[future]
                    try:
                        prompt = future.result()
                        if prompt:
                            # Use new cache format with hash
                            from cache.prompts_cache import store_prompt_with_hash

                            store_prompt_with_hash(name, prompt)
                            successful_loads += 1
                    except Exception as e:
                        logging.error(f"Error processing future for prompt '{name}': {e}")

            logging.info(f"Batch load completed: {successful_loads}/{len(prompt_names)} prompts loaded")
            return successful_loads > 0

        except Exception as e:
            logging.error(f"Error during batch prompt loading: {e}")
            return False

    async def refresh_all_prompts(self) -> bool:
        """
        Refresh all cached prompts and remove any that no longer exist.
        Concurrency limited to avoid overwhelming the prompts service.

        Returns:
            bool: True if successful, False otherwise
        """
        logging.info("Starting batch refresh of all prompts")

        try:
            # Get current list of prompt names
            current_prompt_names = self.list_prompts()
            if not current_prompt_names:
                logging.warning("No prompts found or failed to fetch prompt list during refresh")
                return False

            current_prompt_set = set(current_prompt_names)
            cached_prompt_set = set(prompt_cache.keys())

            # Find prompts to remove (no longer exist in service)
            prompts_to_remove = cached_prompt_set - current_prompt_set
            for name in prompts_to_remove:
                if name in prompt_cache:
                    del prompt_cache[name]
                    logging.debug(f"Removed outdated prompt '{name}' from cache")

            if prompts_to_remove:
                logging.info(f"Removed {len(prompts_to_remove)} outdated prompts from cache")

            concurrency = int(os.environ.get("PROMPTS_BATCH_CONCURRENCY", DEFAULT_BATCH_CONCURRENCY))
            logging.info(f"Refreshing {len(current_prompt_names)} prompts (concurrency: {concurrency})")

            # Refresh all current prompts with reduced concurrency
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                future_to_name = {
                    executor.submit(self._fetch_single_prompt, name): name for name in current_prompt_names
                }

                successful_refreshes = 0
                for future in future_to_name:
                    name = future_to_name[future]
                    try:
                        prompt = future.result()
                        if prompt:
                            # Update cache with fresh data and extend TTL
                            prompt_cache[name] = prompt
                            successful_refreshes += 1
                    except Exception as e:
                        logging.error(f"Error processing refresh future for prompt '{name}': {e}")

            logging.info(
                f"Batch refresh completed: {successful_refreshes}/{len(current_prompt_names)} prompts refreshed"
            )
            return successful_refreshes > 0

        except Exception as e:
            logging.error(f"Error during batch prompt refresh: {e}")
            return False

    async def smart_refresh_all_prompts(self) -> bool:
        """
        Smart refresh: only update prompts that have actually changed.
        Uses content hashing to detect changes before updating cache.

        Returns:
            bool: True if successful, False otherwise
        """
        logging.info("Starting smart refresh of all prompts")

        try:
            # Get current list of prompt names
            current_prompt_names = self.list_prompts()
            if not current_prompt_names:
                logging.warning("No prompts found or failed to fetch prompt list during smart refresh")
                return False

            current_prompt_set = set(current_prompt_names)
            cached_prompt_set = set(prompt_cache.keys())

            # Find prompts to remove (no longer exist in service)
            prompts_to_remove = cached_prompt_set - current_prompt_set
            for name in prompts_to_remove:
                if name in prompt_cache:
                    del prompt_cache[name]
                    logging.debug(f"Removed outdated prompt '{name}' from cache")

            if prompts_to_remove:
                logging.info(f"Removed {len(prompts_to_remove)} outdated prompts from cache")

            concurrency = int(os.environ.get("PROMPTS_BATCH_CONCURRENCY", DEFAULT_BATCH_CONCURRENCY))
            logging.info(f"Smart refreshing {len(current_prompt_names)} prompts (concurrency: {concurrency})")

            # Smart refresh: check hash before updating
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                future_to_name = {
                    executor.submit(self._fetch_single_prompt, name): name for name in current_prompt_names
                }

                updated_count = 0
                skipped_count = 0
                for future in future_to_name:
                    name = future_to_name[future]
                    try:
                        fresh_prompt = future.result()
                        if fresh_prompt:
                            # Check if content has changed using hash
                            from cache.prompts_cache import get_cached_prompt_hash, store_prompt_with_hash

                            cached_hash = get_cached_prompt_hash(name)
                            fresh_hash = store_prompt_with_hash(name, fresh_prompt, return_hash=True)

                            if cached_hash != fresh_hash:
                                updated_count += 1
                                logging.debug(
                                    f"Updated prompt '{name}' (hash changed: {cached_hash[:8] if cached_hash else 'none'} -> {fresh_hash[:8]})"
                                )
                            else:
                                # Content unchanged, just extend TTL
                                extend_cache_ttl(name)
                                skipped_count += 1
                                logging.debug(f"Skipped prompt '{name}' (unchanged)")

                    except Exception as e:
                        logging.error(f"Error processing smart refresh future for prompt '{name}': {e}")

                # Update statistics
                cache_statistics["prompts_updated"] += updated_count
                cache_statistics["prompts_skipped_unchanged"] += skipped_count

                logging.info(
                    f"Smart refresh completed: {updated_count} updated, {skipped_count} skipped (unchanged), "
                    f"{len(prompts_to_remove)} removed"
                )
                return True

        except Exception as e:
            logging.error(f"Error during smart prompt refresh: {e}")
            return False

    async def wait_for_cache_ready(self, timeout_seconds: int = 30) -> bool:
        """
        Wait for the cache to be initialized.

        Args:
            timeout_seconds: Maximum time to wait in seconds

        Returns:
            bool: True if cache is ready, False if timeout
        """
        import asyncio

        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            if prompts_list_cache.get("all") is not None:
                return True
            await asyncio.sleep(0.5)
        return False

    def create_prompt(self, prompt_request: PushPromptRequest) -> bool:
        """
        Create a new prompt in the prompts service.

        Args:
            prompt_request: The prompt data to create

        Returns:
            bool: True if successful, False otherwise
        """
        if not self._is_configured():
            logging.debug("PromptsServiceClient not properly configured")
            return False

        try:
            # Use requests directly for POST since call_cloud_run_service only supports GET
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account

            # Load credentials and get token
            creds = service_account.IDTokenCredentials.from_service_account_file(
                self.service_key_path, target_audience=self.prompts_service_url
            )
            creds.refresh(Request())

            headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}

            if not self.prompts_service_url:
                logging.error("prompts_service_url is None")
                return False

            response = requests.post(self.prompts_service_url, json=prompt_request.model_dump(), headers=headers)
            response.raise_for_status()

            # Invalidate cache after successful creation
            invalidate_prompt_cache()
            logging.info(f"Created prompt '{prompt_request.name}' in service and invalidated cache")
            return True
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 409:
                logging.info(f"Prompt '{prompt_request.name}' already exists in service - considering this a success")
                # Invalidate cache to ensure we get the latest version if needed
                invalidate_prompt_cache()
                return True
            else:
                logging.error(f"HTTP error creating prompt '{prompt_request.name}': {e}")
                return False
        except Exception as e:
            logging.error(f"Error creating prompt '{prompt_request.name}': {e}")
            return False

    def delete_prompt(self, name: str) -> bool:
        """
        Delete a prompt from the prompts service.

        Args:
            name: The name of the prompt to delete

        Returns:
            bool: True if successful, False otherwise
        """
        if not self._is_configured():
            logging.debug("PromptsServiceClient not properly configured")
            return False

        try:
            # Use requests directly for DELETE since call_cloud_run_service only supports GET
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account

            # Load credentials and get token
            creds = service_account.IDTokenCredentials.from_service_account_file(
                self.service_key_path, target_audience=self.prompts_service_url
            )
            creds.refresh(Request())

            headers = {"Authorization": f"Bearer {creds.token}"}

            if not self.prompts_service_url:
                logging.error("prompts_service_url is None")
                return False

            response = requests.delete(f"{self.prompts_service_url}/{name}", headers=headers)
            response.raise_for_status()

            # Invalidate cache after successful deletion
            invalidate_prompt_cache()
            logging.info(f"Deleted prompt '{name}' from service and invalidated cache")
            return True
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logging.warning(f"Prompt '{name}' not found in service for deletion")
                return False
            logging.error(f"HTTP error deleting prompt '{name}': {e}")
            return False
        except Exception as e:
            logging.error(f"Error deleting prompt '{name}': {e}")
            return False


# Singleton instance
prompts_client = PromptsServiceClient()
