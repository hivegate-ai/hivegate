# Toolkits

AI agent toolkits for various integrations and services.

## Overview

This package provides a collection of toolkits that enable AI agents to interact with external services and APIs. Each toolkit is designed to work seamlessly with the Agno 2.0 agent framework and follows consistent patterns for authentication, error handling, and user confirmation.

## Package Structure

```
toolkits/
├── __init__.py              # Package initialization and exports
├── README.md                # This file
├── token_cache.py           # Shared token caching system
├── calendar.py              # Calendar toolkit (Google, Microsoft)
├── contacts.py              # Contacts toolkit (Google, Microsoft)
├── email.py                 # Email toolkit (Gmail, Outlook)
├── drive.py                 # Drive toolkit (Google Drive, OneDrive)
└── API_REFERENCE.md         # Method specifications
```

## Available Toolkits

### CalendarToolkit

Multi-provider calendar management with support for:
- **Google Calendar**: Event creation, scheduling, invitations
- **Microsoft Calendar**: Event creation, scheduling, invitations

**Features**:
- ✅ Confirmation-based workflow (user reviews before execution)
- ✅ OAuth token management with caching
- ✅ Multi-provider support
- ✅ Graceful authentication fallback
- ✅ Rich error handling

**Usage**:
```python
from toolkits import CalendarToolkit

toolkit = CalendarToolkit(
    user_id="user123",
    organizer_email="user@example.com",
    service_name="google_calendar",
    auth=True
)
```

**Tools Provided**:
- `schedule_meeting`: Create calendar events (requires confirmation)
- `auth_required`: Prompt for authentication (when auth=False)
- `error_card`: Display errors with retry options

## Additional Agent Tools

### BrightDataTools (Agno Built-in)

**Note**: BrightDataTools is an Agno framework built-in tool, not a toolkit. It's added directly to agents as a standalone tool.

Web scraping and search capabilities powered by BrightData API:
- **Web Scraping**: Extract webpage content as markdown optimized for AI
- **SERP API**: Get search engine results from Google, Bing, and other engines
- **Screenshots**: Capture webpage screenshots
- **Structured Data**: Extract structured data from web pages

**Features**:
- ✅ API key-based authentication (no OAuth)
- ✅ Markdown output optimized for LLMs
- ✅ Multiple search engine support
- ✅ Configurable zones and settings
- ✅ No confirmation required (read-only operations)

**Environment Variables**:
```bash
BRIGHT_DATA_API_KEY=your_api_key_here          # Required
BRIGHT_DATA_WEB_UNLOCKER_ZONE=web_unlocker1    # Optional (default: web_unlocker1)
BRIGHT_DATA_SERP_ZONE=serp_api                 # Optional (default: serp_api)
```

**Usage with Agent**:
```python
from agno.agent import Agent
from agno.tools.brightdata import BrightDataTools

agent = Agent(
    tools=[
        BrightDataTools(),  # Add BrightData tools directly
        # ... other toolkits ...
    ]
)
```

**Tools Provided**:
- `scrape_url`: Scrape webpage content as markdown
- `search_web`: Get search engine results (SERP)
- Additional BrightData API capabilities

**Setup**:
1. Sign up for BrightData account at https://brightdata.com
2. Get your API key from the BrightData dashboard
3. Set `BRIGHT_DATA_API_KEY` environment variable
4. Add `BrightDataTools()` to your agent's tools list

## Token Management

All toolkits share a common token caching system defined in `token_cache.py`.

### TokenCache

Thread-safe token cache for OAuth access tokens.

**Features**:
- Cross-toolkit token sharing
- Multi-user token isolation
- TTL-based token expiration (default: 5 minutes)
- Error cooldown periods (default: 5 seconds)
- Thread-safe concurrent access

**Usage**:
```python
from toolkits.token_cache import TokenCache

# Create cache instance (typically one per application)
token_cache = TokenCache(ttl_seconds=300.0, cooldown_seconds=5.0)

# Get token (fetches if missing/stale, returns cached if fresh)
token = token_cache.get_token(
    service_name="google_calendar",
    user_id="user123",
    fetcher=lambda: fetch_access_token("user123", "google_calendar"),
    force=False
)

# Invalidate specific token
token_cache.invalidate("google_calendar", "user123")

# Clear all tokens
token_cache.clear()

# Get statistics
stats = token_cache.get_cache_stats()
print(f"Total cached tokens: {stats['total_entries']}")
```

### Token Architecture

Tokens are keyed by `(service_name, user_id)` tuple:
- **Service isolation**: Different services use separate tokens
- **User isolation**: Different users have separate tokens
- **No cross-contamination**: Tokens never leak between services or users

**Example keys**:
```python
("google_calendar", "user123")      # User 123's Google Calendar token
("microsoft_calendar", "user123")   # User 123's Microsoft Calendar token
("google_calendar", "user456")      # User 456's Google Calendar token
```

### Token Lifecycle

```
┌─────────────────────────────────────────────────────┐
│  1. Request token via get_token()                   │
└────────────────┬────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────┐
│  2. Check cache for fresh token (within TTL)        │
│     - If found: return immediately                  │
│     - If stale: continue to step 3                  │
│     - If in cooldown: return stale token            │
└────────────────┬────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────┐
│  3. Call fetcher function to get new token          │
│     - Success: cache token with timestamp           │
│     - Error: record error timestamp, enter cooldown │
└────────────────┬────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────┐
│  4. Return token to caller                          │
└─────────────────────────────────────────────────────┘
```

## Confirmation Workflow

All destructive operations (like creating calendar events) use a confirmation workflow:

### Process

```
┌────────────────────────────────────────────────────┐
│  1. Agent calls tool with proposed parameters      │
└───────────────┬────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────┐
│  2. Run pauses (status: "paused")                  │
│     - Tool marked as requires_confirmation         │
│     - Tool details returned to client              │
└───────────────┬────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────┐
│  3. Client displays interactive form               │
│     - User can review/edit parameters              │
│     - User chooses: Confirm / Skip / Cancel        │
└───────────────┬────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────┐
│  4. Client sends confirmed tools to /chat/commit   │
│     - Only confirmed tools are sent                │
│     - Skipped tools are omitted                    │
└───────────────┬────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────┐
│  5. Agent resumes and executes tools               │
│     - Actual API calls happen now                  │
│     - Results returned to client                   │
└───────────────┬────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────┐
│  6. Client displays formatted results              │
│     - Beautiful Rich-formatted output              │
│     - Success confirmation or error message        │
└────────────────────────────────────────────────────┘
```

### Benefits

- ✅ **User Control**: Users approve all actions before execution
- ✅ **Safety**: Prevents accidental or unwanted operations
- ✅ **Editability**: Users can modify parameters before confirming
- ✅ **Transparency**: Users see exactly what will happen

## Creating a New Toolkit

### Step 1: Define Toolkit Class

```python
from agno.tools import Toolkit
from typing import Dict, Any, Optional, List

class MyServiceToolkit(Toolkit):
    """
    Toolkit for MyService integration.

    Provides tools for interacting with MyService API.
    """

    def __init__(self, user_id: str, service_name: str, auth: bool = True):
        super().__init__(
            name="my_service_toolkit",
            tools=[
                self.my_action if auth else self.auth_required,
            ],
            requires_confirmation_tools=["my_action"] if auth else [],
            show_result_tools=[],
            stop_after_tool_call_tools=([] if auth else ["auth_required"]),
        )
        self.user_id = user_id
        self.service_name = service_name
        self.context: Dict[str, Any] = {"token_valid": False}
```

### Step 2: Implement Token Management

```python
from toolkits.token_cache import token_cache, fetch_access_token

def _prepare_auth(self, *, hard: bool = False) -> Optional[str]:
    """Get valid token using shared cache."""
    token = token_cache.get_token(
        service_name=self.service_name,
        user_id=self.user_id,
        fetcher=lambda: fetch_access_token(self.user_id, self.service_name),
        force=hard
    )
    self.context["token_valid"] = bool(token)
    return token if token else None
```

### Step 3: Implement Tools

```python
def my_action(self, param1: str, param2: int = 10) -> Dict[str, Any]:
    """
    Perform action on MyService.

    Args:
        param1: Description of param1
        param2: Description of param2

    Returns:
        Result dictionary with status and data
    """
    # Get token
    token = self._prepare_auth(hard=True)
    if not token:
        return self.error_card("Authentication required")

    # Make API call
    result = call_my_service_api(token, param1, param2)

    # Return structured result
    return {
        "status": "success",
        "data": result
    }

def service_auth_required(self) -> Dict[str, Any]:
    """Prompt for authentication."""
    self._prepare_auth(hard=False)
    return {
        "card": "service-auth-required",
        "context": dict(self.context),
    }

def error_card(self, message: str = "Unknown error") -> Dict[str, Any]:
    """Display error message."""
    return {
        "card": "error",
        "message": message,
        "context": dict(self.context),
    }
```

### Step 4: Add to Package

```python
# In toolkits/__init__.py
from toolkits.my_service import MyServiceToolkit

__all__ = [
    "CalendarToolkit",
    "MyServiceToolkit",  # Add new toolkit
]
```

### Step 5: Create Rich Formatter (Optional)

```python
# In test_cards_client_tau2.py ResultFormatter class

def format_result(self, tool: Dict[str, Any], result: Any) -> None:
    tool_name = tool.get("tool_name")

    if tool_name == "my_action":
        self._format_my_action(tool, result)
    # ... other formatters ...

def _format_my_action(self, tool: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Format my_action results beautifully."""
    from rich.text import Text
    from rich.panel import Panel

    # Extract data
    status = result.get("status", "unknown")
    data = result.get("data", {})

    # Build content
    content = Text()
    content.append("✓ Action completed successfully!\n\n", style="bold green")
    content.append(f"Status: {status}\n", style="cyan")

    # Display in panel
    self.console.print(Panel(
        content,
        title="[bold green]MyService Action[/bold green]",
        border_style="green"
    ))
```

## Best Practices

### 1. Token Management
- ✅ Always use shared `token_cache` for token storage
- ✅ Set appropriate TTL based on token expiration (default: 5 minutes)
- ✅ Implement `_prepare_auth()` with `hard` parameter
- ✅ Check token validity before API calls
- ❌ Don't store tokens in instance variables
- ❌ Don't log tokens or include in error messages

### 2. Error Handling
- ✅ Return structured error cards with `error_card()`
- ✅ Provide actionable error messages
- ✅ Include retry options where appropriate
- ✅ Log errors with context (no sensitive data)
- ❌ Don't raise exceptions from tools (return error cards)
- ❌ Don't expose internal error details to users

### 3. Confirmation Workflow
- ✅ Mark destructive operations as `requires_confirmation_tools`
- ✅ Allow user to edit parameters before execution
- ✅ Provide clear descriptions of what will happen
- ✅ Include "skip" option for each tool
- ❌ Don't execute operations without confirmation
- ❌ Don't assume user wants all proposed actions

### 4. Documentation
- ✅ Write comprehensive docstrings for all tools
- ✅ Include usage examples in docstrings
- ✅ Document all parameters and return values
- ✅ Explain when tools are called
- ✅ Provide integration examples

## Testing

### Unit Tests

```python
import unittest
from toolkits import CalendarToolkit

class TestCalendarToolkit(unittest.TestCase):
    def setUp(self):
        self.toolkit = CalendarToolkit(
            user_id="test_user",
            organizer_email="test@example.com",
            service_name="google_calendar"
        )

    def test_schedule_meeting(self):
        # Mock token fetch
        # Test meeting creation
        pass

    def test_service_auth_required(self):
        # Test auth prompt
        result = self.toolkit.service_auth_required()
        self.assertEqual(result["card"], "service-auth-required")
```

### Integration Tests

```python
# Test with real agent
from agno.agent import Agent

agent = Agent(
    tools=[toolkit],
    system_message="Test agent"
)

response = agent.run("Schedule a meeting tomorrow at 2pm")
# Verify response structure
```

## Troubleshooting

### Token Issues

**Problem**: Token not found
**Solution**: Check that user has authenticated via OAuth flow

**Problem**: Token expired
**Solution**: Cache automatically refreshes; check TTL settings

**Problem**: Token fetch fails
**Solution**: Check backend service connectivity and credentials

### Confirmation Issues

**Problem**: Tool executes without confirmation
**Solution**: Ensure tool is in `requires_confirmation_tools` list

**Problem**: Confirmation form doesn't appear
**Solution**: Check that `requires_confirmation` flag is set on tool

### Import Issues

**Problem**: Can't import toolkit
**Solution**: Ensure package is in Python path; check `__init__.py` exports

## References

- **Agno Framework**: https://github.com/agno-agi/agno
- **Rich Library**: https://rich.readthedocs.io/
- **InquirerPy**: https://inquirerpy.readthedocs.io/
- **Tool Confirmation Documentation**: See `../TOOL_CONFIRMATION_RICH_DIALOGS.md`

## License

Copyright (c) 2025 Development Team

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history.

### Planned Features
- Webhook support
- Real-time notifications

---

**Last Updated**: 2026-01-19
**Version**: 2.0.0
