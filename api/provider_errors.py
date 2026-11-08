from typing import Any

from fastapi.responses import JSONResponse

from agents import get_provider
from agents.provider_models import BILLING_ERROR_CODE, BILLING_ERROR_MESSAGE, is_billing_message

_PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "zai": "ZAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def error_payload(model: Any, error: Any = None) -> dict[str, Any]:
    """Describe a failed model run without returning provider exception data."""
    model_id = getattr(model, "value", model)
    try:
        key = _PROVIDER_KEYS[get_provider(model_id).value]
    except (ValueError, KeyError):
        key = "the provider API key"
    detail = f"Model provider failed for {model_id}; check {key}, provider credits, and model availability."
    code = "model_provider_error"
    # These checks run only on a known failure, never on a successful answer.
    if is_billing_message(error) or getattr(error, "status_code", None) == 402:
        detail, code = BILLING_ERROR_MESSAGE, BILLING_ERROR_CODE
    elif "stop_reason=refusal" in str(error or ""):
        detail = "The model declined the request (stop_reason=refusal); revise the request before retrying."
        code = "model_refusal_error"
    return {"status": "error", "error": detail, "detail": detail, "error_code": code}


def failed_run_response(response: Any, model: Any) -> JSONResponse | None:
    """Agno returns ERROR run outputs instead of raising provider exceptions."""
    run_status = getattr(response, "status", None)
    if str(getattr(run_status, "value", run_status)).lower() != "error":
        return None
    return JSONResponse(error_payload(model, getattr(response, "content", None)), status_code=502)


def run_error_event(event: dict[str, Any], model: Any) -> dict[str, Any] | None:
    """Sanitize run errors regardless of optional error_type, including team members."""
    if event.get("event") not in ("RunError", "TeamRunError"):
        return None
    result: dict[str, Any] = {
        key: event[key]
        for key in ("event", "run_id", "session_id", "agent_id", "team_id", "parent_run_id", "error_type")
        if key in event
    }
    result.update(error_payload(model, event.get("content")))
    result["content"] = result["detail"]  # Existing chat clients render content.
    return result
