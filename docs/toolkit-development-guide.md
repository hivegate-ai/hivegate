# Toolkit Development Guide

**Complete guide for adding new toolkits to the Agent System**

---

## Table of Contents

1. [Overview & Architecture](#overview--architecture)
2. [Step 1: Define the Service (workspace_suite)](#step-1-define-the-service-workspace_suite)
3. [Step 2: Create the Toolkit](#step-2-create-the-toolkit-toolkits)
4. [Step 3: Integrate with Demo App](#step-3-integrate-with-demo-app)
5. [Step 4: Add CLI Formatting](#step-4-add-cli-formatting-optional)
6. [Step 5: Documentation](#step-5-documentation)
7. [Step 6: Testing Checklist](#step-6-testing-checklist)
8. [OAuth Setup Guide](#oauth-setup-guide)
9. [Troubleshooting](#troubleshooting)
10. [Deployment Checklist](#deployment-checklist)
11. [Common Patterns Reference](#common-patterns-reference)
12. [Example: Slack Toolkit](#example-slack-toolkit)

---

## Overview & Architecture

### System Layers

```
┌─────────────────────────────────────────────────┐
│  CLI Client (test_cards_client_tau2.py)        │  ← User interaction
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│  Demo App (demo_toolkits_app.py)                │  ← Agent orchestration
│  - Agent creation with toolkits                 │
│  - /chat and /chat/commit endpoints             │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│  Toolkit Layer (toolkits/)                      │  ← Your new toolkit here
│  - *Toolkit classes                     │
│  - Adapter over workspace_suite                 │
│  - Confirmation workflow                        │
│  - Token management via TokenCache              │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│  Workspace Suite (workspace_suite/)             │  ← Provider abstraction
│  - Protocol definitions (*_base.py)             │
│  - Google/Microsoft implementations             │
│  - Vendor-agnostic models                       │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│  External APIs (Google, Microsoft)              │
└─────────────────────────────────────────────────┘
```

### Key Principles

1. **Separation of Concerns**: workspace_suite handles API integration, toolkits handle agent-facing interface
2. **Protocol-Based Abstraction**: Use protocols for type-safe provider swapping
3. **Confirmation Workflow**: Write operations require user confirmation via `requires_confirmation` attribute
4. **Token Management**: OAuth tokens cached via `token_cache` with automatic expiration
5. **Multi-Provider Support**: Same toolkit supports both Google and Microsoft (or other providers)

---

## Step 1: Define the Service (workspace_suite)

### 1.1 Create Protocol Definition

**File**: `workspace_suite/providers/{service}_base.py`

```python
from typing import Protocol, Dict, Any
from workspace_suite.models import ServiceRequest, ServiceResponse

class ServiceProviderProtocol(Protocol):
    """Protocol for {service} provider implementations."""

    def operation_name(
        self,
        token: str,
        request: ServiceRequest
    ) -> Dict[str, Any]:
        """
        Perform operation.

        Args:
            token: OAuth2 access token
            request: Request model

        Returns:
            {"status": "success"|"error", ...}
        """
        ...
```

**Key Points**:
- Use `Protocol` from `typing` for duck-typing interface definition
- All operations must accept `token: str` parameter (OAuth2 access token)
- Return structured dicts with `status` field for consistent error handling
- Document all parameters and return values with docstrings

### 1.2 Create Data Models

**File**: `workspace_suite/models.py`

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

@dataclass(frozen=True)
class ServiceRequest:
    """Request model for service operation."""
    field1: str
    field2: Optional[str] = None
    field3: Optional[List[str]] = None

    def __post_init__(self):
        """Validate required fields."""
        if not self.field1:
            raise ValueError("field1 is required")

        # Additional validation
        if self.field3 is not None and len(self.field3) == 0:
            raise ValueError("field3 must not be empty if provided")

@dataclass(frozen=True)
class ServiceResponse:
    """Response model for service operation."""
    id: str
    status: str
    created_at: datetime
```

**Key Points**:
- Use `@dataclass(frozen=True)` for immutability (prevents accidental modifications)
- Vendor-agnostic field names (e.g., `summary` not `subject` or `title`)
- Add `__post_init__` validation for required fields and business rules
- Keep models simple and focused (single responsibility)
- Use `Optional[...]` for optional fields with `None` defaults

### 1.3 Implement Providers

#### Google Provider

**File**: `workspace_suite/providers/google_{service}.py`

```python
import httpx
from typing import Dict, Any, Optional
from workspace_suite.providers.{service}_base import ServiceProviderProtocol
from workspace_suite.config import ProviderConfig
from workspace_suite.models import ServiceRequest

class GoogleServiceProvider:
    """Google {Service} API implementation."""

    def __init__(
        self,
        config: ProviderConfig,
        http_client: Optional[httpx.Client] = None
    ):
        """
        Initialize Google provider.

        Args:
            config: Provider configuration (timezone, defaults, etc.)
            http_client: Optional HTTP client for testing (MockTransport)
        """
        self.config = config
        self.client = http_client or httpx.Client(timeout=30.0)
        self.base_url = "https://www.googleapis.com/{service}/v1"

    def operation_name(
        self,
        token: str,
        request: ServiceRequest
    ) -> Dict[str, Any]:
        """
        Implement operation for Google API.

        Args:
            token: OAuth2 access token
            request: Operation request

        Returns:
            {"status": "success", ...} or {"status": "error", "error": {...}}
        """
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        # Build request payload (map workspace_suite models to Google API format)
        payload = {
            "field1": request.field1,
            "field2": request.field2,
        }

        try:
            response = self.client.post(
                f"{self.base_url}/endpoint",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()

            # Map Google response to workspace_suite format
            return {
                "status": "success",
                "id": data.get("id"),
                "created_at": data.get("created"),
                # ... additional fields
            }

        except httpx.HTTPStatusError as e:
            # HTTP error (4xx, 5xx)
            error_data = {}
            try:
                error_data = e.response.json()
            except Exception:
                error_data = {"message": str(e)}

            return {
                "status": "error",
                "error": {
                    "message": error_data.get("error", {}).get("message", str(e)),
                    "code": e.response.status_code,
                    "details": error_data
                }
            }

        except httpx.RequestError as e:
            # Network error (timeout, connection refused, etc.)
            return {
                "status": "error",
                "error": {
                    "message": f"Network error: {str(e)}",
                    "code": 0
                }
            }
```

#### Microsoft Provider

**File**: `workspace_suite/providers/microsoft_{service}.py`

```python
import httpx
from typing import Dict, Any, Optional
from workspace_suite.providers.{service}_base import ServiceProviderProtocol
from workspace_suite.config import ProviderConfig
from workspace_suite.models import ServiceRequest

class MicrosoftServiceProvider:
    """Microsoft {Service} API implementation (Graph API)."""

    def __init__(
        self,
        config: ProviderConfig,
        http_client: Optional[httpx.Client] = None
    ):
        """
        Initialize Microsoft provider.

        Args:
            config: Provider configuration
            http_client: Optional HTTP client for testing
        """
        self.config = config
        self.client = http_client or httpx.Client(timeout=30.0)
        self.base_url = "https://graph.microsoft.com/v1.0"

    def operation_name(
        self,
        token: str,
        request: ServiceRequest
    ) -> Dict[str, Any]:
        """
        Implement operation for Microsoft Graph API.

        Args:
            token: OAuth2 access token
            request: Operation request

        Returns:
            {"status": "success", ...} or {"status": "error", "error": {...}}
        """
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        # Build request payload (map workspace_suite models to Graph API format)
        payload = {
            "field1": request.field1,  # Might be different field name in Graph API
            "field2": request.field2,
        }

        try:
            response = self.client.post(
                f"{self.base_url}/me/endpoint",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()

            # Map Graph API response to workspace_suite format
            return {
                "status": "success",
                "id": data.get("id"),
                "created_at": data.get("createdDateTime"),
                # ... additional fields
            }

        except httpx.HTTPStatusError as e:
            error_data = {}
            try:
                error_data = e.response.json()
            except Exception:
                error_data = {"message": str(e)}

            return {
                "status": "error",
                "error": {
                    "message": error_data.get("error", {}).get("message", str(e)),
                    "code": e.response.status_code,
                    "details": error_data
                }
            }

        except httpx.RequestError as e:
            return {
                "status": "error",
                "error": {
                    "message": f"Network error: {str(e)}",
                    "code": 0
                }
            }
```

**Key Points**:
- Accept `httpx.Client` for testing with `MockTransport` (enables network-free tests)
- Use `httpx` (not `requests`) for async compatibility
- Map vendor-specific field names to workspace_suite models (e.g., Google's `summary` → Microsoft's `subject`)
- Handle errors gracefully with structured error responses
- Return consistent format: `{"status": "success"|"error", ...}`
- Use `response.raise_for_status()` to convert HTTP errors to exceptions

### 1.4 Create Service Layer

**File**: `workspace_suite/services/{service}_service.py`

```python
from typing import Dict, Any
from workspace_suite.providers.{service}_base import ServiceProviderProtocol
from workspace_suite.models import ServiceRequest

class ServiceService:
    """
    High-level service for {service} operations.

    Provides business logic layer over provider implementations.
    """

    def __init__(self, provider: ServiceProviderProtocol):
        """
        Initialize service.

        Args:
            provider: Provider implementation (Google or Microsoft)
        """
        self.provider = provider

    def perform_operation(
        self,
        token: str,
        req: ServiceRequest
    ) -> Dict[str, Any]:
        """
        Perform operation with business logic.

        Args:
            token: OAuth2 access token
            req: Operation request

        Returns:
            Operation result
        """
        # Add business logic layer here:
        # - Input validation
        # - Default handling
        # - Error enrichment
        # - Logging

        # Validate inputs (beyond model validation)
        if not token:
            return {
                "status": "error",
                "error": {
                    "message": "Authentication token required",
                    "code": 401
                }
            }

        # Call provider
        result = self.provider.operation_name(token, req)

        # Post-process result
        if result.get("status") == "success":
            # Enrich success response (e.g., add computed fields)
            result["service_type"] = type(self.provider).__name__

        return result
```

**Key Points**:
- Thin wrapper over provider for business logic and orchestration
- Protocol-based provider injection (works with any provider implementation)
- Keep service layer focused on orchestration, not implementation details
- Add logging, validation, and enrichment here
- Don't duplicate provider logic

### 1.5 Test Workspace Suite Components

**File**: `tests/workspace_suite/test_{service}_google.py`

```python
import unittest
from unittest.mock import Mock
import httpx
from workspace_suite.providers.google_{service} import GoogleServiceProvider
from workspace_suite.config import ProviderConfig
from workspace_suite.models import ServiceRequest

class TestGoogleServiceProvider(unittest.TestCase):
    """Test Google {Service} provider implementation."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = ProviderConfig(default_timezone="UTC")

        # Mock transport for network-free testing
        self.mock_transport = httpx.MockTransport(self._mock_handler)
        self.client = httpx.Client(transport=self.mock_transport)
        self.provider = GoogleServiceProvider(self.config, self.client)

    def _mock_handler(self, request: httpx.Request) -> httpx.Response:
        """
        Mock HTTP responses.

        Args:
            request: HTTP request

        Returns:
            Mock HTTP response
        """
        # Check request headers
        if "Authorization" not in request.headers:
            return httpx.Response(401, json={"error": {"message": "Unauthorized"}})

        # Mock successful operation
        if "endpoint" in str(request.url):
            return httpx.Response(200, json={
                "id": "123",
                "field1": "value",
                "created": "2025-01-01T00:00:00Z"
            })

        # Default 404
        return httpx.Response(404, json={"error": {"message": "Not found"}})

    def test_operation_success(self):
        """Test successful operation."""
        req = ServiceRequest(field1="value")
        result = self.provider.operation_name("fake-token", req)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["id"], "123")

    def test_operation_unauthorized(self):
        """Test unauthorized error."""
        req = ServiceRequest(field1="value")
        result = self.provider.operation_name("", req)  # Empty token

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], 401)

    def test_operation_network_error(self):
        """Test network error handling."""
        # Use transport that raises network error
        def error_handler(request):
            raise httpx.ConnectError("Connection refused")

        error_transport = httpx.MockTransport(error_handler)
        error_client = httpx.Client(transport=error_transport)
        provider = GoogleServiceProvider(self.config, error_client)

        req = ServiceRequest(field1="value")
        result = provider.operation_name("fake-token", req)

        self.assertEqual(result["status"], "error")
        self.assertIn("Network error", result["error"]["message"])
```

**File**: `tests/workspace_suite/test_{service}_microsoft.py`

```python
# Similar structure to Google provider tests
# Test Microsoft Graph API specific behavior
```

**Key Points**:
- Use `httpx.MockTransport` for network-free tests (fast, reliable, no API quotas)
- Test both success and error paths (HTTP errors, network errors, auth errors)
- Verify request headers and payloads in mock handler
- Test for both Google and Microsoft providers
- Cover edge cases (empty responses, malformed JSON, rate limits)

---

## Step 2: Create the Toolkit (toolkits/)

### 2.0 Understanding BaseToolkit

**IMPORTANT**: All Toolkits inherit from `BaseToolkit`, not directly from `agno.tools.Toolkit`.

**File**: `toolkits/base.py` (already exists)

The `BaseToolkit` abstract base class provides:

1. **Common initialization**: Standardized `__init__` with `user_id`, `service_name`, `auth`, and `fetch_token_func`
2. **Token management**: `_prepare_auth()` method that handles token caching automatically
3. **Error handling**: `error_card()` method for consistent error responses
4. **Toolkit comparison**: `__hash__`, `__eq__`, `__str__`, `__repr__` for toolkit identification
5. **Abstract method**: `_initialize_service()` that subclasses must implement

**Key Methods**:

```python
class BaseToolkit(Toolkit, ABC):
    """Abstract base class for all Toolkits."""

    def __init__(
        self,
        user_id: str,
        service_name: str,
        auth: bool = True,
        fetch_token_func: Optional[Callable[[str, str], Optional[str]]] = None,
    ):
        """Initialize base toolkit."""
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
        """Initialize service-specific configuration (must be implemented by subclass)."""
        pass

    def _prepare_auth(self, *, hard: bool = False) -> Optional[str]:
        """
        Get a valid token using the shared token cache.

        Args:
            hard: If True, force fetch a fresh token

        Returns:
            Access token string, or None if unavailable
        """
        # Automatically handles token caching with get_token()
        # Updates self.context["token_valid"]
        ...

    def error_card(self, message: str = "Unknown error") -> Dict[str, Any]:
        """Display an error message to the user."""
        return {
            "card": "error",
            "message": message,
            "context": dict(self.context),
        }
```

**Benefits of Using BaseToolkit**:
- ✅ Automatic token caching (no need to manually call `token_cache.get_token()`)
- ✅ Consistent error handling across all toolkits
- ✅ Toolkit comparison and deduplication support
- ✅ Shared `context` dict for tracking state
- ✅ Less boilerplate code in your toolkit

### 2.1 Toolkit Structure

**File**: `toolkits/{service}.py` (not `{service}.py`)

```python
from typing import Optional, Callable, Dict, Any, List
from toolkits.base import BaseToolkit  # Inherit from base
from workspace_suite import ServiceService
from workspace_suite.providers.google_{service} import GoogleServiceProvider
from workspace_suite.providers.microsoft_{service} import MicrosoftServiceProvider
from workspace_suite.config import ProviderConfig
from workspace_suite.models import ServiceRequest

class ServiceToolkit(BaseToolkit):  # Inherit from BaseToolkit
    """
    Multi-provider {service} toolkit for Agno agents.

    Supports:
    - Google {Service}
    - Microsoft {Service}

    Features:
    - Confirmation-based workflow for write operations
    - OAuth token management with caching
    - Provider auto-selection based on token availability

    Example:
        ```python
        toolkit = ServiceToolkit(
            user_id="user123",
            service_name="google_{service}",
            auth=True,
            fetch_token_func=fetch_access_token
        )

        agent = Agent(
            name="assistant",
            tools=[toolkit],
            ...
        )
        ```
    """

    def __init__(
        self,
        user_id: str,
        service_name: str = "{service}",  # "google_{service}" | "microsoft_{service}"
        auth: bool = False,
        fetch_token_func: Optional[Callable[[str, str], Optional[str]]] = None,
        default_timezone: str = "UTC",
    ):
        """
        Initialize toolkit.

        Args:
            user_id: User identifier for token lookup
            service_name: Provider identifier (e.g., "google_{service}", "microsoft_{service}")
            auth: Whether authentication is available
            fetch_token_func: Function to fetch OAuth tokens (user_id, integration_key) -> token
            default_timezone: Default timezone for operations
        """
        # Set toolkit-specific attributes BEFORE calling parent
        self.default_timezone = default_timezone

        # Call parent __init__ which triggers _initialize_service()
        # BaseToolkit handles: user_id, service_name, auth, fetch_token_func
        super().__init__(
            user_id=user_id,
            service_name=service_name,
            auth=auth,
            fetch_token_func=fetch_token_func,
        )

        # Register tools based on auth status AFTER service initialization
        if self.auth:
            # Authenticated: register all tools
            self.register(self.perform_operation)
            self.register(self.read_only_operation)
        else:
            # Not authenticated: only show auth required
            self.register(self.auth_required)

    def _initialize_service(self) -> None:
        """
        Initialize service with appropriate provider.

        This method is called by BaseToolkit.__init__() after setting base attributes.
        Use it to configure providers and service instances.
        """
        config = ProviderConfig(default_timezone=self.default_timezone)

        if "google" in self.service_name.lower():
            provider = GoogleServiceProvider(config)
        elif "microsoft" in self.service_name.lower():
            provider = MicrosoftServiceProvider(config)
        else:
            raise ValueError(f"Unknown service: {self.service_name}")

        self.service = ServiceService(provider)

    def perform_operation(
        self,
        field1: str,
        field2: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Perform operation (requires confirmation).

        This is a write operation that modifies data, so it requires
        user confirmation via the /chat/commit workflow.

        Args:
            field1: Required field
            field2: Optional field

        Returns:
            Operation result with status

        Example:
            ```json
            {
              "status": "success",
              "id": "123",
              "message": "Operation completed successfully"
            }
            ```
        """
        # Use base class method for token management
        token = self._prepare_auth()
        if not token:
            # Use base class error_card method
            return self.error_card("Authentication failed. Please reconnect.")

        try:
            # Build request
            req = ServiceRequest(
                field1=field1,
                field2=field2,
            )

            # Call service
            result = self.service.perform_operation(token, req)

            # Handle errors
            if result.get("status") == "error":
                error_msg = result.get("error", {}).get("message", "Unknown error")
                return self.error_card(error_msg)

            return result

        except ValueError as e:
            # Validation error from model
            return self.error_card(f"Invalid input: {str(e)}")

        except Exception as e:
            # Unexpected error
            return self.error_card(f"Failed to perform operation: {str(e)}")

    # Mark as requiring confirmation (Agno framework checks this attribute)
    perform_operation.requires_confirmation = True  # type: ignore[attr-defined]

    def read_only_operation(
        self,
        query: Optional[str] = None,
        max_results: int = 50,
    ) -> Dict[str, Any]:
        """
        Read-only operation (no confirmation required).

        This operation only reads data and doesn't modify anything,
        so it executes immediately without confirmation.

        Args:
            query: Optional search query
            max_results: Maximum results to return

        Returns:
            Query results

        Example:
            ```json
            {
              "status": "success",
              "results": [...],
              "total_count": 42
            }
            ```
        """
        # Use base class method for token management
        token = self._prepare_auth()
        if not token:
            return self.error_card("Authentication failed. Please reconnect.")

        try:
            # Implement read-only logic
            # This would call a list/search method on the service
            result = self.service.list_items(token, query, max_results)

            if result.get("status") == "error":
                error_msg = result.get("error", {}).get("message", "Unknown error")
                return self.error_card(error_msg)

            return result

        except Exception as e:
            return self.error_card(f"Failed to fetch data: {str(e)}")

    def auth_required(self) -> Dict[str, Any]:
        """
        Prompt for authentication when not authenticated.

        Returns special card that triggers OAuth flow in client.

        Returns:
            Auth required card
        """
        return {
            "card": f"{self.service_name}-auth-required",
            "service": self.service_name,
            "message": f"Please connect your {self.service_name} account to use this feature.",
            "context": {"token_valid": False}
        }

    # Note: error_card() is inherited from BaseToolkit
    # No need to override it - the base class version uses self.context
    # which is automatically updated by _prepare_auth()
```

**Key Points**:
- **Inherit from `BaseToolkit`**: Provides token caching, error handling, and toolkit comparison
- **Implement `_initialize_service()`**: Abstract method for setting up providers and services
- **Use `_prepare_auth()`**: Base class method for token management (auto-caching)
- **Use inherited `error_card()`**: Base class provides this method with automatic context tracking
- Mark write operations with `method.requires_confirmation = True`
- Provide `auth_required()` card for unauthenticated state
- Return structured dicts (not strings) from all methods
- Use docstrings with examples for agent LLM context
- Register tools conditionally based on auth status

### 2.2 Register Toolkit

**File**: `toolkits/__init__.py`

```python
from toolkits.calendar import CalendarToolkit
from toolkits.email import EmailToolkit
from toolkits.contacts import ContactsToolkit
from toolkits.drive import DriveToolkit
from toolkits.{service} import ServiceToolkit  # Add your toolkit
from toolkits.token_cache import token_cache

__all__ = [
    "CalendarToolkit",
    "EmailToolkit",
    "ContactsToolkit",
    "DriveToolkit",
    "ServiceToolkit",  # Export your toolkit
    "token_cache",
]
```

### 2.3 Test Toolkit

**File**: `tests/toolkits/test_{service}.py`

```python
import unittest
from unittest.mock import Mock, patch, MagicMock
from toolkits import ServiceToolkit
from toolkits.token_cache import token_cache

class TestServiceToolkit(unittest.TestCase):
    """Test {Service} Toolkit."""

    def setUp(self):
        """Set up test fixtures."""
        # Clear token cache before each test
        token_cache._cache.clear()

        self.user_id = "test-user"
        self.mock_fetch_token = Mock(return_value="fake-access-token")

        self.toolkit = ServiceToolkit(
            user_id=self.user_id,
            service_name="google_{service}",
            auth=True,
            fetch_token_func=self.mock_fetch_token
        )

    @patch('workspace_suite.providers.google_{service}.GoogleServiceProvider.operation_name')
    def test_perform_operation_success(self, mock_operation):
        """Test successful operation."""
        mock_operation.return_value = {
            "status": "success",
            "id": "123",
            "message": "Operation completed"
        }

        result = self.toolkit.perform_operation(field1="value")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["id"], "123")
        mock_operation.assert_called_once()

    @patch('workspace_suite.providers.google_{service}.GoogleServiceProvider.operation_name')
    def test_perform_operation_error(self, mock_operation):
        """Test operation error handling."""
        mock_operation.return_value = {
            "status": "error",
            "error": {
                "message": "API error",
                "code": 400
            }
        }

        result = self.toolkit.perform_operation(field1="value")

        self.assertEqual(result["card"], "error")
        self.assertIn("API error", result["message"])

    def test_perform_operation_no_token(self):
        """Test operation without authentication."""
        self.toolkit.fetch_token_func = Mock(return_value=None)

        result = self.toolkit.perform_operation(field1="value")

        self.assertEqual(result["card"], "error")
        self.assertIn("Authentication failed", result["message"])

    def test_token_caching(self):
        """Test token cache usage."""
        # First call fetches token
        self.toolkit._get_token()
        self.mock_fetch_token.assert_called_once_with(self.user_id, "google_{service}")

        # Second call uses cached token
        self.toolkit._get_token()
        self.mock_fetch_token.assert_called_once()  # Still only called once

    def test_auth_required_when_no_auth(self):
        """Test auth required card."""
        toolkit = ServiceToolkit(
            user_id=self.user_id,
            service_name="google_{service}",
            auth=False
        )

        result = toolkit.auth_required()
        self.assertEqual(result["card"], "google_{service}-auth-required")
        self.assertEqual(result["service"], "google_{service}")
        self.assertFalse(result["context"]["token_valid"])

    def test_requires_confirmation_attribute(self):
        """Test that write operations have requires_confirmation attribute."""
        self.assertTrue(
            hasattr(self.toolkit.perform_operation, "requires_confirmation")
        )
        self.assertTrue(self.toolkit.perform_operation.requires_confirmation)

    def test_microsoft_provider_initialization(self):
        """Test Microsoft provider initialization."""
        toolkit = ServiceToolkit(
            user_id=self.user_id,
            service_name="microsoft_{service}",
            auth=True,
            fetch_token_func=self.mock_fetch_token
        )

        # Should initialize without error
        self.assertIsNotNone(toolkit.service)

    def test_unknown_provider_raises_error(self):
        """Test unknown provider raises ValueError."""
        with self.assertRaises(ValueError):
            ServiceToolkit(
                user_id=self.user_id,
                service_name="unknown_service",
                auth=True,
                fetch_token_func=self.mock_fetch_token
            )
```

**Key Points**:
- Clear token cache before each test (`setUp()`)
- Mock provider methods to avoid real API calls
- Test both success and error paths
- Verify token caching behavior
- Test `requires_confirmation` attribute
- Test both Google and Microsoft provider initialization
- Test unauthenticated state

---

## Step 3: Integrate with Demo App

### 3.1 Add Toolkit to Dynamic Selection

**File**: `demo_toolkits_app.py`

```python
from toolkits import (
    CalendarToolkit,
    EmailToolkit,
    ServiceToolkit,  # Import your toolkit
)

def select_toolkits_by_token_availability(
    user_id: str,
    organizer_email: str
) -> List[Any]:
    """
    Select toolkits based on available OAuth tokens.

    Tries to load tokens for each service and initializes
    the appropriate toolkit (authenticated or not).

    Args:
        user_id: User identifier
        organizer_email: User's email address

    Returns:
        List of initialized toolkits
    """
    toolkits = []

    # Calendar toolkit
    toolkits.append(select_calendar_toolkit(user_id, organizer_email))

    # Email toolkit
    toolkits.append(select_email_toolkit(user_id))

    # Your new service toolkit
    # Try Google first
    google_token = fetch_access_token(user_id, "google_{service}")
    if google_token:
        toolkits.append(ServiceToolkit(
            user_id=user_id,
            service_name="google_{service}",
            auth=True,
            fetch_token_func=fetch_access_token
        ))
    else:
        # Try Microsoft fallback
        ms_token = fetch_access_token(user_id, "microsoft_{service}")
        if ms_token:
            toolkits.append(ServiceToolkit(
                user_id=user_id,
                service_name="microsoft_{service}",
                auth=True,
                fetch_token_func=fetch_access_token
            ))
        else:
            # No auth available - show auth required
            toolkits.append(ServiceToolkit(
                user_id=user_id,
                service_name="{service}",
                auth=False
            ))

    return toolkits
```

**Alternative**: Create dedicated selection function for cleaner code:

```python
def select_service_toolkit(user_id: str) -> ServiceToolkit:
    """
    Select {service} toolkit based on token availability.

    Priority:
    1. Google {Service} (if token available)
    2. Microsoft {Service} (if token available)
    3. No-auth mode (show auth required)

    Args:
        user_id: User identifier

    Returns:
        Initialized toolkit
    """
    # Try Google
    google_token = fetch_access_token(user_id, "google_{service}")
    if google_token:
        return ServiceToolkit(
            user_id=user_id,
            service_name="google_{service}",
            auth=True,
            fetch_token_func=fetch_access_token
        )

    # Try Microsoft
    ms_token = fetch_access_token(user_id, "microsoft_{service}")
    if ms_token:
        return ServiceToolkit(
            user_id=user_id,
            service_name="microsoft_{service}",
            auth=True,
            fetch_token_func=fetch_access_token
        )

    # No auth available
    return ServiceToolkit(
        user_id=user_id,
        service_name="{service}",
        auth=False
    )

# Then use in select_toolkits_by_token_availability:
def select_toolkits_by_token_availability(user_id: str, organizer_email: str):
    return [
        select_calendar_toolkit(user_id, organizer_email),
        select_email_toolkit(user_id),
        select_service_toolkit(user_id),  # Add here
    ]
```

### 3.2 Test Integration

```bash
# Start demo app
python demo_toolkits_app.py

# In another terminal, test with CLI
cd cli
python test_cards_client_tau2.py

# Send test message
You> Perform {service} operation with value "test"

# Verify:
# 1. Agent recognizes the tool
# 2. Confirmation workflow triggers
# 3. Tool executes after confirmation
# 4. Results are displayed
```

---

## Step 4: Add CLI Formatting (Optional)

### 4.1 Create Formatter

**File**: `cli/client_ui/formatters.py`

```python
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from typing import Dict, Any

def format_service_operation_result(result: Dict[str, Any]) -> None:
    """
    Format {service} operation result for console display.

    Args:
        result: Operation result dict
    """
    console = Console()

    if result.get("status") == "success":
        # Success case - show table with details
        table = Table(title="Operation Result")
        table.add_column("Field", style="cyan", no_wrap=True)
        table.add_column("Value", style="green")

        table.add_row("ID", result.get("id", "N/A"))
        table.add_row("Status", result.get("status", "N/A"))

        # Add custom fields
        if "field1" in result:
            table.add_row("Field 1", result["field1"])
        if "created_at" in result:
            table.add_row("Created", result["created_at"])

        console.print(Panel(
            table,
            title="✓ Operation Completed",
            border_style="green"
        ))

    else:
        # Error case - show error panel
        error_msg = result.get("error", {}).get("message", "Unknown error")
        error_code = result.get("error", {}).get("code", "N/A")

        error_text = Text()
        error_text.append(f"Error {error_code}: ", style="bold red")
        error_text.append(error_msg, style="red")

        console.print(Panel(
            error_text,
            title="✗ Operation Failed",
            border_style="red"
        ))

def format_service_list_result(result: Dict[str, Any]) -> None:
    """
    Format {service} list result for console display.

    Args:
        result: List result dict
    """
    console = Console()

    if result.get("status") == "success":
        items = result.get("results", [])
        total = result.get("total_count", len(items))

        if not items:
            console.print(Panel(
                "[yellow]No results found[/yellow]",
                title="Search Results",
                border_style="yellow"
            ))
            return

        # Create table
        table = Table(title=f"Results ({total} total)")
        table.add_column("ID", style="cyan")
        table.add_column("Field 1", style="white")
        table.add_column("Field 2", style="white")

        for item in items:
            table.add_row(
                item.get("id", "N/A"),
                item.get("field1", "N/A"),
                item.get("field2", "N/A"),
            )

        console.print(Panel(table, border_style="green"))

    else:
        error_msg = result.get("error", {}).get("message", "Unknown error")
        console.print(Panel(
            f"[red]{error_msg}[/red]",
            title="✗ Error",
            border_style="red"
        ))
```

### 4.2 Register Formatter

**File**: `cli/client_ui/formatters.py`

```python
# Tool name → formatter function mapping
TOOL_FORMATTERS = {
    "schedule_meeting": format_schedule_meeting_result,
    "list_events": format_list_events_result,
    "send_email": format_send_email_result,
    "perform_operation": format_service_operation_result,  # Add your formatter
    "read_only_operation": format_service_list_result,
}
```

### 4.3 Create Dialog (for confirmation editing)

**File**: `cli/client_ui/dialogs.py`

```python
from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from typing import Dict, Any, Optional

def confirm_service_operation(tool: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Show confirmation dialog for {service} operation.

    Allows user to:
    - Confirm execution
    - Edit parameters
    - Skip/cancel

    Args:
        tool: Tool call dict with tool_args

    Returns:
        Updated tool dict or None if skipped
    """
    args = tool.get("tool_args", {})

    # Show current parameters
    print("\n[bold]Operation Parameters:[/bold]")
    print(f"  Field 1: {args.get('field1', 'N/A')}")
    print(f"  Field 2: {args.get('field2', 'N/A')}")

    # Ask for confirmation
    action = inquirer.select(
        message="Confirm operation?",
        choices=[
            Choice(value="confirm", name="✓ Confirm"),
            Choice(value="edit", name="✎ Edit parameters"),
            Choice(value="skip", name="✗ Skip"),
        ],
        default="confirm"
    ).execute()

    if action == "skip":
        return None

    if action == "edit":
        # Edit parameters
        field1 = inquirer.text(
            message="Field 1:",
            default=args.get("field1", "")
        ).execute()

        field2 = inquirer.text(
            message="Field 2 (optional):",
            default=args.get("field2", "")
        ).execute()

        # Update args
        args["field1"] = field1
        if field2:
            args["field2"] = field2

    # Return updated tool
    tool["confirmed"] = True
    tool["confirmation_note"] = f"Confirmed via CLI ({action})"
    return tool
```

### 4.4 Register Dialog

**File**: `cli/client_ui/dialogs.py`

```python
# Tool name → dialog function mapping
TOOL_DIALOGS = {
    "schedule_meeting": confirm_schedule_meeting,
    "cancel_meeting": confirm_cancel_meeting,
    "send_email": confirm_send_email,
    "perform_operation": confirm_service_operation,  # Add your dialog
}
```

---

## Step 5: Documentation

### 5.1 Update CLAUDE.md

Add toolkit documentation to `CLAUDE.md` under the "Toolkits Package" section:

```markdown
- **ServiceToolkit**: Multi-provider {service} management (Google + Microsoft)
  - **Architecture**: Adapter pattern over `workspace_suite.ServiceService`
  - Confirmation-based workflow for write operations
  - OAuth token management with caching
  - **Tools** (when authenticated):
    - `perform_operation`: Description of operation - requires confirmation
    - `read_only_operation`: Description of read operation - no confirmation (read-only)
    - `auth_required`: Prompt for {service} authentication (when not authenticated)
    - `error_card`: Display error messages with retry options
  - **Provider Selection**: Dynamic initialization based on `service_name` parameter
    - `"google_{service}"` → `GoogleServiceProvider`
    - `"microsoft_{service}"` → `MicrosoftServiceProvider`
```

### 5.2 Add Method Specifications

Add detailed method specs to the "Toolkit Method Specifications" section in `CLAUDE.md`:

```markdown
#### ServiceToolkit Methods

**1. perform_operation** (requires confirmation)
```python
def perform_operation(
    field1: str,                                   # Required field
    field2: Optional[str] = None,                  # Optional field
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "id": "123",
  "field1": "value",
  "created_at": "2025-01-01T00:00:00Z",
  "message": "Operation completed successfully"
}
```
**Output (error)**:
```json
{
  "card": "error",
  "message": "Authentication failed. Please reconnect.",
  "actions": ["retry", "cancel"],
  "context": {"token_valid": false}
}
```

**2. read_only_operation** (no confirmation)
```python
def read_only_operation(
    query: Optional[str] = None,                   # Search query
    max_results: int = 50,                         # Max results
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "results": [
    {"id": "1", "field1": "value1", "field2": "value2"},
    {"id": "2", "field1": "value3", "field2": "value4"}
  ],
  "total_count": 42
}
```
```

### 5.3 Update Toolkits README

**File**: `toolkits/README.md`

Add section for your new toolkit:

```markdown
## ServiceToolkit

Multi-provider {service} management toolkit for Agno agents.

### Features

- **Multi-Provider Support**: Google {Service} and Microsoft {Service}
- **Confirmation Workflow**: Write operations require user confirmation
- **Token Caching**: Automatic OAuth token caching (5-minute TTL)
- **Error Handling**: Graceful error handling with retry options

### Supported Operations

- `perform_operation`: Write operation (requires confirmation)
- `read_only_operation`: Read operation (no confirmation)

### Usage Example

```python
from toolkits import ServiceToolkit

# Initialize with Google provider
toolkit = ServiceToolkit(
    user_id="user123",
    service_name="google_{service}",
    auth=True,
    fetch_token_func=fetch_access_token
)

# Use with Agno agent
from agno.agent import Agent
agent = Agent(
    name="assistant",
    tools=[toolkit],
    ...
)

# Agent can now use {service} operations
agent.print_response("Perform operation with value 'test'")
```

### OAuth Scopes Required

**Google {Service}**:
- `https://www.googleapis.com/auth/{service}` - Full access
- `https://www.googleapis.com/auth/{service}.readonly` - Read-only

**Microsoft {Service}**:
- `{Service}.ReadWrite` - Full access
- `{Service}.Read` - Read-only

### Provider Support

| Provider | Status | Notes |
|----------|--------|-------|
| Google {Service} | ✅ Supported | Full API coverage |
| Microsoft {Service} | ✅ Supported | Via Graph API |
```

---

## Step 6: Testing Checklist

Complete this checklist before considering the toolkit done:

### Unit Tests

- [ ] **workspace_suite Google provider**: Success cases, error cases, network errors
- [ ] **workspace_suite Microsoft provider**: Success cases, error cases, network errors
- [ ] **workspace_suite service layer**: Business logic, error enrichment
- [ ] **Toolkit methods**: All tool methods with mock providers
- [ ] **Toolkit auth states**: Authenticated, unauthenticated, token expiry
- [ ] **Token caching**: Cache hit, cache miss, cache expiration

### Integration Tests

- [ ] **End-to-end with demo app**: Agent can use toolkit
- [ ] **Multi-provider switching**: Google → Microsoft fallback works
- [ ] **Confirmation workflow**: /chat → /chat/commit flow works
- [ ] **Error handling**: Auth failures, API errors handled gracefully

### Manual Testing

- [ ] **CLI client**: Test with `test_cards_client_tau2.py`
- [ ] **Token refresh**: Test expired token auto-refresh
- [ ] **Error scenarios**: Test network errors, API rate limits
- [ ] **Provider comparison**: Test same operation with Google and Microsoft

### Code Quality

- [ ] **Linting**: `./scripts/validate.sh` passes (ruff check + mypy)
- [ ] **Formatting**: `./scripts/format.sh` applied
- [ ] **Type safety**: No mypy errors
- [ ] **Documentation**: Docstrings for all public methods
- [ ] **Tests pass**: All unit and integration tests pass

### Run Validation

```bash
# Format code
./scripts/format.sh

# Run validation (lint + type check)
./scripts/validate.sh

# Run workspace_suite tests
python -m pytest tests/workspace_suite/test_{service}_*.py -v

# Run toolkit tests
python -m pytest tests/toolkits/test_{service}.py -v

# Run all tests
python -m pytest tests/ -v

# Check coverage
python -m pytest tests/ --cov=workspace_suite --cov=toolkits --cov-report=term-missing
```

---

## OAuth Setup Guide

### Google Cloud Console Setup

#### 1. Create OAuth Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (or create new one)
3. Navigate to **APIs & Services** → **Credentials**
4. Click **+ CREATE CREDENTIALS** → **OAuth client ID**
5. Choose **Application type**: Web application
6. Set **Authorized redirect URIs**:
   - Development: `http://localhost:3000/auth/callback`
   - Production: `https://yourdomain.com/auth/callback`
7. Save **Client ID** and **Client Secret**

#### 2. Enable Required APIs

1. Navigate to **APIs & Services** → **Library**
2. Search for your service API (e.g., "Google Calendar API")
3. Click **ENABLE**
4. Repeat for all required APIs

#### 3. Configure OAuth Consent Screen

1. Navigate to **APIs & Services** → **OAuth consent screen**
2. Choose **User Type**: External (for public) or Internal (for Google Workspace)
3. Fill in:
   - **App name**: Your application name
   - **User support email**: Your email
   - **Developer contact**: Your email
4. Click **SAVE AND CONTINUE**

#### 4. Add Scopes

1. Click **ADD OR REMOVE SCOPES**
2. Search and add required scopes:
   - For Calendar: `https://www.googleapis.com/auth/calendar`
   - For Gmail: `https://www.googleapis.com/auth/gmail.modify`
   - For Contacts: `https://www.googleapis.com/auth/contacts`
   - For Drive: `https://www.googleapis.com/auth/drive`
3. Click **UPDATE** and **SAVE AND CONTINUE**

#### 5. Add Test Users (for development)

1. Click **ADD USERS**
2. Enter email addresses of test users
3. Click **SAVE AND CONTINUE**

#### 6. Service Account Setup (for hivegate authentication)

1. Navigate to **IAM & Admin** → **Service Accounts**
2. Click **+ CREATE SERVICE ACCOUNT**
3. Set **Service account name**: `hivegate`
4. Click **CREATE AND CONTINUE**
5. Grant role: **Service Account Token Creator** (optional, for impersonation)
6. Click **DONE**
7. Click on the service account → **KEYS** tab
8. Click **ADD KEY** → **Create new key** → **JSON**
9. Download and save as `agents/service-account.json`

### Microsoft Azure AD Setup

#### 1. Register Application

1. Go to [Azure Portal](https://portal.azure.com/)
2. Navigate to **Azure Active Directory** → **App registrations**
3. Click **+ New registration**
4. Set:
   - **Name**: Your application name
   - **Supported account types**: Multitenant (for public) or Single tenant
   - **Redirect URI**: Web → `http://localhost:3000/auth/callback` (dev) or `https://yourdomain.com/auth/callback` (prod)
5. Click **Register**

#### 2. Create Client Secret

1. In your app, navigate to **Certificates & secrets**
2. Click **+ New client secret**
3. Set **Description**: e.g., "Production"
4. Set **Expires**: 24 months (or custom)
5. Click **Add**
6. **Copy the Value immediately** (won't be shown again)

#### 3. Configure API Permissions

1. Navigate to **API permissions**
2. Click **+ Add a permission**
3. Choose **Microsoft Graph**
4. Choose **Delegated permissions**
5. Add required permissions:
   - For Calendar: `Calendars.ReadWrite`
   - For Mail: `Mail.ReadWrite`
   - For Contacts: `Contacts.ReadWrite`
   - For OneDrive: `Files.ReadWrite.All`
6. Click **Add permissions**
7. Click **Grant admin consent** (if you're admin)

#### 4. Configure Authentication

1. Navigate to **Authentication**
2. Under **Platform configurations**, click **Add a platform** → **Web**
3. Set **Redirect URIs**: `https://yourdomain.com/auth/callback`
4. Enable **Access tokens** and **ID tokens**
5. Click **Configure**

#### 5. Note Application (Client) ID

1. Go back to **Overview**
2. Copy **Application (client) ID** (you'll need this for OAuth)
3. Copy **Directory (tenant) ID** (for tenant-specific auth)

### Agent-API Token Storage Setup

#### 1. Store Tokens in Database

Tokens are stored in the `user_tokens` table in PostgreSQL:

```sql
-- Schema (already exists in hivegate)
CREATE TABLE user_tokens (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR NOT NULL,
    integration_key VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    token_type VARCHAR NOT NULL CHECK (token_type IN ('oauth2', 'api_key', 'jwt')),
    encrypted_token_data TEXT NOT NULL,  -- Fernet encrypted
    scopes TEXT,  -- JSON array
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE,
    UNIQUE(user_id, integration_key)
);
```

#### 2. Encrypt Tokens with Fernet

Agent-api uses Fernet encryption for token data:

```python
# Generate encryption key (one-time setup)
from cryptography.fernet import Fernet
key = Fernet.generate_key()
print(key.decode())  # Save as SECRET_TOKEN_ENC_KEY env var
```

#### 3. Environment Variables

Set in `.env` or deployment environment:

```bash
# Google OAuth
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret

# Microsoft OAuth
MICROSOFT_CLIENT_ID=your-app-id
MICROSOFT_CLIENT_SECRET=your-client-secret
MICROSOFT_TENANT_ID=your-tenant-id  # or "common" for multitenant

# Token encryption
SECRET_TOKEN_ENC_KEY=your-base64-fernet-key

# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=agent_api
DB_USER=postgres
DB_PASSWORD=password
```

#### 4. Store Token via API

```bash
# Example: Store Google Calendar token
curl -X POST http://localhost:8000/v2/users/user123/tokens \
  -H "Content-Type: application/json" \
  -d '{
    "integration_key": "google_calendar",
    "provider": "google",
    "token_type": "oauth2",
    "token_data": {
      "access_token": "ya29.a0...",
      "refresh_token": "1//0g...",
      "expires_in": 3600,
      "token_type": "Bearer"
    },
    "scopes": ["https://www.googleapis.com/auth/calendar"]
  }'
```

### Testing OAuth Flow

#### 1. Test with CLI

```bash
# Start demo app
python demo_toolkits_app.py

# Start CLI client
cd cli
python test_cards_client_tau2.py

# Send message that requires auth
You> List my calendar events

# If not authenticated, agent will return auth-required card
# Follow OAuth flow to authenticate
```

#### 2. Verify Token Storage

```bash
# Check token in database
curl http://localhost:8000/v2/users/user123/tokens/google_calendar

# Response should show token metadata (encrypted data not exposed)
{
  "id": 1,
  "user_id": "user123",
  "integration_key": "google_calendar",
  "provider": "google",
  "token_type": "oauth2",
  "scopes": ["https://www.googleapis.com/auth/calendar"],
  "expires_at": "2025-01-01T12:00:00Z",
  "is_active": true
}
```

#### 3. Test Token Refresh

```bash
# Wait for token to expire (or manually set expires_at in past)
# Make request that uses the token
# Agent should automatically refresh via refresh_token
```

### OAuth Scope Reference

#### Google Scopes

| Service | Scope | Access Level |
|---------|-------|--------------|
| Calendar | `https://www.googleapis.com/auth/calendar` | Read/Write |
| Calendar | `https://www.googleapis.com/auth/calendar.readonly` | Read-only |
| Gmail | `https://www.googleapis.com/auth/gmail.modify` | Read/Write/Delete |
| Gmail | `https://www.googleapis.com/auth/gmail.readonly` | Read-only |
| Contacts | `https://www.googleapis.com/auth/contacts` | Read/Write |
| Contacts | `https://www.googleapis.com/auth/contacts.readonly` | Read-only |
| Drive | `https://www.googleapis.com/auth/drive` | Full access |
| Drive | `https://www.googleapis.com/auth/drive.readonly` | Read-only |

#### Microsoft Graph Scopes

| Service | Scope | Access Level |
|---------|-------|--------------|
| Calendar | `Calendars.ReadWrite` | Read/Write |
| Calendar | `Calendars.Read` | Read-only |
| Mail | `Mail.ReadWrite` | Read/Write |
| Mail | `Mail.Read` | Read-only |
| Contacts | `Contacts.ReadWrite` | Read/Write |
| Contacts | `Contacts.Read` | Read-only |
| OneDrive | `Files.ReadWrite.All` | Full access |
| OneDrive | `Files.Read.All` | Read-only |

---

## Troubleshooting

### Common Development Issues

#### 1. Token Cache Not Working

**Symptom**: Toolkit fetches token on every request

**Causes**:
- Token cache key mismatch (service_name or user_id different)
- Token cache TTL expired (default 5 minutes)
- Token cache cleared between requests

**Solutions**:

```python
# Debug token cache
from toolkits.token_cache import token_cache

# Check cache contents
print(token_cache._cache)

# Check specific token
cached = token_cache.get_token("google_calendar", "user123")
print(f"Cached token: {cached}")

# Increase TTL for testing
token_cache.set_token("google_calendar", "user123", "token", ttl_seconds=600)
```

#### 2. Confirmation Workflow Not Triggering

**Symptom**: Tool executes immediately without confirmation

**Causes**:
- Missing `requires_confirmation` attribute
- Attribute set incorrectly (must be `True`, not truthy value)
- Agent not checking confirmation attribute

**Solutions**:

```python
# Verify attribute is set correctly
def my_tool(self, arg1: str):
    ...

# CORRECT
my_tool.requires_confirmation = True  # type: ignore[attr-defined]

# WRONG
my_tool.requires_confirmation = 1  # Truthy but not True
my_tool.confirmation_required = True  # Wrong attribute name

# Debug in agent
print(hasattr(toolkit.my_tool, "requires_confirmation"))
print(toolkit.my_tool.requires_confirmation == True)
```

#### 3. Provider Selection Error

**Symptom**: `ValueError: Unknown service: ...`

**Causes**:
- Service name doesn't match "google" or "microsoft" pattern
- Case sensitivity issue

**Solutions**:

```python
# Make provider selection case-insensitive
if "google" in service_name.lower():  # Use .lower()
    provider = GoogleServiceProvider(config)
elif "microsoft" in service_name.lower():
    provider = MicrosoftServiceProvider(config)

# Or use explicit mapping
PROVIDER_MAP = {
    "google_calendar": GoogleServiceProvider,
    "microsoft_calendar": MicrosoftServiceProvider,
    "gmail": GoogleServiceProvider,
    "outlook": MicrosoftServiceProvider,
}

provider_class = PROVIDER_MAP.get(service_name)
if not provider_class:
    raise ValueError(f"Unknown service: {service_name}")
provider = provider_class(config)
```

#### 4. OAuth Token Not Found

**Symptom**: `error_card("Authentication failed")`

**Causes**:
- Token not stored in hivegate database
- Wrong integration_key used for token lookup
- Token expired and refresh failed
- User not authenticated

**Solutions**:

```bash
# Check token exists in database
curl http://localhost:8000/v2/users/user123/tokens/google_calendar

# Verify integration_key matches
# Toolkit uses: fetch_token_func(user_id, "google_calendar")
# Database has: user_tokens.integration_key = "google_calendar"

# Check token expiration
# If expires_at is in the past, token should auto-refresh

# Debug token fetch
def fetch_access_token(user_id: str, integration_key: str):
    print(f"Fetching token for user={user_id}, key={integration_key}")
    # ... rest of implementation
```

#### 5. Type Errors with SQLAlchemy

**Symptom**: `mypy` errors about SQLAlchemy Column types

**Causes**:
- SQLAlchemy Column types don't match Pydantic model types
- mypy doesn't understand SQLAlchemy ORM patterns

**Solutions**:

```python
# Use type: ignore comments
agent_info = AgentInfoDB(
    id=agent_id,
    name=req.name,  # type: ignore[arg-type]
    description=req.description,  # type: ignore[arg-type]
)

# Or individual field assignments
agent_info = AgentInfoDB()
agent_info.id = agent_id  # type: ignore[assignment]
agent_info.name = req.name  # type: ignore[assignment]
```

#### 6. Event Loop Already Running Error

**Symptom**: `RuntimeError: This event loop is already running`

**Causes**:
- Calling `asyncio.run()` when FastAPI's event loop is running
- Using `loop.run_until_complete()` in async context

**Solutions**:

```python
# Use _await() helper from demo_toolkits_app.py
def _await(coro_or_func):
    """Run async coroutine from sync context."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    if not loop or not loop.is_running():
        # Standard async execution
        if callable(coro_or_func):
            coro = coro_or_func()
        else:
            coro = coro_or_func
        return asyncio.run(coro) if not loop else loop.run_until_complete(coro)

    # Event loop running - use separate thread
    import concurrent.futures
    def run_in_thread():
        if callable(coro_or_func):
            coro = coro_or_func()
        else:
            coro = coro_or_func
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(run_in_thread)
        return future.result()

# Use it
result = _await(lambda: async_function(arg1, arg2))
```

### Debugging Strategies

#### 1. Enable Debug Logging

```python
# In toolkit __init__
import logging
logging.basicConfig(level=logging.DEBUG)
self.logger = logging.getLogger(__name__)

# In methods
self.logger.debug(f"Fetching token for user={self.user_id}")
self.logger.debug(f"Calling provider with token={token[:10]}...")
self.logger.debug(f"Provider response: {result}")
```

#### 2. Use Mock Testing

```python
# Test without real API calls
@patch('workspace_suite.providers.google_service.GoogleServiceProvider.operation_name')
def test_toolkit_method(self, mock_operation):
    mock_operation.return_value = {"status": "success", "id": "123"}

    result = self.toolkit.perform_operation(field1="value")

    # Verify
    self.assertEqual(result["status"], "success")
    mock_operation.assert_called_once()
```

#### 3. API Explorer Tools

**Google APIs**:
- [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/) - Test OAuth flow
- [API Explorer](https://developers.google.com/apis-explorer) - Test API endpoints

**Microsoft Graph**:
- [Graph Explorer](https://developer.microsoft.com/en-us/graph/graph-explorer) - Test Graph API
- [JWT Decoder](https://jwt.ms/) - Inspect access tokens

#### 4. Network Traffic Inspection

```bash
# Use mitmproxy to inspect HTTP traffic
pip install mitmproxy
mitmproxy -p 8888

# Configure httpx to use proxy
client = httpx.Client(proxies="http://localhost:8888")
provider = GoogleServiceProvider(config, client)
```

#### 5. Database Query Debugging

```python
# Enable SQLAlchemy query logging
import logging
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

# This will print all SQL queries to console
```

### Error Patterns and Solutions

#### Pattern: "Invalid token" errors

**Solution**: Implement token refresh logic

```python
def _get_token(self) -> Optional[str]:
    """Get cached or fresh token with auto-refresh."""
    cached = token_cache.get_token(self.service_name, self.user_id)
    if cached:
        # Verify token is not expired
        if self._is_token_expired(cached):
            # Refresh token
            refreshed = self._refresh_token(cached)
            if refreshed:
                token_cache.set_token(self.service_name, self.user_id, refreshed)
                return refreshed
        return cached

    # Fetch fresh
    return self.fetch_token_func(self.user_id, self.service_name)
```

#### Pattern: "Rate limit exceeded" errors

**Solution**: Implement exponential backoff

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10)
)
def _call_api_with_retry(self, url, headers, payload):
    """Call API with automatic retry on rate limits."""
    response = self.client.post(url, headers=headers, json=payload)

    if response.status_code == 429:  # Rate limit
        retry_after = int(response.headers.get("Retry-After", 1))
        time.sleep(retry_after)
        raise Exception("Rate limited, retrying...")

    response.raise_for_status()
    return response.json()
```

#### Pattern: "Scope not granted" errors

**Solution**: Check OAuth scopes in token

```python
def _verify_scopes(self, required_scopes: List[str]) -> bool:
    """Verify token has required scopes."""
    # Fetch token metadata from hivegate
    token_data = self._get_token_metadata()
    granted_scopes = token_data.get("scopes", [])

    missing_scopes = set(required_scopes) - set(granted_scopes)
    if missing_scopes:
        self.logger.error(f"Missing scopes: {missing_scopes}")
        return False

    return True

def perform_operation(self, ...):
    """Operation requiring specific scopes."""
    required = ["https://www.googleapis.com/auth/calendar"]
    if not self._verify_scopes(required):
        return self.error_card("Insufficient permissions. Please re-authenticate.")

    # ... rest of method
```

---

## Deployment Checklist

### Environment Variables

#### Required Variables

```bash
# Service URLs
PROMPTS_SERVICE_URL=https://prompts-service.run.app
AGENTS_SERVICE_URL=https://hivegate.run.app
BASE_URL=https://chat-server.run.app

# Google Cloud
GOOGLE_SERVICE_ACCOUNT_KEY_PATH=/app/service-account.json
GOOGLE_APPLICATION_CREDENTIALS=/app/service-account.json

# Gemini Model
GENAI_MODEL_ID=gemini-2.5-pro
GENAI_API_KEY=your-production-api-key

# Database (hivegate)
DB_HOST=your-cloudsql-instance
DB_PORT=5432
DB_NAME=agent_api
DB_USER=postgres
DB_PASSWORD=your-secure-password

# Token Encryption
SECRET_TOKEN_ENC_KEY=your-base64-fernet-key

# OAuth Credentials
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
MICROSOFT_CLIENT_ID=your-app-id
MICROSOFT_CLIENT_SECRET=your-client-secret
MICROSOFT_TENANT_ID=common  # or specific tenant
```

#### Optional Variables

```bash
# Agent Storage (demo_toolkits_app)
AGENT_SQLITE_DB=/data/agents.db

# Qdrant Vector DB
QDRANT_URL=https://qdrant.example.com

# Logging
LOG_LEVEL=INFO
SENTRY_DSN=https://...@sentry.io/...

# CORS
ALLOWED_ORIGINS=https://yourdomain.com,https://app.yourdomain.com
```

### Production OAuth Setup

#### 1. Update OAuth Redirect URIs

**Google Cloud Console**:
1. Go to **APIs & Services** → **Credentials**
2. Edit OAuth 2.0 Client ID
3. Add **Authorized redirect URIs**:
   - `https://yourdomain.com/auth/callback`
   - Remove localhost URIs for production

**Microsoft Azure**:
1. Go to **App registrations** → Your app → **Authentication**
2. Add **Redirect URIs**:
   - `https://yourdomain.com/auth/callback`
3. Click **Save**

#### 2. Verify OAuth Consent

**Google**:
- Submit app for verification if using sensitive scopes
- Add privacy policy and terms of service URLs
- Complete OAuth consent screen branding

**Microsoft**:
- Configure Publisher domain
- Add privacy policy and terms of service
- Complete Publisher verification if needed

#### 3. Test OAuth Flow in Production

```bash
# Test Google OAuth
curl https://accounts.google.com/o/oauth2/v2/auth \
  ?client_id=your-client-id \
  &redirect_uri=https://yourdomain.com/auth/callback \
  &response_type=code \
  &scope=https://www.googleapis.com/auth/calendar \
  &access_type=offline

# Test Microsoft OAuth
curl https://login.microsoftonline.com/common/oauth2/v2.0/authorize \
  ?client_id=your-app-id \
  &redirect_uri=https://yourdomain.com/auth/callback \
  &response_type=code \
  &scope=Calendars.ReadWrite \
  &response_mode=query
```

### Cloud Run Deployment (Google Cloud)

#### 1. Build Docker Image

```bash
# Build image
./scripts/build_image.sh

# Tag for Google Container Registry
docker tag hivegate gcr.io/your-project-id/hivegate:latest

# Push to GCR
docker push gcr.io/your-project-id/hivegate:latest
```

#### 2. Deploy to Cloud Run

```bash
# Deploy with script
./scripts/deploy_to_cloud_run.sh

# Or manually
gcloud run deploy hivegate \
  --image gcr.io/your-project-id/hivegate:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "DB_HOST=your-cloudsql,DB_NAME=agent_api,..." \
  --set-secrets "SECRET_TOKEN_ENC_KEY=token-key:latest,GENAI_API_KEY=gemini-key:latest" \
  --add-cloudsql-instances your-project:us-central1:agent-db \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 80 \
  --min-instances 1 \
  --max-instances 10
```

#### 3. Configure Cloud SQL Connection

```bash
# Create Cloud SQL instance (if not exists)
gcloud sql instances create agent-db \
  --database-version POSTGRES_14 \
  --tier db-f1-micro \
  --region us-central1

# Create database
gcloud sql databases create agent_api --instance agent-db

# Create user
gcloud sql users create postgres --instance agent-db --password your-password
```

#### 4. Upload Service Account Key

```bash
# Create secret in Secret Manager
gcloud secrets create service-account-key \
  --data-file=agents/service-account.json

# Grant Cloud Run service access
gcloud secrets add-iam-policy-binding service-account-key \
  --member="serviceAccount:your-project-id@appspot.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Mount secret in Cloud Run
gcloud run services update hivegate \
  --set-secrets "/app/service-account.json=service-account-key:latest"
```

### Integration Testing in Production

#### 1. Health Check

```bash
# Check hivegate health
curl https://hivegate.run.app/health

# Expected response
{"status": "healthy", "version": "1.0.0"}
```

#### 2. Token Storage Test

```bash
# Store test token
curl -X POST https://hivegate.run.app/v2/users/testuser/tokens \
  -H "Content-Type: application/json" \
  -d '{
    "integration_key": "google_calendar",
    "provider": "google",
    "token_type": "oauth2",
    "token_data": {
      "access_token": "test-token",
      "refresh_token": "test-refresh",
      "expires_in": 3600
    },
    "scopes": ["https://www.googleapis.com/auth/calendar"]
  }'

# Verify retrieval
curl https://hivegate.run.app/v2/users/testuser/tokens/google_calendar
```

#### 3. End-to-End Toolkit Test

```bash
# Send chat message
curl -X POST https://chat-server.run.app/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "List my calendar events",
    "user_id": "testuser",
    "session_id": "test-session",
    "timezone": "America/Los_Angeles",
    "locale": "en-US"
  }'

# Verify response includes tools or results
```

#### 4. Monitor Logs

```bash
# Agent-API logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=hivegate" \
  --limit 50 \
  --format json

# Chat server logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=demo-app" \
  --limit 50 \
  --format json
```

### Monitoring and Logging

#### 1. Set Up Cloud Monitoring

```bash
# Create uptime check
gcloud monitoring uptime-checks create https://hivegate.run.app/health \
  --display-name "Agent API Health" \
  --check-interval 60

# Create alert policy
gcloud alpha monitoring policies create \
  --notification-channels your-channel-id \
  --display-name "Agent API Down" \
  --condition-display-name "Health check failing" \
  --condition-threshold-value 1 \
  --condition-threshold-duration 300
```

#### 2. Application Logging

```python
# In toolkit/service code
import logging
import json

logger = logging.getLogger(__name__)

def perform_operation(self, ...):
    """Operation with structured logging."""
    logger.info(
        "Operation started",
        extra={
            "user_id": self.user_id,
            "service_name": self.service_name,
            "operation": "perform_operation"
        }
    )

    try:
        result = self.service.perform_operation(...)

        logger.info(
            "Operation completed",
            extra={
                "user_id": self.user_id,
                "status": result.get("status"),
                "operation": "perform_operation"
            }
        )

        return result

    except Exception as e:
        logger.error(
            "Operation failed",
            extra={
                "user_id": self.user_id,
                "error": str(e),
                "operation": "perform_operation"
            },
            exc_info=True
        )
        raise
```

#### 3. Error Tracking with Sentry

```python
# Install sentry-sdk
# pip install sentry-sdk

# In main.py
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    integrations=[FastApiIntegration()],
    traces_sample_rate=1.0,
    environment="production"
)

# Errors will be automatically tracked
```

### Performance Optimization

#### 1. Token Cache Tuning

```python
# Increase TTL for production (reduce API calls)
token_cache.set_token(
    service_name,
    user_id,
    token,
    ttl_seconds=3600  # 1 hour (vs 5 min default)
)
```

#### 2. HTTP Client Connection Pooling

```python
# In provider __init__
self.client = http_client or httpx.Client(
    timeout=30.0,
    limits=httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20
    )
)
```

#### 3. Agent Caching

```python
# In demo_toolkits_app.py
# Cache agents by (user_id, session_id) to avoid rebuilding
_agent_cache: Dict[str, Agent] = {}

def get_or_create_agent(user_id: str, session_id: str) -> Agent:
    cache_key = f"{user_id}:{session_id}"

    if cache_key in _agent_cache:
        return _agent_cache[cache_key]

    agent = build_agent(user_id, session_id)
    _agent_cache[cache_key] = agent

    return agent
```

### Security Checklist

- [ ] **OAuth credentials**: Stored in Secret Manager (not in code)
- [ ] **Token encryption**: `SECRET_TOKEN_ENC_KEY` is secure and rotated
- [ ] **Database**: CloudSQL with private IP, no public access
- [ ] **Service account**: Minimal permissions (principle of least privilege)
- [ ] **HTTPS only**: All endpoints use HTTPS (no HTTP)
- [ ] **CORS**: `ALLOWED_ORIGINS` set to specific domains (not `*`)
- [ ] **Rate limiting**: Implemented for public endpoints
- [ ] **Input validation**: All user inputs validated and sanitized
- [ ] **Logging**: No sensitive data (tokens, passwords) in logs
- [ ] **Dependencies**: All packages updated to latest secure versions

---

## Common Patterns Reference

### Confirmation Workflow Pattern

```python
# In toolkit method
def write_operation(self, ...):
    """Write operation (requires confirmation)."""
    # Implementation
    return result

# Mark as requiring confirmation (Agno framework checks this)
write_operation.requires_confirmation = True  # type: ignore[attr-defined]
```

**How it works**:
1. Agent calls `write_operation`
2. Agno framework sees `requires_confirmation = True`
3. Agent pauses and returns tools to user
4. User confirms via `/chat/commit`
5. Agent resumes and executes operation

### Token Cache Pattern

```python
def _get_token(self) -> Optional[str]:
    """Get cached or fresh token."""
    if not self.auth or not self.fetch_token_func:
        return None

    # 1. Check cache (fast path)
    cached = token_cache.get_token(self.service_name, self.user_id)
    if cached:
        return cached

    # 2. Fetch fresh (slow path)
    token = self.fetch_token_func(self.user_id, self.service_name)
    if token:
        # 3. Cache for next time (5 min TTL by default)
        token_cache.set_token(self.service_name, self.user_id, token)

    return token
```

**Benefits**:
- Reduces API calls to hivegate
- Improves performance (cache hits are instant)
- Automatic expiration (TTL-based)
- Thread-safe (uses locks internally)

### Error Handling Pattern

```python
def perform_operation(self, ...):
    """Operation with comprehensive error handling."""
    # 1. Validate authentication
    token = self._get_token()
    if not token:
        return self.error_card("Authentication failed. Please reconnect.")

    try:
        # 2. Validate inputs
        req = ServiceRequest(field1=field1, field2=field2)

        # 3. Call service
        result = self.service.perform_operation(token, req)

        # 4. Handle service errors
        if result.get("status") == "error":
            error_msg = result.get("error", {}).get("message", "Unknown error")
            return self.error_card(error_msg)

        # 5. Return success
        return result

    except ValueError as e:
        # Input validation error
        return self.error_card(f"Invalid input: {str(e)}")

    except Exception as e:
        # Unexpected error
        return self.error_card(f"Failed to perform operation: {str(e)}")
```

**Error flow**:
1. Auth check → `error_card("Authentication failed")`
2. Input validation → `error_card("Invalid input: ...")`
3. Service error → `error_card(service_error_message)`
4. Unexpected error → `error_card("Failed to perform operation: ...")`

### Provider Selection Pattern

```python
def __init__(self, ..., service_name: str, ...):
    """Initialize with dynamic provider selection."""
    config = ProviderConfig(default_timezone=default_timezone)

    # Option 1: Pattern matching
    if "google" in service_name.lower():
        provider = GoogleServiceProvider(config)
    elif "microsoft" in service_name.lower():
        provider = MicrosoftServiceProvider(config)
    else:
        raise ValueError(f"Unknown service: {service_name}")

    # Option 2: Explicit mapping (preferred for clarity)
    PROVIDER_MAP = {
        "google_calendar": GoogleCalendarProvider,
        "microsoft_calendar": MicrosoftCalendarProvider,
        "google_drive": GoogleDriveProvider,
        "microsoft_drive": MicrosoftDriveProvider,
    }

    provider_class = PROVIDER_MAP.get(service_name)
    if not provider_class:
        raise ValueError(f"Unknown service: {service_name}")

    provider = provider_class(config)

    self.service = ServiceService(provider)
```

### Structured Response Pattern

```python
# Success response
{
  "status": "success",
  "id": "123",
  "field1": "value",
  "created_at": "2025-01-01T00:00:00Z",
  "message": "Operation completed successfully"
}

# Error response
{
  "status": "error",
  "error": {
    "message": "Human-readable error message",
    "code": 400,  # HTTP status code
    "details": {...}  # Optional additional context
  }
}

# Auth required card
{
  "card": "service-auth-required",
  "service": "google_calendar",
  "message": "Please connect your Google Calendar account",
  "context": {"token_valid": False}
}

# Generic error card
{
  "card": "error",
  "message": "Operation failed: ...",
  "actions": ["retry", "cancel"],
  "context": {"token_valid": True}
}
```

### Testing with MockTransport Pattern

```python
import httpx
import unittest

class TestProvider(unittest.TestCase):
    def setUp(self):
        """Set up mock HTTP client."""
        self.mock_transport = httpx.MockTransport(self._mock_handler)
        self.client = httpx.Client(transport=self.mock_transport)
        self.provider = GoogleServiceProvider(config, self.client)

    def _mock_handler(self, request: httpx.Request) -> httpx.Response:
        """
        Mock HTTP responses based on request.

        This runs in place of actual network calls.
        """
        # Check authentication
        if "Authorization" not in request.headers:
            return httpx.Response(
                401,
                json={"error": {"message": "Unauthorized"}}
            )

        # Success case
        if "create" in str(request.url):
            return httpx.Response(
                200,
                json={"id": "123", "status": "created"}
            )

        # Rate limit case
        if "rate-limited" in str(request.url):
            return httpx.Response(
                429,
                headers={"Retry-After": "60"},
                json={"error": {"message": "Too many requests"}}
            )

        # Default 404
        return httpx.Response(404)

    def test_success(self):
        """Test successful API call."""
        result = self.provider.create_item(...)
        self.assertEqual(result["status"], "success")

    def test_unauthorized(self):
        """Test unauthorized error."""
        # Clear auth header
        self.provider.client = httpx.Client(
            transport=self.mock_transport
        )
        result = self.provider.create_item(...)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], 401)
```

**Benefits**:
- No network calls (fast tests)
- Deterministic (no flaky tests)
- Test error scenarios easily
- No API quotas consumed

---

## Example: Slack Toolkit

Here's a complete example of adding a Slack toolkit to demonstrate all patterns:

### 1. Workspace Suite Models

**File**: `workspace_suite/models.py`

```python
@dataclass(frozen=True)
class SlackMessageRequest:
    """Request to send Slack message."""
    channel: str  # Channel ID or name
    text: str  # Message text
    thread_ts: Optional[str] = None  # Thread timestamp for replies

    def __post_init__(self):
        if not self.channel:
            raise ValueError("channel is required")
        if not self.text:
            raise ValueError("text is required")

@dataclass(frozen=True)
class SlackChannelListRequest:
    """Request to list Slack channels."""
    types: str = "public_channel,private_channel"  # Channel types
    limit: int = 100  # Max channels to return
```

### 2. Slack Provider Protocol

**File**: `workspace_suite/providers/slack_base.py`

```python
from typing import Protocol, Dict, Any
from workspace_suite.models import SlackMessageRequest, SlackChannelListRequest

class SlackProviderProtocol(Protocol):
    """Protocol for Slack provider implementations."""

    def send_message(
        self,
        token: str,
        request: SlackMessageRequest
    ) -> Dict[str, Any]:
        """Send message to Slack channel."""
        ...

    def list_channels(
        self,
        token: str,
        request: SlackChannelListRequest
    ) -> Dict[str, Any]:
        """List Slack channels."""
        ...
```

### 3. Slack Provider Implementation

**File**: `workspace_suite/providers/slack_provider.py`

```python
import httpx
from typing import Dict, Any, Optional
from workspace_suite.providers.slack_base import SlackProviderProtocol
from workspace_suite.config import ProviderConfig
from workspace_suite.models import SlackMessageRequest, SlackChannelListRequest

class SlackProvider:
    """Slack API implementation."""

    def __init__(
        self,
        config: ProviderConfig,
        http_client: Optional[httpx.Client] = None
    ):
        self.config = config
        self.client = http_client or httpx.Client(timeout=30.0)
        self.base_url = "https://slack.com/api"

    def send_message(
        self,
        token: str,
        request: SlackMessageRequest
    ) -> Dict[str, Any]:
        """Send message to Slack channel."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {
            "channel": request.channel,
            "text": request.text,
        }

        if request.thread_ts:
            payload["thread_ts"] = request.thread_ts

        try:
            response = self.client.post(
                f"{self.base_url}/chat.postMessage",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("ok"):
                return {
                    "status": "error",
                    "error": {
                        "message": data.get("error", "Unknown error"),
                        "code": 400
                    }
                }

            return {
                "status": "success",
                "ts": data.get("ts"),
                "channel": data.get("channel"),
                "message": "Message sent successfully"
            }

        except httpx.HTTPStatusError as e:
            return {
                "status": "error",
                "error": {
                    "message": str(e),
                    "code": e.response.status_code
                }
            }

        except httpx.RequestError as e:
            return {
                "status": "error",
                "error": {
                    "message": f"Network error: {str(e)}",
                    "code": 0
                }
            }

    def list_channels(
        self,
        token: str,
        request: SlackChannelListRequest
    ) -> Dict[str, Any]:
        """List Slack channels."""
        headers = {
            "Authorization": f"Bearer {token}"
        }

        params = {
            "types": request.types,
            "limit": request.limit
        }

        try:
            response = self.client.get(
                f"{self.base_url}/conversations.list",
                headers=headers,
                params=params
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("ok"):
                return {
                    "status": "error",
                    "error": {
                        "message": data.get("error", "Unknown error"),
                        "code": 400
                    }
                }

            channels = []
            for ch in data.get("channels", []):
                channels.append({
                    "id": ch.get("id"),
                    "name": ch.get("name"),
                    "is_private": ch.get("is_private", False),
                    "num_members": ch.get("num_members", 0)
                })

            return {
                "status": "success",
                "channels": channels,
                "total_count": len(channels)
            }

        except httpx.HTTPStatusError as e:
            return {
                "status": "error",
                "error": {
                    "message": str(e),
                    "code": e.response.status_code
                }
            }

        except httpx.RequestError as e:
            return {
                "status": "error",
                "error": {
                    "message": f"Network error: {str(e)}",
                    "code": 0
                }
            }
```

### 4. Slack Service

**File**: `workspace_suite/services/slack_service.py`

```python
from typing import Dict, Any
from workspace_suite.providers.slack_base import SlackProviderProtocol
from workspace_suite.models import SlackMessageRequest, SlackChannelListRequest

class SlackService:
    """High-level Slack service."""

    def __init__(self, provider: SlackProviderProtocol):
        self.provider = provider

    def send_message(
        self,
        token: str,
        req: SlackMessageRequest
    ) -> Dict[str, Any]:
        """Send Slack message with business logic."""
        if not token:
            return {
                "status": "error",
                "error": {
                    "message": "Authentication token required",
                    "code": 401
                }
            }

        return self.provider.send_message(token, req)

    def list_channels(
        self,
        token: str,
        req: SlackChannelListRequest
    ) -> Dict[str, Any]:
        """List Slack channels."""
        if not token:
            return {
                "status": "error",
                "error": {
                    "message": "Authentication token required",
                    "code": 401
                }
            }

        return self.provider.list_channels(token, req)
```

### 5. Slack Toolkit

**File**: `toolkits/slack_toolkit.py`

```python
from typing import Optional, Callable, Dict, Any
from agno.tools import Toolkit
from toolkits.token_cache import token_cache
from workspace_suite.services.slack_service import SlackService
from workspace_suite.providers.slack_provider import SlackProvider
from workspace_suite.config import ProviderConfig
from workspace_suite.models import SlackMessageRequest, SlackChannelListRequest

class SlackToolkit(Toolkit):
    """
    Slack toolkit for Agno agents.

    Features:
    - Send messages to channels
    - List channels
    - Thread replies
    - Confirmation workflow for sending messages
    """

    def __init__(
        self,
        user_id: str,
        service_name: str = "slack",
        auth: bool = False,
        fetch_token_func: Optional[Callable[[str, str], Optional[str]]] = None,
    ):
        super().__init__(name=f"{service_name}_toolkit")

        self.user_id = user_id
        self.service_name = service_name
        self.auth = auth
        self.fetch_token_func = fetch_token_func

        # Initialize provider
        config = ProviderConfig()
        provider = SlackProvider(config)
        self.service = SlackService(provider)

        # Register tools
        if self.auth:
            self.register(self.send_message)
            self.register(self.list_channels)
        else:
            self.register(self.auth_required)

    def _get_token(self) -> Optional[str]:
        """Get cached or fresh OAuth token."""
        if not self.auth or not self.fetch_token_func:
            return None

        cached = token_cache.get_token(self.service_name, self.user_id)
        if cached:
            return cached

        token = self.fetch_token_func(self.user_id, self.service_name)
        if token:
            token_cache.set_token(self.service_name, self.user_id, token)

        return token

    def send_message(
        self,
        channel: str,
        text: str,
        thread_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send message to Slack channel (requires confirmation).

        Args:
            channel: Channel ID or name (e.g., "#general" or "C1234567890")
            text: Message text
            thread_ts: Thread timestamp for replies (optional)

        Returns:
            Message result
        """
        token = self._get_token()
        if not token:
            return self.error_card("Authentication failed. Please reconnect.")

        try:
            req = SlackMessageRequest(
                channel=channel,
                text=text,
                thread_ts=thread_ts
            )

            result = self.service.send_message(token, req)

            if result.get("status") == "error":
                error_msg = result.get("error", {}).get("message", "Unknown error")
                return self.error_card(error_msg)

            return result

        except ValueError as e:
            return self.error_card(f"Invalid input: {str(e)}")

        except Exception as e:
            return self.error_card(f"Failed to send message: {str(e)}")

    send_message.requires_confirmation = True  # type: ignore[attr-defined]

    def list_channels(
        self,
        types: str = "public_channel,private_channel",
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        List Slack channels (no confirmation).

        Args:
            types: Channel types (comma-separated: public_channel, private_channel, mpim, im)
            limit: Maximum channels to return

        Returns:
            List of channels
        """
        token = self._get_token()
        if not token:
            return self.error_card("Authentication failed. Please reconnect.")

        try:
            req = SlackChannelListRequest(types=types, limit=limit)
            result = self.service.list_channels(token, req)

            if result.get("status") == "error":
                error_msg = result.get("error", {}).get("message", "Unknown error")
                return self.error_card(error_msg)

            return result

        except Exception as e:
            return self.error_card(f"Failed to list channels: {str(e)}")

    def auth_required(self) -> Dict[str, Any]:
        """Prompt for Slack authentication."""
        return {
            "card": "slack-auth-required",
            "service": "slack",
            "message": "Please connect your Slack workspace to use this feature.",
            "context": {"token_valid": False}
        }

    def error_card(self, message: str = "Unknown error") -> Dict[str, Any]:
        """Display error message."""
        token = self._get_token()
        return {
            "card": "error",
            "message": message,
            "actions": ["retry", "cancel"],
            "context": {"token_valid": bool(token)}
        }
```

### 6. Integration with Demo App

**File**: `demo_toolkits_app.py`

```python
from toolkits import SlackToolkit

def select_slack_toolkit(user_id: str) -> SlackToolkit:
    """Select Slack toolkit based on token availability."""
    slack_token = fetch_access_token(user_id, "slack")

    if slack_token:
        return SlackToolkit(
            user_id=user_id,
            service_name="slack",
            auth=True,
            fetch_token_func=fetch_access_token
        )
    else:
        return SlackToolkit(
            user_id=user_id,
            service_name="slack",
            auth=False
        )

# Add to toolkit selection
def select_toolkits_by_token_availability(user_id: str, organizer_email: str):
    return [
        select_calendar_toolkit(user_id, organizer_email),
        select_email_toolkit(user_id),
        select_slack_toolkit(user_id),  # Add Slack
    ]
```

### 7. OAuth Scopes

**Slack OAuth Scopes**:
- `chat:write` - Send messages
- `channels:read` - List public channels
- `groups:read` - List private channels
- `im:read` - List direct messages
- `mpim:read` - List group messages

### 8. Testing

**File**: `tests/workspace_suite/test_slack_provider.py`

```python
import unittest
import httpx
from workspace_suite.providers.slack_provider import SlackProvider
from workspace_suite.config import ProviderConfig
from workspace_suite.models import SlackMessageRequest

class TestSlackProvider(unittest.TestCase):
    def setUp(self):
        self.config = ProviderConfig()
        self.mock_transport = httpx.MockTransport(self._mock_handler)
        self.client = httpx.Client(transport=self.mock_transport)
        self.provider = SlackProvider(self.config, self.client)

    def _mock_handler(self, request: httpx.Request) -> httpx.Response:
        if "chat.postMessage" in str(request.url):
            return httpx.Response(200, json={
                "ok": True,
                "ts": "1234567890.123456",
                "channel": "C1234567890"
            })

        if "conversations.list" in str(request.url):
            return httpx.Response(200, json={
                "ok": True,
                "channels": [
                    {"id": "C1", "name": "general", "is_private": False},
                    {"id": "C2", "name": "random", "is_private": False}
                ]
            })

        return httpx.Response(404)

    def test_send_message_success(self):
        req = SlackMessageRequest(channel="#general", text="Hello")
        result = self.provider.send_message("fake-token", req)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["channel"], "C1234567890")

    def test_list_channels_success(self):
        from workspace_suite.models import SlackChannelListRequest
        req = SlackChannelListRequest()
        result = self.provider.list_channels("fake-token", req)

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["channels"]), 2)
```

This complete Slack example demonstrates all the patterns and best practices for adding a new toolkit to the agent system.

---

## Summary

This guide provides a complete blueprint for adding new toolkits to the agent system. Key steps:

1. **workspace_suite layer**: Define protocols, models, providers, and service
2. **Toolkit layer**: Create *Toolkit with confirmation workflow and token caching
3. **Integration**: Add to demo app's toolkit selection
4. **CLI (optional)**: Add formatters and dialogs for rich console UI
5. **Documentation**: Update CLAUDE.md and toolkits/README.md
6. **Testing**: Comprehensive unit and integration tests
7. **OAuth**: Set up Google/Microsoft OAuth and token storage
8. **Deployment**: Cloud Run deployment with monitoring

By following these patterns, you ensure consistency with existing toolkits and maintain the architectural principles of the system.

**Questions or Issues?** Refer to the Troubleshooting section or review existing toolkits (Calendar, Email, Contacts, Drive) for reference implementations.