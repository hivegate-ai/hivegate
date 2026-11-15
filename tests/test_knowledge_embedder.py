"""Configurable knowledge embedder: KNOWLEDGE_EMBEDDER, _DIMENSIONS, _COLLECTION (#119)."""

import asyncio
import logging
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from agno.vectordb import qdrant
from pydantic import ValidationError

from api.services.embedders import (
    DEFAULT_KNOWLEDGE_EMBEDDER,
    EmbedderConfigError,
    create_embedder,
    embedder_dimensions,
    parse_embedder_spec,
)
from api.services.knowledge_service import KnowledgeEmbedderMismatchError, KnowledgeService
from api.settings import ApiSettings
from db.db_models import KnowledgeEntryDB
from tests.test_knowledge_service import DummyEmbedder

# ---------------------------------------------------------------------------
# Parsing and building the embedder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("google:gemini-embedding-001", ("google", "gemini-embedding-001")),
        ("openai", ("openai", None)),
        (" OpenAI : text-embedding-3-large ", ("openai", "text-embedding-3-large")),
        # Ollama tags contain ":"; only the first one separates the provider.
        ("ollama:nomic-embed-text:latest", ("ollama", "nomic-embed-text:latest")),
        ("fastembed:BAAI/bge-small-en-v1.5", ("fastembed", "BAAI/bge-small-en-v1.5")),
    ],
)
def test_parse_embedder_spec(spec, expected):
    assert parse_embedder_spec(spec) == expected


def test_parse_embedder_spec_rejects_unknown_provider():
    with pytest.raises(EmbedderConfigError, match="Unknown KNOWLEDGE_EMBEDDER provider 'gemini'"):
        parse_embedder_spec("gemini:gemini-embedding-001")


def test_default_is_todays_gemini_embedder():
    from agno.knowledge.embedder.google import GeminiEmbedder

    embedder = create_embedder(DEFAULT_KNOWLEDGE_EMBEDDER)
    assert isinstance(embedder, GeminiEmbedder)
    assert embedder.id == "gemini-embedding-001"
    assert embedder.dimensions == 1536
    # Still pinned to v1beta (#234).
    assert embedder.client_params is not None and "http_options" in embedder.client_params


def test_openai_embedder_with_dimensions():
    from agno.knowledge.embedder.openai import OpenAIEmbedder

    embedder = create_embedder("openai:text-embedding-3-large", dimensions=3072)
    assert isinstance(embedder, OpenAIEmbedder)
    assert (embedder.id, embedder.dimensions) == ("text-embedding-3-large", 3072)
    assert embedder_dimensions(embedder) == 3072


def test_openai_without_dimensions_falls_back_to_qdrants_default():
    assert embedder_dimensions(create_embedder("openai")) == 1536


def test_non_default_model_without_dimensions_warns(caplog):
    # text-embedding-3-large is 3072-d, but without a size the collection gets Qdrant's 1536.
    with caplog.at_level(logging.WARNING):
        embedder = create_embedder("openai:text-embedding-3-large")
    assert embedder.id == "text-embedding-3-large"
    assert "KNOWLEDGE_EMBEDDER_DIMENSIONS" in caplog.text
    assert "1536 dimensions" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        create_embedder("openai:text-embedding-3-large", dimensions=3072)
        create_embedder("openai")
        create_embedder("openai:text-embedding-3-small")
    assert caplog.text == ""


def test_uninstalled_provider_package_is_a_clear_error():
    # ollama is an optional extra, not in requirements.txt.
    try:
        import ollama  # noqa: F401
    except ImportError:
        with pytest.raises(EmbedderConfigError, match="ollama:nomic-embed-text needs a package"):
            create_embedder("ollama:nomic-embed-text")
    else:
        pytest.skip("ollama is installed")


def test_missing_optional_package_names_it():
    with patch("api.services.embedders.importlib.import_module", side_effect=ImportError("No module named 'voyageai'")):
        with pytest.raises(EmbedderConfigError, match="voyageai.*isn't installed"):
            create_embedder("voyageai:voyage-3")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_defaults_match_todays_behaviour(monkeypatch):
    for var in ("KNOWLEDGE_EMBEDDER", "KNOWLEDGE_EMBEDDER_DIMENSIONS", "KNOWLEDGE_COLLECTION"):
        monkeypatch.delenv(var, raising=False)
    settings = ApiSettings()
    assert settings.knowledge_embedder == "google:gemini-embedding-001"
    assert settings.knowledge_embedder_dimensions is None
    assert settings.knowledge_collection == "unified_knowledge"


def test_settings_blank_values_mean_defaults(monkeypatch):
    # .env.example leaves them blank.
    monkeypatch.setenv("KNOWLEDGE_EMBEDDER", "")
    monkeypatch.setenv("KNOWLEDGE_EMBEDDER_DIMENSIONS", "")
    monkeypatch.setenv("KNOWLEDGE_COLLECTION", " ")
    settings = ApiSettings()
    assert settings.knowledge_embedder == DEFAULT_KNOWLEDGE_EMBEDDER
    assert settings.knowledge_embedder_dimensions is None
    assert settings.knowledge_collection == "unified_knowledge"


def test_settings_read_the_environment(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_EMBEDDER", "ollama:nomic-embed-text")
    monkeypatch.setenv("KNOWLEDGE_EMBEDDER_DIMENSIONS", "768")
    monkeypatch.setenv("KNOWLEDGE_COLLECTION", "knowledge_nomic")
    settings = ApiSettings()
    assert settings.knowledge_embedder == "ollama:nomic-embed-text"
    assert settings.knowledge_embedder_dimensions == 768
    assert settings.knowledge_collection == "knowledge_nomic"


@pytest.mark.parametrize(
    ("var", "value"), [("KNOWLEDGE_EMBEDDER", "bogus:model"), ("KNOWLEDGE_EMBEDDER_DIMENSIONS", "0")]
)
def test_settings_reject_bad_values(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        ApiSettings()


def test_get_knowledge_service_uses_the_settings():
    """An OpenAI-only deployment: no GOOGLE_API_KEY involved in building the service."""
    from agno.knowledge.embedder.openai import OpenAIEmbedder

    import api.services.knowledge_service as ks

    with (
        patch.object(ks, "_knowledge_service", None),
        patch.object(ks.api_settings, "knowledge_embedder", "openai:text-embedding-3-small"),
        patch.object(ks.api_settings, "knowledge_embedder_dimensions", None),
        patch.object(ks.api_settings, "knowledge_collection", "knowledge_openai"),
    ):
        service = ks.get_knowledge_service()
    assert isinstance(service.embedder, OpenAIEmbedder)
    assert service.collection == "knowledge_openai"
    assert service.embedding_metadata() == {
        "embedding_model": "openai:text-embedding-3-small",
        "embedding_dimensions": 1536,
    }


# ---------------------------------------------------------------------------
# An existing collection with a different vector size
# ---------------------------------------------------------------------------


@pytest.fixture
def local_qdrant(tmp_path):
    """Point KnowledgeService at an on-disk Qdrant, so a collection outlives one service."""
    real_qdrant = qdrant.Qdrant
    clients = []

    def on_disk(**kwargs):
        kwargs.pop("location", None)
        vector_db = real_qdrant(path=str(tmp_path), **kwargs)
        clients.append(vector_db)
        return vector_db

    def close_all():
        while clients:
            clients.pop().client.close()

    with (
        patch("api.services.knowledge_service.api_settings") as settings,
        patch("api.services.knowledge_service.qdrant.Qdrant", side_effect=on_disk),
    ):
        settings.qdrant_url = None
        settings.qdrant_api_key = None
        settings.qdrant_port = 6333
        yield close_all
    close_all()


def _service(dim: int) -> KnowledgeService:
    return KnowledgeService(embedder=DummyEmbedder(dim), collection="kb_test", embedder_name=f"dummy:{dim}")


def _url_entry() -> KnowledgeEntryDB:
    return KnowledgeEntryDB(
        id=uuid.uuid4(),
        tenant_id="tenant-1",
        collection_id=None,
        file_id=uuid.uuid4(),
        original_filename="https://example.com/doc",
        file_type="company",
        content_type="text/url",
        gcs_path=None,
        status="active",
        knowledge_status="indexing",
        entry_metadata={},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


def test_matching_collection_indexes_and_records_the_embedder(local_qdrant):
    _service(64).get_dynamic_kb()
    local_qdrant()

    service = _service(64)
    service.get_dynamic_kb()
    assert service.embedder_error is None

    entry = _url_entry()
    with (
        patch.object(service, "_fetch_url_content", return_value="HiveGate keeps knowledge per tenant."),
        patch("api.services.knowledge_service.update_knowledge_entry") as mock_update,
    ):
        assert asyncio.run(service.create_knowledge_base(db=MagicMock(), entry=entry)) is True
    assert mock_update.call_args.args[3] == {"knowledge_status": "indexed"}

    results = asyncio.run(service.search_knowledge(db=MagicMock(), tenant_id="tenant-1", query="knowledge"))
    assert results
    assert results[0]["metadata"]["embedding_model"] == "dummy:64"
    assert results[0]["metadata"]["embedding_dimensions"] == 64


def test_mismatched_collection_is_reported_and_refused(local_qdrant, caplog):
    _service(64).get_dynamic_kb()  # created at 64 dimensions
    local_qdrant()

    service = _service(32)
    with caplog.at_level(logging.ERROR):
        kb = service.get_dynamic_kb()
    # Agents still get a knowledge base: a bad embedder setting never fails a chat.
    assert kb is not None
    message = service.embedder_error or ""
    assert "'kb_test' stores 64-dimension vectors" in message
    assert "dummy:32 produces 32" in message
    assert "KNOWLEDGE_COLLECTION" in message
    assert message in caplog.text

    with pytest.raises(KnowledgeEmbedderMismatchError):
        service.get_compatible_kb()

    # Indexing is refused (status "failed") instead of failing on upsert...
    with (
        patch.object(service, "_fetch_url_content", return_value="text"),
        patch("api.services.knowledge_service.update_knowledge_entry") as mock_update,
    ):
        assert asyncio.run(service.create_knowledge_base(db=MagicMock(), entry=_url_entry())) is False
    assert mock_update.call_args.args[3] == {"knowledge_status": "failed"}

    # ...and so is search.
    assert asyncio.run(service.search_knowledge(db=MagicMock(), tenant_id="tenant-1", query="x")) == []


def test_new_collection_name_avoids_the_mismatch(local_qdrant):
    _service(64).get_dynamic_kb()
    local_qdrant()

    service = KnowledgeService(embedder=DummyEmbedder(32), collection="kb_test_32", embedder_name="dummy:32")
    service.get_dynamic_kb()
    assert service.embedder_error is None
