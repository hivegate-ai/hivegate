"""Every google-genai client HiveGate builds must call the v1beta API.

google-genai 3.0 defaults to `v1`, where gemini-2.5 models refuse function calling and
preview models (the default `gemini-3-flash-preview`) are not found. See agents/gemini_api.py.
"""

import ast
from pathlib import Path

import pytest
from agno.knowledge.embedder.google import GeminiEmbedder
from google import genai

from agents.gemini_api import GEMINI_API_VERSION, gemini_client_params, gemini_http_options
from agents.model_factory import create_model

REPO = Path(__file__).resolve().parent.parent


def _api_version(client) -> str:
    return client._api_client._http_options.api_version


def test_pinned_version_is_v1beta():
    assert GEMINI_API_VERSION == "v1beta"


@pytest.mark.parametrize("model_id", ["gemini-2.5-flash-lite", "gemini-3-flash-preview"])
def test_chat_model_client_uses_v1beta(model_id):
    model = create_model(model_id, gemini_api_key="test-key")

    client = model.get_client()

    assert _api_version(client) == "v1beta"
    # agno's partner header is merged into the same http_options, not replaced by ours.
    assert client._api_client._http_options.headers["x-goog-api-client"].split()[-1].startswith("agno/")


def test_embedder_client_uses_v1beta():
    embedder = GeminiEmbedder(api_key="test-key", client_params=gemini_client_params())

    assert _api_version(embedder.client) == "v1beta"


def test_vertex_ai_keeps_its_own_default(monkeypatch):
    # Vertex has v1 and v1beta1, no v1beta. Forcing ours would break every call.
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")

    assert gemini_client_params() == {"http_options": {}}
    # Express mode (an API key) builds a Vertex client without Google Cloud credentials.
    client = genai.Client(vertexai=True, api_key="test-key", http_options=gemini_http_options())
    assert _api_version(client) == "v1beta1"


def test_client_params_are_fresh_per_call():
    # agno writes its header into the http_options dict it is given, so a shared dict
    # would leak one client's options into the next.
    assert gemini_client_params() is not gemini_client_params()
    assert gemini_client_params()["http_options"] is not gemini_client_params()["http_options"]


def _calls_missing_version(path: Path) -> list[str]:
    """`genai.Client(...)` without http_options, or agno `Gemini(...)`/`GeminiEmbedder(...)` without client_params."""
    required = {"Client": "http_options", "Gemini": "client_params", "GeminiEmbedder": "client_params"}
    missing = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "Client" and not (isinstance(func, ast.Attribute) and getattr(func.value, "id", None) == "genai"):
            continue
        if name in required and required[name] not in {kw.arg for kw in node.keywords}:
            missing.append(f"{path.relative_to(REPO)}:{node.lineno} {name}(...)")
    return missing


def test_no_gemini_client_is_built_without_the_pinned_version():
    sources = [p for d in ("agents", "api", "toolkits", "workspace_suite") for p in (REPO / d).rglob("*.py")]
    missing = [hit for path in sources for hit in _calls_missing_version(path)]

    assert missing == [], "pass gemini_http_options()/gemini_client_params() (agents/gemini_api.py): " + ", ".join(
        missing
    )
