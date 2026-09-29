import logging
import os
from typing import Any

from prompts.models import PromptData
from prompts.storage.base import PromptStorageBackend

logger = logging.getLogger(__name__)


class LangSmithStorage(PromptStorageBackend):
    """LangSmith-based prompt storage backend.

    Requires:
        - LANGCHAIN_API_KEY environment variable
        - Optional: LANGCHAIN_HUB_URL for custom LangSmith instance

    Install with: pip install hivegate[langsmith]
    """

    def __init__(self):
        """Initialize LangSmith storage."""
        try:
            from langsmith import Client  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "LangSmith storage requires langsmith package. Install with: pip install hivegate[langsmith]"
            ) from e

        api_key = os.getenv("LANGCHAIN_API_KEY")
        if not api_key:
            raise ValueError("LANGCHAIN_API_KEY environment variable is required for LangSmith storage")

        self.client = Client(api_key=api_key)
        self._prompts_cache: dict[str, PromptData] = {}
        logger.info("Initialized LangSmith storage backend")

    def create(
        self,
        prompt_id: str,
        name: str,
        template: str,
        description: str | None = None,
        tags: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> PromptData:
        """Create a new prompt in LangSmith Hub.

        Note: LangSmith Hub uses a push model, so we create a prompt
        by pushing it to the hub.
        """
        try:
            from langchain_core.prompts import ChatPromptTemplate  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError("langchain-core is required for LangSmith storage") from e

        # Create a ChatPromptTemplate
        prompt_template = ChatPromptTemplate.from_template(template)

        # Push to LangSmith Hub
        # The prompt_id becomes the repo name in the hub
        hub_url = self.client.push_prompt(prompt_id, object=prompt_template)

        prompt_data = PromptData(
            id=prompt_id,
            name=name,
            template=template,
            description=description,
            tags=tags or [],
            tools=tools or [],
            version=1,
        )

        self._prompts_cache[prompt_id] = prompt_data
        logger.info(f"Created prompt in LangSmith Hub: {prompt_id} -> {hub_url}")
        return prompt_data

    def get(self, prompt_id: str) -> PromptData | None:
        """Get a prompt from LangSmith Hub."""
        # Check cache first
        if prompt_id in self._prompts_cache:
            return self._prompts_cache[prompt_id]

        try:
            from langsmith import hub
        except ImportError as e:
            raise ImportError("langsmith hub is required for LangSmith storage") from e

        try:
            # Pull from LangSmith Hub
            prompt = hub.pull(prompt_id)

            # Extract template from the prompt object
            if hasattr(prompt, "messages") and prompt.messages:
                template = str(prompt.messages[0].prompt.template)
            elif hasattr(prompt, "template"):
                template = prompt.template
            else:
                template = str(prompt)

            prompt_data = PromptData(
                id=prompt_id,
                name=prompt_id,  # LangSmith doesn't store a separate name
                template=template,
            )

            self._prompts_cache[prompt_id] = prompt_data
            return prompt_data
        except Exception as e:
            logger.warning(f"Failed to get prompt from LangSmith: {prompt_id} - {e}")
            return None

    def get_all(self) -> list[PromptData]:
        """Get all prompts.

        Note: LangSmith Hub doesn't have a simple list API for all prompts,
        so we return cached prompts.
        """
        return list(self._prompts_cache.values())

    def update(
        self,
        prompt_id: str,
        name: str | None = None,
        template: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> PromptData | None:
        """Update a prompt in LangSmith Hub.

        Note: Updates are done by pushing a new version.
        """
        existing = self.get(prompt_id)
        if not existing:
            return None

        try:
            from langchain_core.prompts import ChatPromptTemplate  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError("langchain-core is required for LangSmith storage") from e

        # Use new template or existing
        new_template = template if template is not None else existing.template

        # Create and push updated prompt
        prompt_template = ChatPromptTemplate.from_template(new_template)
        self.client.push_prompt(prompt_id, object=prompt_template)

        # Update local data
        prompt_data = PromptData(
            id=prompt_id,
            name=name if name is not None else existing.name,
            template=new_template,
            description=description if description is not None else existing.description,
            tags=tags if tags is not None else existing.tags,
            tools=tools if tools is not None else existing.tools,
            version=existing.version + 1,
        )

        self._prompts_cache[prompt_id] = prompt_data
        logger.info(f"Updated prompt in LangSmith Hub: {prompt_id}")
        return prompt_data

    def delete(self, prompt_id: str) -> bool:
        """Delete a prompt.

        Note: LangSmith Hub doesn't support deletion via API,
        so we just remove from cache.
        """
        if prompt_id in self._prompts_cache:
            del self._prompts_cache[prompt_id]
            logger.info(f"Removed prompt from cache: {prompt_id}")
            return True
        return False

    def exists(self, prompt_id: str) -> bool:
        """Check if a prompt exists."""
        if prompt_id in self._prompts_cache:
            return True

        # Try to fetch from hub
        return self.get(prompt_id) is not None
