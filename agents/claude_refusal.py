"""Make Anthropic safety refusals loud instead of silent.

When a Claude safety classifier declines a request, the API answers HTTP 200 with
`stop_reason: "refusal"`, a `stop_details` object naming the category, and little or
no content. agno's Claude adapter never reads that stop reason - checked in 2.6.20
(deployed) and 3.0.11 (latest) - so a refusal becomes an empty assistant message: the
run "completes", the stream carries nothing, and the caller can only guess from the
silence.

That is exactly how the 2026-09-29 pf-budget-classifier outage looked from outside.
Every call on claude-sonnet-5-5 billed 10-33k input tokens and reported 0 output
tokens in Anthropic's own usage, with no error anywhere, and nothing recorded *why*.

`RefusalAwareClaude` raises `ModelRefusalError` instead, carrying the category and
explanation. agno turns a model exception into a RunError event whose content is the
error text, which the chat streamers forward to the client - so the reason reaches
the caller and the logs rather than being dropped.
"""

from typing import Any, NoReturn, Optional, Tuple

from agno.exceptions import ModelProviderError
from agno.models.anthropic import Claude

from agents.provider_models import StreamRestartGuard
from agno.utils.log import log_error

# Stable, greppable text for callers that classify the error from the stream body
# (personal-finance's classifier does). Keep it free of request content.
REFUSAL_MARKER = "stop_reason=refusal"


class ModelRefusalError(ModelProviderError):
    """A Claude safety classifier declined the request.

    Deterministic for a given request, so callers should not retry it: every retry
    re-sends and re-bills the full input for the same verdict.
    """

    def __init__(
        self,
        category: Optional[str],
        explanation: Optional[str],
        model_id: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        self.category = category
        self.explanation = explanation
        message = (
            f"Claude declined the request ({REFUSAL_MARKER}, category={category or 'unspecified'}, model={model_id})"
        )
        if explanation:
            message += f": {explanation}"
        # 422: the request was well-formed and was declined, which is neither a
        # provider fault (agno's 502 default) nor a rate limit.
        super().__init__(message=message, status_code=422, model_name=model_name, model_id=model_id)
        self.type = "model_refusal_error"
        self.error_id = "model_refusal_error"


def refusal_of(message: Any) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """(category, explanation) if this Anthropic message is a refusal, else None."""
    if message is None or getattr(message, "stop_reason", None) != "refusal":
        return None
    details = getattr(message, "stop_details", None)
    return getattr(details, "category", None), getattr(details, "explanation", None)


class RefusalAwareClaude(StreamRestartGuard, Claude):
    """agno's Claude, except a refusal raises instead of returning empty content."""

    def _raise_if_refused(self, message: Any) -> None:
        refusal = refusal_of(message)
        if refusal is None:
            return
        category, explanation = refusal
        error = ModelRefusalError(category, explanation, model_id=self.id, model_name=self.name)
        log_error(str(error))
        raise error

    def _parse_provider_response(self, response: Any, *args: Any, **kwargs: Any) -> Any:
        # Non-streaming: the whole Message is here.
        self._raise_if_refused(response)
        return super()._parse_provider_response(response, *args, **kwargs)

    def _parse_provider_response_delta(self, response: Any, *args: Any, **kwargs: Any) -> Any:
        # Streaming: the SDK's stream helper ends with a message_stop event carrying the
        # accumulated message, which is where stop_reason and stop_details live. A
        # refusal after some text has streamed still raises - the answer is incomplete.
        if getattr(response, "type", None) == "message_stop":
            self._raise_if_refused(getattr(response, "message", None))
        return super()._parse_provider_response_delta(response, *args, **kwargs)

    def _handle_api_error(self, e: Exception) -> NoReturn:
        # agno routes every exception raised during a call through this handler, which
        # re-wraps anything unrecognised as a generic 502 ModelProviderError. Let ours
        # through intact so the category and the no-retry status survive.
        if isinstance(e, ModelRefusalError):
            raise e
        super()._handle_api_error(e)


# agno recognises Claude for its native-thinking reasoning path by class *name*:
# `reasoning_model.__class__.__name__ == "Claude"` in agno/reasoning/anthropic.py (2.6.20
# and 3.0.11 alike - the only name check in agno). Under its own name this subclass fails
# it, so an agent that configured `thinking` to reason natively would silently fall back
# to agno's manual chain-of-thought step - the very call Sonnet 5.5 refuses as
# reasoning_extraction. Presenting as "Claude" keeps agno's behaviour identical to the
# class it extends; isinstance checks were never affected.
RefusalAwareClaude.__name__ = "Claude"
RefusalAwareClaude.__qualname__ = "Claude"
