# Contributing a New Toolkit

Quick-start guide for adding a new toolkit to the hivegate.

## Architecture (3 Layers)

```
workspace_suite/          # Layer 1: API providers (Google, Microsoft)
  providers/              #   Protocol-based, vendor-specific implementations
  services/               #   Vendor-agnostic service wrappers
  models.py               #   Shared dataclasses

toolkits/                 # Layer 2: Agno agent toolkits
  base.py                 #   BaseToolkit (abstract base class)
  calendar.py             #   CalendarToolkit, EmailToolkit, etc.

tests/                    # Layer 3: Tests
  workspace_suite/        #   Provider tests (httpx MockTransport)
  test_toolkits_*.py      #   Toolkit unit tests
```

**Data flow**: User -> Toolkit (adapter) -> workspace_suite Service -> Provider -> External API

## 10-Step Checklist

1. **Add provider protocol** in `workspace_suite/providers/<service>_base.py`
2. **Implement provider** in `workspace_suite/providers/google_<service>.py` (and/or `microsoft_<service>.py`)
3. **Add models** to `workspace_suite/models.py` (frozen dataclasses)
4. **Create service** in `workspace_suite/services/<service>_service.py`
5. **Create toolkit** in `toolkits/<service>.py` inheriting from `BaseToolkit`
6. **Register tools** using `self.register()` in `_initialize_service()`
7. **Export** from `toolkits/__init__.py`
8. **Write provider tests** with `httpx.MockTransport` in `tests/workspace_suite/`
9. **Write toolkit tests** covering auth and no-auth states
10. **Run validation**: `./scripts/run_validate.sh`

## BaseToolkit Interface

All toolkits inherit from `BaseToolkit` (in `toolkits/base.py`), which extends `agno.tools.Toolkit`:

| Method | Type | Purpose |
|--------|------|---------|
| `__init__(user_id, service_name, auth, fetch_token_func)` | Constructor | Sets up user context and calls `_initialize_service()` |
| `_initialize_service()` | Abstract | Initialize providers and register tools |
| `_prepare_auth(hard=False)` | Inherited | Fetch OAuth token via shared `TokenCache` |
| `error_card(message)` | Inherited | Return standardized error dict to the agent |

## Minimal Toolkit Example

```python
from typing import Any, Callable, Dict, List, Optional

from toolkits.base import BaseToolkit


class NotesToolkit(BaseToolkit):
    def __init__(
        self,
        user_id: str,
        service_name: str = "google_notes",
        auth: bool = True,
        fetch_token_func: Optional[Callable] = None,
    ):
        super().__init__(user_id=user_id, service_name=service_name, auth=auth, fetch_token_func=fetch_token_func)

    def _initialize_service(self) -> None:
        if self.auth:
            self.register(self.list_notes)
            self.register(self.create_note)
        else:
            self.register(self.auth_required)

    def auth_required(self) -> Dict[str, Any]:
        """Prompt user to authenticate."""
        return {"card": "auth_required", "service": self.service_name}

    def list_notes(self) -> Dict[str, Any]:
        """List all notes."""
        token = self._prepare_auth()
        if not token:
            return self.error_card("Authentication failed")
        # Call workspace_suite service here
        return {"card": "notes_list", "notes": []}

    def create_note(self, title: str, content: str) -> Dict[str, Any]:
        """Create a new note. Requires confirmation."""
        token = self._prepare_auth()
        if not token:
            return self.error_card("Authentication failed")
        return {"card": "note_created", "title": title}
```

## Testing Patterns

```python
from toolkits import token_cache

class TestNotesToolkit(unittest.TestCase):
    def setUp(self):
        token_cache.clear()  # Isolate tests

    def test_no_auth(self):
        toolkit = NotesToolkit(user_id="u1", auth=False)
        result = toolkit.auth_required()
        assert result["card"] == "auth_required"

    def test_with_auth(self):
        toolkit = NotesToolkit(
            user_id="u1",
            auth=True,
            fetch_token_func=lambda uid, svc: "mock-token",
        )
        result = toolkit.list_notes()
        assert "notes" in result

    def test_auth_failure(self):
        toolkit = NotesToolkit(
            user_id="u1",
            auth=True,
            fetch_token_func=lambda uid, svc: None,
        )
        result = toolkit.list_notes()
        assert result["card"] == "error"
```

Key testing practices:
- Clear `token_cache` in `setUp` to prevent test contamination
- Test both `auth=True` and `auth=False` initialization paths
- Test token fetch failure (returns `None`)
- Use `httpx.MockTransport` for provider-level tests (no network calls)

## Full Guide

See `docs/toolkit-development-guide.md` for the complete development guide with detailed examples, confirmation workflows, and deployment instructions.
