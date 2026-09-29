from typing import Any, Optional, Union

from agno.models.deepseek import DeepSeek
from agno.models.google import Gemini
from agno.models.openai.like import OpenAILike
from agno.models.xai import xAI

from agents import Model, ModelProvider, get_provider
from agents.model_resolver import resolve_model_id
from agents.openai_responses import ReasoningAwareOpenAIResponses

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
            raise ValueError("OpenAI API key is required for OpenAI models")

        # Responses API, not Chat Completions: GPT-6 calls tools on Chat Completions
        # only with reasoning off. See agents/openai_responses.py.
        kwargs: dict[str, Any] = {"id": model_id, "api_key": openai_api_key}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens  # the Responses API's name for it

        return ReasoningAwareOpenAIResponses(**kwargs)

    elif provider == ModelProvider.GEMINI:
        if not gemini_api_key:
            raise ValueError("Gemini API key is required for Gemini models")

        kwargs = {"id": model_id, "api_key": gemini_api_key}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens  # Gemini uses different param name

        return Gemini(**kwargs)

    elif provider == ModelProvider.ANTHROPIC:
        # Lazy import to handle missing anthropic package gracefully. The refusal-aware
        # subclass raises on a safety refusal that agno would otherwise turn into an
        # empty reply - see agents/claude_refusal.py.
        try:
            from agents.claude_refusal import RefusalAwareClaude
        except ImportError as e:
            raise ImportError("anthropic package is not installed. Install it with: pip install anthropic") from e

        if not anthropic_api_key:
            raise ValueError("Anthropic API key is required for Claude models")

        kwargs = {"id": model_id, "api_key": anthropic_api_key}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        return RefusalAwareClaude(**kwargs)

    elif provider == ModelProvider.XAI:
        if not xai_api_key:
            raise ValueError("xAI API key is required for Grok models")

        # agno's xAI speaks xAI's OpenAI-compatible Chat Completions endpoint.
        # (agno's xAIResponses adds SuperGrok OAuth, which the gateway doesn't use.)
        kwargs = {"id": model_id, "api_key": xai_api_key}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        return xAI(**kwargs)

    elif provider == ModelProvider.ZAI:
        if not zai_api_key:
            raise ValueError("Z.ai API key is required for GLM models")

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

        return OpenAILike(**kwargs)

    elif provider == ModelProvider.DEEPSEEK:
        if not deepseek_api_key:
            raise ValueError("DeepSeek API key is required for DeepSeek models")

        kwargs = {"id": model_id, "api_key": deepseek_api_key}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        return DeepSeek(**kwargs)

    else:
        raise ValueError(f"Unknown model provider: {provider}")
