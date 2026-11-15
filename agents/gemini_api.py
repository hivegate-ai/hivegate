"""Pin the Gemini API version for every google-genai client HiveGate creates.

google-genai 3.0 changed the client's default API version from `v1beta` to `v1`
(2.34.0: v1beta, 3.0.0: v1). On `v1`, function calling is refused for gemini-2.5 models
("Function calling is not enabled for api version v1. Use api version v1beta instead.")
and preview models such as `gemini-3-flash-preview`, the default chat model, are not
found at all. The Files API is v1beta-only too. Pinning keeps the behaviour HiveGate had
with google-genai 2.x.
"""

from typing import Any, Dict

GEMINI_API_VERSION = "v1beta"


def gemini_http_options() -> Dict[str, Any]:
    """`http_options` for `google.genai.Client(...)`."""
    return {"api_version": GEMINI_API_VERSION}


def gemini_client_params() -> Dict[str, Any]:
    """`client_params` for agno's `Gemini` model and `GeminiEmbedder`."""
    return {"http_options": gemini_http_options()}
