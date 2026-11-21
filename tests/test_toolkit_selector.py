import unittest
from unittest.mock import MagicMock, patch

from agents.toolkit_selector import (
    create_datetime_resolver_tool,
    create_multi_toolkit_selector,
)


class TestDatetimeResolverTool(unittest.TestCase):
    """Tests for create_datetime_resolver_tool function."""

    def test_create_tool_returns_tool_object(self):
        """Test that the factory returns a tool object."""
        tool = create_datetime_resolver_tool("America/New_York")
        # The tool is decorated with @tool, so it has 'name' attribute
        self.assertTrue(hasattr(tool, "name"))
        self.assertEqual(tool.name, "resolve_datetime")

    @patch("dateparser.parse")
    def test_resolve_datetime_success(self, mock_parse):
        """Test successful datetime resolution."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # Create a mock datetime
        mock_dt = datetime(2025, 1, 15, 14, 30, tzinfo=ZoneInfo("America/New_York"))
        mock_parse.return_value = mock_dt

        tool = create_datetime_resolver_tool("America/New_York")
        # The tool's entrypoint is the actual function
        result = tool.entrypoint("tomorrow at 2:30pm")

        self.assertIsNotNone(result)
        self.assertIn("2025-01-15", result)

    @patch("dateparser.parse")
    def test_resolve_datetime_failure(self, mock_parse):
        """Test datetime resolution when parsing fails."""
        mock_parse.return_value = None

        tool = create_datetime_resolver_tool("UTC")
        result = tool.entrypoint("invalid datetime text")

        self.assertIsNone(result)

    def test_different_timezones(self):
        """Test creating tools with different timezones."""
        tool_ny = create_datetime_resolver_tool("America/New_York")
        tool_la = create_datetime_resolver_tool("America/Los_Angeles")
        tool_utc = create_datetime_resolver_tool("UTC")

        # All should have name attribute
        self.assertEqual(tool_ny.name, "resolve_datetime")
        self.assertEqual(tool_la.name, "resolve_datetime")
        self.assertEqual(tool_utc.name, "resolve_datetime")


class TestMultiToolkitSelector(unittest.TestCase):
    """Tests for create_multi_toolkit_selector function."""

    def setUp(self):
        """Set up test fixtures."""
        # Create mock toolkits
        self.mock_calendar_google = MagicMock()
        self.mock_calendar_google.__str__ = MagicMock(return_value="CalendarToolkit(google)")  # type: ignore[method-assign]

        self.mock_calendar_microsoft = MagicMock()
        self.mock_calendar_microsoft.__str__ = MagicMock(return_value="CalendarToolkit(microsoft)")  # type: ignore[method-assign]

        self.mock_calendar_no_auth = MagicMock()
        self.mock_calendar_no_auth.__str__ = MagicMock(return_value="CalendarToolkit(no_auth)")  # type: ignore[method-assign]

        self.mock_email_google = MagicMock()
        self.mock_email_google.__str__ = MagicMock(return_value="EmailToolkit(google)")  # type: ignore[method-assign]

        self.base_tools = [MagicMock(name="base_tool_1")]

    def test_create_selector_returns_callable(self):
        """Test that the factory returns a callable."""
        hook = create_multi_toolkit_selector(
            user_id="user123",
            timezone="UTC",
            base_tools=self.base_tools,
        )
        self.assertTrue(callable(hook))

    @patch("api.routes.v2.agents.has_access_tokens_batch")
    def test_selector_adds_google_calendar_when_token_exists(self, mock_has_tokens):
        """Test that Google calendar toolkit is added when token exists."""
        mock_has_tokens.return_value = {
            "google_calendar": True,
            "microsoft_calendar": False,
        }

        hook = create_multi_toolkit_selector(
            user_id="user123",
            timezone="UTC",
            base_tools=self.base_tools,
            calendar_google=self.mock_calendar_google,
            calendar_microsoft=self.mock_calendar_microsoft,
            calendar_no_auth=self.mock_calendar_no_auth,
        )

        # Create mock agent
        mock_agent = MagicMock()
        mock_agent._cached_toolkit_names = []

        # Run the hook
        hook(mock_agent, "test input")

        # Verify set_tools was called with google calendar
        mock_agent.set_tools.assert_called_once()
        tools_arg = mock_agent.set_tools.call_args[0][0]

        # Should have base_tools + google_calendar
        self.assertEqual(len(tools_arg), 2)
        self.assertIn(self.mock_calendar_google, tools_arg)

    @patch("api.routes.v2.agents.has_access_tokens_batch")
    def test_selector_adds_microsoft_calendar_when_google_missing(self, mock_has_tokens):
        """Test that Microsoft calendar toolkit is added when Google token is missing."""
        mock_has_tokens.return_value = {
            "google_calendar": False,
            "microsoft_calendar": True,
        }

        hook = create_multi_toolkit_selector(
            user_id="user123",
            timezone="UTC",
            base_tools=self.base_tools,
            calendar_google=self.mock_calendar_google,
            calendar_microsoft=self.mock_calendar_microsoft,
            calendar_no_auth=self.mock_calendar_no_auth,
        )

        mock_agent = MagicMock()
        mock_agent._cached_toolkit_names = []

        hook(mock_agent, "test input")

        mock_agent.set_tools.assert_called_once()
        tools_arg = mock_agent.set_tools.call_args[0][0]
        self.assertIn(self.mock_calendar_microsoft, tools_arg)

    @patch("api.routes.v2.agents.has_access_tokens_batch")
    def test_selector_adds_no_auth_when_no_tokens(self, mock_has_tokens):
        """Test that no-auth toolkit is added when no tokens exist."""
        mock_has_tokens.return_value = {
            "google_calendar": False,
            "microsoft_calendar": False,
        }

        hook = create_multi_toolkit_selector(
            user_id="user123",
            timezone="UTC",
            base_tools=self.base_tools,
            calendar_google=self.mock_calendar_google,
            calendar_microsoft=self.mock_calendar_microsoft,
            calendar_no_auth=self.mock_calendar_no_auth,
        )

        mock_agent = MagicMock()
        mock_agent._cached_toolkit_names = []

        hook(mock_agent, "test input")

        mock_agent.set_tools.assert_called_once()
        tools_arg = mock_agent.set_tools.call_args[0][0]
        self.assertIn(self.mock_calendar_no_auth, tools_arg)

    @patch("api.routes.v2.agents.has_access_tokens_batch")
    def test_selector_skips_disabled_toolkits(self, mock_has_tokens):
        """Test that disabled toolkits (None) are skipped - set_tools not called when cache matches."""
        mock_has_tokens.return_value = {
            "google_calendar": True,
        }

        hook = create_multi_toolkit_selector(
            user_id="user123",
            timezone="UTC",
            base_tools=self.base_tools,
            # All toolkits are None (disabled)
        )

        mock_agent = MagicMock()
        # Pre-set cache to empty - simulates previous run with no toolkits
        mock_agent._cached_toolkit_names = []

        hook(mock_agent, "test input")

        # When cache matches current config (both empty), set_tools is NOT called
        # This is the optimization to avoid redundant updates
        mock_agent.set_tools.assert_not_called()

    @patch("api.routes.v2.agents.has_access_tokens_batch")
    def test_selector_respects_tools_filter(self, mock_has_tokens):
        """Test that tools_filter limits which toolkits are added."""
        mock_has_tokens.return_value = {
            "google_calendar": True,
            "google_gmail": True,
        }

        hook = create_multi_toolkit_selector(
            user_id="user123",
            timezone="UTC",
            base_tools=self.base_tools,
            tools_filter=["calendar"],  # Only allow calendar
            calendar_google=self.mock_calendar_google,
            email_google=self.mock_email_google,
        )

        mock_agent = MagicMock()
        mock_agent._cached_toolkit_names = []

        hook(mock_agent, "test input")

        mock_agent.set_tools.assert_called_once()
        tools_arg = mock_agent.set_tools.call_args[0][0]

        # Should have calendar but not email
        self.assertIn(self.mock_calendar_google, tools_arg)
        self.assertNotIn(self.mock_email_google, tools_arg)

    @patch("api.routes.v2.agents.has_access_tokens_batch")
    def test_selector_caches_toolkit_names(self, mock_has_tokens):
        """Test that toolkit configuration is cached to avoid redundant updates."""
        mock_has_tokens.return_value = {
            "google_calendar": True,
        }

        hook = create_multi_toolkit_selector(
            user_id="user123",
            timezone="UTC",
            base_tools=self.base_tools,
            calendar_google=self.mock_calendar_google,
        )

        mock_agent = MagicMock()
        mock_agent._cached_toolkit_names = []

        # First call - should update tools
        hook(mock_agent, "test input")
        self.assertEqual(mock_agent.set_tools.call_count, 1)

        # Get the cached names
        cached = mock_agent._cached_toolkit_names

        # Second call with same configuration - should NOT update
        mock_agent._cached_toolkit_names = cached
        hook(mock_agent, "test input")

        # set_tools should still only have been called once
        self.assertEqual(mock_agent.set_tools.call_count, 1)

    @patch("api.routes.v2.agents.has_access_tokens_batch")
    def test_selector_updates_on_config_change(self, mock_has_tokens):
        """Test that toolkit configuration is updated when tokens change."""
        hook = create_multi_toolkit_selector(
            user_id="user123",
            timezone="UTC",
            base_tools=self.base_tools,
            calendar_google=self.mock_calendar_google,
            calendar_microsoft=self.mock_calendar_microsoft,
        )

        mock_agent = MagicMock()
        mock_agent._cached_toolkit_names = []

        # First call - Google token exists
        mock_has_tokens.return_value = {"google_calendar": True, "microsoft_calendar": False}
        hook(mock_agent, "test input")
        self.assertEqual(mock_agent.set_tools.call_count, 1)

        # Second call - token changes to Microsoft
        mock_has_tokens.return_value = {"google_calendar": False, "microsoft_calendar": True}
        hook(mock_agent, "test input")

        # set_tools should be called again
        self.assertEqual(mock_agent.set_tools.call_count, 2)


def _select_tasks(user_id, connected, tools_filter=None):
    from types import SimpleNamespace

    from toolkits.tasks import TasksToolkit

    candidates = TasksToolkit.provider_candidates(user_id, lambda user, key: None)
    hook = create_multi_toolkit_selector(
        user_id=user_id,
        timezone="UTC",
        base_tools=[],
        tools_filter=tools_filter,
        toolkit_candidates={"tasks": candidates},
    )
    agent = SimpleNamespace(set_tools=MagicMock())
    with patch("api.routes.v2.agents.has_access_tokens_batch") as has_tokens:
        has_tokens.side_effect = lambda user, keys: {key: key in connected.get(user, ()) for key in keys}
        hook(agent, None)
        [[user, keys], _] = has_tokens.call_args
    assert (user, keys[-2:]) == (user_id, ["google_tasks", "microsoft_todo"])
    if not agent.set_tools.called:
        return []
    [[tools], _] = agent.set_tools.call_args
    return [(t.user_id, t.service_name, t.auth) for t in tools]


def test_selector_picks_each_users_tasks_provider_in_order():
    connected = {"alice": ("google_tasks", "microsoft_todo"), "bob": ("microsoft_todo",)}

    assert _select_tasks("alice", connected) == [("alice", "google_tasks", True)]
    assert _select_tasks("bob", connected) == [("bob", "microsoft_todo", True)]
    assert _select_tasks("carol", connected) == [("carol", "tasks", False)]


def test_selector_applies_tools_filter_to_candidate_groups():
    assert _select_tasks("alice", {"alice": ("google_tasks",)}, tools_filter=["Calendar"]) == []
    assert _select_tasks("alice", {"alice": ("google_tasks",)}, tools_filter=["TASKS"]) == [
        ("alice", "google_tasks", True)
    ]


if __name__ == "__main__":
    unittest.main()
