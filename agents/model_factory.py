from typing import Any, Optional, Union

from agno.models.google import Gemini
from agno.models.openai import OpenAIChat

from agents import Model, ModelProvider, get_provider
from agents.model_resolver import resolve_model_id


def create_model(
    model: Union[Model, str],
    *,
    openai_api_key: Optional[str] = None,
    gemini_api_key: Optional[str] = None,
    anthropic_api_key: Optional[str] = None,
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
        temperature: Optional temperature setting
        max_tokens: Optional max tokens setting

    Returns:
        Configured Agno model instance (OpenAIChat, Gemini, or Claude)

    Raises:
        ValueError: If the provider is unknown or required API key is missing
        ImportError: If anthropic package is not installed (for Claude models)
    """
    model_id = resolve_model_id(model)
    provider = get_provider(model_id)

    if provider == ModelProvider.OPENAI:
        if not openai_api_key:
            raise ValueError("OpenAI API key is required for OpenAI models")

        kwargs: dict[str, Any] = {"id": model_id, "api_key": openai_api_key}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        return OpenAIChat(**kwargs)

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

    else:
        raise ValueError(f"Unknown model provider: {provider}")
