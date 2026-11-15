"""Knowledge embedder selection: KNOWLEDGE_EMBEDDER=<provider>[:<model>] (#119).

Kept free of settings and agno imports at module level so api/settings.py can validate the
provider, and so optional embedders (fastembed, voyageai, ...) are only imported when chosen.
"""

import importlib
import logging
from typing import Any, Optional

DEFAULT_KNOWLEDGE_EMBEDDER = "google:gemini-embedding-001"
DEFAULT_KNOWLEDGE_COLLECTION = "unified_knowledge"

# Qdrant's collection size when an embedder reports no dimensions (agno's qdrant.py).
QDRANT_DEFAULT_DIMENSIONS = 1536

# Provider prefix -> (agno module, embedder class). Each needs its own credentials in the
# environment (OPENAI_API_KEY, VOYAGE_API_KEY, OLLAMA_HOST, ...), not the chat model's.
EMBEDDER_PROVIDERS: dict[str, tuple[str, str]] = {
    "google": ("agno.knowledge.embedder.google", "GeminiEmbedder"),
    "openai": ("agno.knowledge.embedder.openai", "OpenAIEmbedder"),
    "azure_openai": ("agno.knowledge.embedder.azure_openai", "AzureOpenAIEmbedder"),
    "voyageai": ("agno.knowledge.embedder.voyageai", "VoyageAIEmbedder"),
    "cohere": ("agno.knowledge.embedder.cohere", "CohereEmbedder"),
    "mistral": ("agno.knowledge.embedder.mistral", "MistralEmbedder"),
    "aws_bedrock": ("agno.knowledge.embedder.aws_bedrock", "AwsBedrockEmbedder"),
    "ollama": ("agno.knowledge.embedder.ollama", "OllamaEmbedder"),
    "fastembed": ("agno.knowledge.embedder.fastembed", "FastEmbedEmbedder"),
    "sentence_transformer": ("agno.knowledge.embedder.sentence_transformer", "SentenceTransformerEmbedder"),
    "huggingface": ("agno.knowledge.embedder.huggingface", "HuggingfaceCustomEmbedder"),
    "jina": ("agno.knowledge.embedder.jina", "JinaEmbedder"),
    "together": ("agno.knowledge.embedder.together", "TogetherEmbedder"),
    "fireworks": ("agno.knowledge.embedder.fireworks", "FireworksEmbedder"),
    "vllm": ("agno.knowledge.embedder.vllm", "VLLMEmbedder"),
}


class EmbedderConfigError(ValueError):
    """KNOWLEDGE_EMBEDDER names an unknown provider or one whose package isn't installed."""


def parse_embedder_spec(spec: str) -> tuple[str, Optional[str]]:
    """Split "<provider>[:<model>]" into (provider, model). The model may itself contain ":".

    A missing model means the provider's default. Raises EmbedderConfigError for an
    unknown provider.
    """
    provider, _, model = spec.strip().partition(":")
    provider = provider.strip().lower()
    if provider not in EMBEDDER_PROVIDERS:
        raise EmbedderConfigError(
            f"Unknown KNOWLEDGE_EMBEDDER provider '{provider}' in '{spec}'. "
            f"Expected <provider>[:<model>] with provider one of: {', '.join(sorted(EMBEDDER_PROVIDERS))}"
        )
    return provider, model.strip() or None


def create_embedder(spec: str, dimensions: Optional[int] = None) -> Any:
    """Build the agno embedder KNOWLEDGE_EMBEDDER names, importing its module lazily."""
    provider, model = parse_embedder_spec(spec)
    module_name, class_name = EMBEDDER_PROVIDERS[provider]
    try:
        embedder_cls = getattr(importlib.import_module(module_name), class_name)
    except ImportError as e:
        raise EmbedderConfigError(
            f"KNOWLEDGE_EMBEDDER={spec} needs a package that isn't installed ({e}). "
            f"Install it, or choose another provider."
        ) from e

    kwargs: dict[str, Any] = {}
    if model:
        kwargs["id"] = model
    if dimensions:
        kwargs["dimensions"] = dimensions
    if provider == "google":
        from agents.gemini_api import gemini_client_params

        kwargs["client_params"] = gemini_client_params()

    # agno's default dimensions belong to its default model (OllamaEmbedder: openhermes,
    # 4096), so another model with no explicit size likely won't fit the collection.
    default_id = getattr(embedder_cls, "id", None)
    if model and model != default_id and dimensions is None:
        logging.warning(
            "KNOWLEDGE_EMBEDDER=%s uses a model other than %s's default (%s) with no "
            "KNOWLEDGE_EMBEDDER_DIMENSIONS; the collection will be sized for %s dimensions. "
            "Set KNOWLEDGE_EMBEDDER_DIMENSIONS to the model's output size if that is wrong.",
            spec,
            class_name,
            default_id,
            getattr(embedder_cls, "dimensions", None) or QDRANT_DEFAULT_DIMENSIONS,
        )
    return embedder_cls(**kwargs)


def embedder_dimensions(embedder: Any) -> int:
    """The vector size Qdrant will use for this embedder's collection."""
    return getattr(embedder, "dimensions", None) or QDRANT_DEFAULT_DIMENSIONS
