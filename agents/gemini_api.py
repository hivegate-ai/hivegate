"""Pin the Gemini API version for every google-genai client HiveGate creates.

google-genai 3.0 changed the Gemini Developer API client's default API version from
`v1beta` to `v1` (2.34.0: v1beta, 3.0.0: v1). On `v1`, function calling is refused for
gemini-2.5 models ("Function calling is not enabled for api version v1. Use api version
v1beta instead.") and preview models such as `gemini-3-flash-preview`, the default chat
model, are not found at all. The Files API is v1beta-only too. Pinning keeps the
behaviour HiveGate had with google-genai 2.x.

Vertex AI is left alone. Its versions are `v1` and `v1beta1`, there is no `v1beta`, and
its default (`v1beta1`) did not change in 3.0. agno and the SDK both switch to Vertex
when `GOOGLE_GENAI_USE_VERTEXAI=true`, so the same variable decides here.
"""

import os
from typing import Any, Dict

from google.genai.types import HttpOptionsDict

GEMINI_API_VERSION = "v1beta"


def _uses_vertex() -> bool:
    return os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() == "true"


def gemini_http_options() -> HttpOptionsDict:
    """`http_options` for `google.genai.Client(...)`. Empty on Vertex AI, which keeps its own default."""
    if _uses_vertex():
        return {}
    return {"api_version": GEMINI_API_VERSION}


def gemini_client_params() -> Dict[str, Any]:
    """`client_params` for agno's `Gemini` model and `GeminiEmbedder`."""
    return {"http_options": gemini_http_options()}
