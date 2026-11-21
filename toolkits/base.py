"""
Base Toolkit

This module provides the BaseToolkit abstract base class that all
toolkits inherit from. It provides common functionality including:
- Hash and equality comparison based on service_name, auth, and class name
- String representation for debugging
- Shared authentication and error handling methods
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

from agno.tools import Toolkit

logger = logging.getLogger(__name__)


class BaseToolkit(Toolkit, ABC):
    """
    Abstract base class for all toolkits.

    Provides common functionality for toolkit identification, comparison,
    authentication, and error handling.

    All toolkits (Calendar, Contacts, Drive, Email, Tasks) inherit from this class.
    """

    # Whether the direct /toolkit/run route may call this toolkit's methods. A toolkit whose
    # writes rely on the chat approval pause sets this to False.
    allow_direct_invocation: bool = True

    def __init__(
        self,
        user_id: str,
        service_name: str,
        auth: bool = True,
        fetch_token_func: Optional[Callable[[str, str], Optional[str]]] = None,
    ):
        """
        Initialize the base toolkit.

        Args:
            user_id: User identifier for token lookup
            service_name: Service identifier (e.g., "google_calendar", "microsoft_contacts")
            auth: Whether authentication is enabled
            fetch_token_func: Optional callback function to fetch OAuth access tokens
        """
        super().__init__()
        self.user_id = user_id
        self.service_name = service_name
        self.auth = auth
        self._fetch_token_func = fetch_token_func
        self.context: Dict[str, Any] = {"token_valid": False}

        # Call subclass-specific initialization
        self._initialize_service()

    @abstractmethod
    def _initialize_service(self) -> None:
        """
        Initialize service-specific configuration.

        This method must be implemented by subclasses to set up
        service-specific providers, configurations, etc.
        """
        pass

    def __hash__(self) -> int:
        """
        Compute hash based on service_name, auth, and class name.

        user_id is excluded to allow toolkit comparison across different users.
        """
        return hash((self.service_name, self.auth, type(self).__name__))

    def __eq__(self, other: object) -> bool:
        """
        Check equality based on service_name, auth, and class name.

        Two toolkits are considered equal if they have the same service provider,
        authentication status, and toolkit type.
        """
        if not isinstance(other, BaseToolkit):
            return False
        return self.service_name == other.service_name and self.auth == other.auth and type(self) is type(other)

    def __str__(self) -> str:
        """
        String representation for debugging and comparison.

        Format: ClassName(service_name='...', auth=True/False)
        """
        return f"{type(self).__name__}(service_name='{self.service_name}', auth={self.auth})"

    def __repr__(self) -> str:
        """
        Detailed string representation (same as __str__ for consistency).
        """
        return str(self)

    def _prepare_auth(self, *, hard: bool = False) -> Optional[str]:
        """
        Get a valid token using the shared token cache.

        Args:
            hard: If True, force fetch a fresh token

        Returns:
            Access token string, or None if unavailable
        """
        # Import token_cache from package level
        from . import token_cache as _token_cache

        if not self._fetch_token_func:
            self.context["token_valid"] = False
            return None

        token = _token_cache.get_token(
            service_name=self.service_name,
            user_id=self.user_id,
            fetcher=lambda: self._fetch_token_func(self.user_id, self.service_name),  # type: ignore[arg-type, return-value, misc]
            force=hard,
        )
        self.context["token_valid"] = bool(token)
        return token if token else None

    def error_card(self, message: str = "Unknown error") -> Dict[str, Any]:
        """
        Display an error message to the user.

        Args:
            message: Error message to display

        Returns:
            Error card with message and context
        """
        return {
            "card": "error",
            "message": message,
            "context": dict(self.context),
        }
