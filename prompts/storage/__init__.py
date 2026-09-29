import os
from typing import TYPE_CHECKING

from prompts.storage.base import PromptStorageBackend

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def get_prompt_storage(db: "Session | None" = None) -> PromptStorageBackend:
    """Factory function to get the configured prompt storage backend.

    Environment variable PROMPT_STORAGE_BACKEND controls which backend to use:
    - "postgres" (default): Use PostgreSQL database
    - "langsmith": Use LangSmith (requires `pip install hivegate[langsmith]`)
    - "service": Use external prompts service (requires SERVICE_PROMPTS env var)

    Args:
        db: SQLAlchemy session (required for postgres backend)

    Returns:
        Configured storage backend instance
    """
    backend = os.getenv("PROMPT_STORAGE_BACKEND", "postgres").lower()

    if backend == "langsmith":
        try:
            from prompts.storage.langsmith import LangSmithStorage

            return LangSmithStorage()
        except ImportError as e:
            raise ImportError(
                "LangSmith storage requires additional dependencies. Install with: pip install hivegate[langsmith]"
            ) from e

    if backend == "service":
        from prompts.storage.service import PromptsServiceStorage

        return PromptsServiceStorage()

    # Default to postgres
    if db is None:
        raise ValueError("PostgreSQL storage requires a database session")

    from prompts.storage.postgres import PostgresStorage

    return PostgresStorage(db)


__all__ = ["PromptStorageBackend", "get_prompt_storage"]
