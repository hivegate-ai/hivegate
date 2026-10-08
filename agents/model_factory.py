import copy
from typing import Any, Optional, Union

from agents import Model, ModelProvider, get_provider
from agents.model_resolver import resolve_model_id
from agents.openai_responses import ReasoningAwareOpenAIResponses
from agents.provider_models import GuardedDeepSeek, GuardedGemini, GuardedOpenAILike, GuardedXAI


class ProviderNotConfiguredError(ValueError):
    """The requested model's provider has no API key on this deployment.

    A ValueError, as before, so existing callers still catch it; the chat routes map
    it to 503 with this message instead of a generic 500 that hides which key is
    missing.
    """


# agno retries a provider error only when told to, and leaves `retries` at 0. So a
# transient 429/5xx - Gemini answering 503 "model is currently experiencing high demand",
# as five Gemini 3.x models did in one integration run on 2026-09-29 - reached the
# caller as the reply itself: HTTP 200 whose content is the provider's error JSON. agno
# never retries 400/401/403/404/413/422 or a context-window error, so a bad key, an
# unknown model or a Claude refusal (422, agents/claude_refusal.py) still fails at once.
# Backoff 2s then 4s: short enough to stay inside a caller's own timeout. The classes
# built here make that safe (agents/provider_models.py): a stream is retried only before
# its first chunk, and a billing error is never retried.
PROVIDER_RETRY: dict[str, Any] = {"retries": 2, "delay_between_retries": 2, "exponential_backoff": True}

# Anthropic prompt caching. Every model round trip re-sends the tools, the system prompt
# and the whole conversation so far; cached, those bill at 0.1x the input price instead
# of 1x (writes at 1.25x). Two breakpoints:
# - cache_system_prompt marks the system prompt, which caches tools + system together
#   (they render first). The gateway passes a fixed per-agent system message, so this
#   prefix is shared by every run of an agent.
# - the top-level cache_control is Anthropic's automatic caching: it marks the last
#   block and moves forward as the conversation grows, so each tool-loop round trip and
#   each follow-up turn reads everything before it from cache.
# Both use the 5-minute TTL; a read refreshes it. Prompts under the model's minimum
# (1024 tokens on Sonnet 4.6, 4096 on Haiku 4.5) are simply not cached - no error.
ANTHROPIC_PROMPT_CACHING: dict[str, Any] = {
    "cache_system_prompt": True,
    "request_params": {"cache_control": {"type": "ephemeral"}},
}


# Z.ai's OpenAI-compatible endpoint (docs.z.ai, "OpenAI Python SDK").
ZAI_BASE_URL = "https://api.z.ai/api/paas/v4/"


def create_model(
    model: Union[Model, str],
    *,
    openai_api_key: Optional[str] = None,
    gemini_api_key: Optional[str] = None,
    anthropic_api_key: Optional[str] = None,
    xai_api_key: Optional[str] = None,
    zai_api_key: Optional[str] = None,
    deepseek_api_key: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Any:
    """
    Factory function to create the appropriate Agno model instance.

    Args:
        model: The Model enum value or model ID string specifying which model to use.
            A tier alias (e.g. "anthropic:sonnet-latest") is resolved to the vendor's
            newest model in that tier; a vendor model id is used as-is.
        openai_api_key: OpenAI API key (required for OpenAI models)
        gemini_api_key: Gemini API key (required for Gemini models)
        anthropic_api_key: Anthropic API key (required for Claude models)
        xai_api_key: xAI API key (required for Grok models)
        zai_api_key: Z.ai API key (required for GLM models)
        deepseek_api_key: DeepSeek API key (required for DeepSeek models)
        temperature: Optional temperature setting
        max_tokens: Optional max tokens setting

    Returns:
        Configured Agno model instance (OpenAI Responses, Gemini, Claude, xAI, Z.ai or DeepSeek)

    Raises:
        ValueError: If the provider is unknown or required API key is missing
        ImportError: If anthropic package is not installed (for Claude models)
    """
    model_id = resolve_model_id(model)
    provider = get_provider(model_id)

    if provider == ModelProvider.OPENAI:
        if not openai_api_key:
            raise ProviderNotConfiguredError("OpenAI API key is required for OpenAI models")

        # Responses API, not Chat Completions: GPT-6 calls tools on Chat Completions
        # only with reasoning off. See agents/openai_responses.py.
        kwargs: dict[str, Any] = {"id": model_id, "api_key": openai_api_key}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens  # the Responses API's name for it
        # Chat Completions stored nothing by default; agno's Responses class would set
        # store=True for every reasoning model and chain turns through OpenAI-held state.
        # store=False keeps conversations off OpenAI's servers, as before: agno then
        # replays the encrypted reasoning items itself.
        kwargs["store"] = False

        return ReasoningAwareOpenAIResponses(**kwargs, **PROVIDER_RETRY)

    elif provider == ModelProvider.GEMINI:
        if not gemini_api_key:
            raise ProviderNotConfiguredError("Gemini API key is required for Gemini models")

        kwargs = {"id": model_id, "api_key": gemini_api_key}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens  # Gemini uses different param name

        return GuardedGemini(**kwargs, **PROVIDER_RETRY)

    elif provider == ModelProvider.ANTHROPIC:
        # Lazy import to handle missing anthropic package gracefully. The refusal-aware
        # subclass raises on a safety refusal that agno would otherwise turn into an
        # empty reply - see agents/claude_refusal.py.
        try:
            from agents.claude_refusal import RefusalAwareClaude
        except ImportError as e:
            raise ImportError("anthropic package is not installed. Install it with: pip install anthropic") from e

        if not anthropic_api_key:
            raise ProviderNotConfiguredError("Anthropic API key is required for Claude models")

        kwargs = {"id": model_id, "api_key": anthropic_api_key, **copy.deepcopy(ANTHROPIC_PROMPT_CACHING)}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        return RefusalAwareClaude(**kwargs, **PROVIDER_RETRY)

    elif provider == ModelProvider.XAI:
        if not xai_api_key:
            raise ProviderNotConfiguredError("xAI API key is required for Grok models")

        # agno's xAI speaks xAI's OpenAI-compatible Chat Completions endpoint.
        # (agno's xAIResponses adds SuperGrok OAuth, which the gateway doesn't use.)
        kwargs = {"id": model_id, "api_key": xai_api_key}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        return GuardedXAI(**kwargs, **PROVIDER_RETRY)

    elif provider == ModelProvider.ZAI:
        if not zai_api_key:
            raise ProviderNotConfiguredError("Z.ai API key is required for GLM models")

        kwargs = {
            "id": model_id,
            "api_key": zai_api_key,
            "base_url": ZAI_BASE_URL,
            "name": "Zai",
            "provider": "Z.ai",
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        return GuardedOpenAILike(**kwargs, **PROVIDER_RETRY)

    elif provider == ModelProvider.DEEPSEEK:
        if not deepseek_api_key:
            raise ProviderNotConfiguredError("DeepSeek API key is required for DeepSeek models")

        kwargs = {"id": model_id, "api_key": deepseek_api_key}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        return GuardedDeepSeek(**kwargs, **PROVIDER_RETRY)

    else:
        raise ValueError(f"Unknown model provider: {provider}")
