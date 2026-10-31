import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from api.services import str_to_bool
from api.services.models import PullPromptResponse


def call_cloud_run_service(
    service_url: str,
    service_key_path: str,
    path_params: Optional[Dict[str, Any]] = None,
    query_params: Optional[Dict[str, Any]] = None,
) -> requests.Response:
    """
    Authenticates with a Google Cloud Run service using a service account and ID token,
    then makes an authenticated GET request with optional path and query parameters.

    Args:
        service_url (str): The base URL of the Cloud Run service (can contain placeholders).
        service_key_path (str): Path to the service account JSON key file.
        path_params (Optional[Dict[str, Any]]): Dictionary for replacing placeholders in the URL.
        query_params (Optional[Dict[str, Any]]): Dictionary of query parameters.

    Returns:
        requests.Response: The HTTP response object.
    """
    # Inject path parameters into the URL if needed
    if path_params:
        service_url = service_url.format(**path_params)

    # Load credentials
    creds = service_account.IDTokenCredentials.from_service_account_file(service_key_path, target_audience=service_url)

    # Refresh to get ID token
    creds.refresh(Request())
    token = creds.token

    # Make authenticated request
    headers = {"Authorization": f"Bearer {token}"}

    return requests.get(service_url, headers=headers, params=query_params)


def fetch_prompt_names(prompts_service_url: str, service_key_path: str) -> List[str]:
    """Call the main prompt list endpoint and return the list of prompt names."""
    response = call_cloud_run_service(service_url=prompts_service_url, service_key_path=service_key_path)
    response.raise_for_status()
    return response.json().get("prompts", [])


def fetch_prompt_detail(prompts_service_url: str, name: str, service_key_path: str) -> Dict:
    """Call the individual prompt endpoint by name."""
    try:
        response = call_cloud_run_service(
            service_url=f"{prompts_service_url}/{name}", service_key_path=service_key_path
        )
        response.raise_for_status()
        logging.info(f"Prompt '{name}' fetched")
        return {"name": name, "status": "success", "data": response.json()}
    except Exception as e:
        logging.error(f"{name} failed: {e}")
        return {"name": name, "status": "error", "error": str(e)}


def service_account_key_path() -> str:
    """Where the prompts service key lives. The default is inside the package, which only suits a clone."""
    return os.environ.get("PROMPTS_SERVICE_ACCOUNT_KEY_PATH") or str(
        Path(__file__).resolve().parent / "service-account.json"
    )


def retrieve_prompts():
    try:
        service_key_path = service_account_key_path()
        if not os.path.exists(service_key_path):
            logging.warning(f"Service account file not found at {service_key_path}. Skipping prompt retrieval.")
            return {}

        prompts_service_url = os.environ.get("SERVICE_PROMPTS")
        if not prompts_service_url:
            logging.warning("SERVICE_PROMPTS environment variable not set. Skipping prompt retrieval.")
            return {}

        prompt_names = fetch_prompt_names(prompts_service_url, service_key_path)

        if str_to_bool(os.environ.get("TESTING_V1_BOOTSTRAP_FAST", "False")):
            prompt_names = prompt_names[:3]

        loaded_prompts = {}
    except Exception as e:
        logging.error(f"Error initializing prompt retrieval: {e}")
        return {}

    # Use a timeout to prevent hanging during startup
    import concurrent.futures

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(fetch_prompt_detail, prompts_service_url, name, service_key_path): name
            for name in prompt_names
        }

        # Get timeout from environment variable or use default of 180 seconds
        timeout = int(os.environ.get("CONCURRENT_FUTURES_TIMEOUT", 180))
        try:
            for future in concurrent.futures.as_completed(futures, timeout=timeout):
                name = futures[future]
                try:
                    response = future.result()
                    if not isinstance(response, dict):
                        logging.warning(f"Response for '{name}' is not a dict: {response}")
                        continue

                    data = response.get("data")
                    if not isinstance(data, dict):
                        logging.error(f"Prompt '{name}' has invalid or missing 'data' field: {response}")
                        continue

                    if "template" not in data:
                        logging.error(f"Prompt '{name}' missing required 'template' field: {response}")
                        continue

                    prompt: PullPromptResponse = PullPromptResponse.model_validate(data)
                    loaded_prompts[name] = prompt
                except Exception as e:
                    logging.error(f"Error loading prompt '{name}': {e}")
        except concurrent.futures.TimeoutError:
            logging.warning("Timeout reached while fetching prompts. Continuing with partial results.")
            # Cancel any remaining futures
            for future in futures:
                if not future.done():
                    future.cancel()

    logging.info(f"{len(loaded_prompts)} of {len(prompt_names)} prompts fetched successfully")
    return loaded_prompts
