import os
import unittest
from unittest.mock import patch

from api.settings import ApiSettings


class TestDebugModeConfiguration(unittest.TestCase):
    """Test cases for AGENT_DEBUG_MODE / TESTING debug mode configuration."""

    def test_debug_mode_disabled_when_testing_false(self):
        """Test that debug mode is disabled when TESTING=false."""
        with patch.dict(os.environ, {"TESTING": "false"}):
            settings = ApiSettings()
            self.assertFalse(settings.agent_debug_mode)

    def test_debug_mode_enabled_when_testing_true(self):
        """Test that debug mode is enabled when TESTING=true."""
        with patch.dict(os.environ, {"TESTING": "true"}):
            settings = ApiSettings()
            self.assertTrue(settings.agent_debug_mode)

    def test_debug_mode_enabled_when_testing_true_case_insensitive(self):
        """Test that debug mode is enabled when TESTING=True (case insensitive)."""
        with patch.dict(os.environ, {"TESTING": "True"}):
            settings = ApiSettings()
            self.assertTrue(settings.agent_debug_mode)

    def test_debug_mode_disabled_by_default(self):
        """Test that debug mode defaults to disabled when TESTING is not set."""
        with patch.dict(os.environ, {}, clear=True):
            settings = ApiSettings()
            self.assertFalse(settings.agent_debug_mode)

    def test_debug_mode_ignores_environment_variable(self):
        """Test that debug mode ignores ENVIRONMENT and uses AGENT_DEBUG_MODE / TESTING."""
        with patch.dict(
            os.environ,
            {"ENVIRONMENT": "development", "TESTING": "false", "AGENT_DEBUG_MODE": "false"},
            clear=False,
        ):
            settings = ApiSettings()
            self.assertFalse(settings.agent_debug_mode)

        with patch.dict(
            os.environ,
            {"ENVIRONMENT": "production", "TESTING": "false", "AGENT_DEBUG_MODE": "true"},
            clear=False,
        ):
            settings = ApiSettings()
            self.assertTrue(settings.agent_debug_mode)

    def test_agent_debug_mode_env_true(self):
        """AGENT_DEBUG_MODE=true enables debug mode even when TESTING is unset/false."""
        with patch.dict(os.environ, {"AGENT_DEBUG_MODE": "true", "TESTING": "false"}, clear=False):
            settings = ApiSettings()
            self.assertTrue(settings.agent_debug_mode)

    def test_agent_debug_mode_env_false_overrides_testing(self):
        """AGENT_DEBUG_MODE=false keeps debug off even if TESTING=true."""
        with patch.dict(os.environ, {"AGENT_DEBUG_MODE": "false", "TESTING": "true"}, clear=False):
            settings = ApiSettings()
            self.assertFalse(settings.agent_debug_mode)

    def test_agent_debug_mode_falls_back_to_testing(self):
        """When AGENT_DEBUG_MODE is unset, TESTING still controls debug mode."""
        env = {k: v for k, v in os.environ.items() if k != "AGENT_DEBUG_MODE"}
        env["TESTING"] = "true"
        with patch.dict(os.environ, env, clear=True):
            settings = ApiSettings()
            self.assertTrue(settings.agent_debug_mode)

    def test_debug_mode_various_testing_true_values(self):
        """Test that various truthy values for TESTING enable debug mode."""
        true_values = ["true", "True", "TRUE", "1", "yes", "YES", "on", "ON"]

        for true_val in true_values:
            with self.subTest(testing_value=true_val):
                with patch.dict(os.environ, {"TESTING": true_val}):
                    settings = ApiSettings()
                    self.assertTrue(settings.agent_debug_mode)

    def test_debug_mode_various_testing_false_values(self):
        """Test that various falsy values for TESTING disable debug mode."""
        false_values = ["false", "False", "FALSE", "0", "no", "NO", "off", "OFF", ""]

        for false_val in false_values:
            with self.subTest(testing_value=false_val):
                with patch.dict(os.environ, {"TESTING": false_val}):
                    settings = ApiSettings()
                    self.assertFalse(settings.agent_debug_mode)


class TestAgentDebugModeIntegration(unittest.TestCase):
    """Test cases for agent debug mode integration with selectors."""

    def test_v2_selector_uses_environment_setting(self):
        """Test that V2 selector uses the environment debug mode setting."""
        from unittest.mock import MagicMock, patch

        from agents.v2_selector import get_agent

        # Mock the dependencies
        mock_db = MagicMock()
        mock_agent_info = MagicMock()
        mock_agent_info.prompt_service_id = "test-prompt-123"
        mock_prompt = MagicMock()

        with (
            patch("agents.v2_selector.api_settings") as mock_settings,
            patch("agents.v2_selector.get_available_agents", return_value=["test-agent"]),
            patch("agents.v2_selector.get_agent_info", return_value=mock_agent_info),
            patch("agents.v2_selector._get_prompt_from_local_storage", return_value=mock_prompt),
            patch("agents.v2_selector.get_agent_impl") as mock_get_agent,
        ):
            # Mock the settings
            mock_settings.agent_debug_mode = False
            # Call get_agent without debug_mode parameter
            get_agent(agent_id="test-agent", db=mock_db)

            # Verify it was called with the environment setting
            mock_get_agent.assert_called_once()
            call_kwargs = mock_get_agent.call_args[1]
            # debug_mode should be passed as keyword argument
            self.assertFalse(call_kwargs["debug_mode"])

    def test_agent_uses_environment_setting(self):
        """Test that agent uses the environment debug mode setting."""
        from unittest.mock import MagicMock, patch

        from agents.agent import get_agent

        # Mock the dependencies
        mock_prompt = MagicMock()
        mock_prompt.name = "test-agent"
        mock_prompt.description = "Test Agent"
        mock_prompt.template = "Test template"

        # Mock knowledge service
        mock_kb = MagicMock()
        mock_knowledge_service = MagicMock()
        mock_knowledge_service.get_dynamic_kb.return_value = mock_kb

        with (
            patch("agents.agent.api_settings") as mock_settings,
            patch("agents.agent.Agent") as mock_agent_class,
            patch("agents.agent.get_knowledge_service", return_value=mock_knowledge_service),
            patch("agents.agent.create_model"),
            patch("agents.agent.PostgresDb"),
            patch("agents.agent.MemoryManager"),
        ):
            # Mock the settings
            mock_settings.agent_debug_mode = False
            # Call get_agent without debug_mode parameter
            get_agent(
                prompt=mock_prompt,
                user_id="test_user",
                session_id="test_session",
                organizer_email="test@example.com",
                tenant_id="test_tenant",
            )

            # Verify Agent was instantiated with the environment setting
            mock_agent_class.assert_called_once()
            call_kwargs = mock_agent_class.call_args[1]
            self.assertFalse(call_kwargs["debug_mode"])

    def test_explicit_debug_mode_overrides_testing_setting(self):
        """Test that explicitly passing debug_mode overrides TESTING setting."""
        from unittest.mock import MagicMock, patch

        from agents.agent import get_agent

        # Mock settings to have debug mode disabled
        with patch("api.settings.api_settings") as mock_settings:
            mock_settings.agent_debug_mode = False

            mock_prompt = MagicMock()
            mock_prompt.name = "test-agent"
            mock_prompt.description = "Test Agent"
            mock_prompt.template = "Test template"

            # Mock knowledge service
            mock_kb = MagicMock()
            mock_knowledge_service = MagicMock()
            mock_knowledge_service.get_dynamic_kb.return_value = mock_kb

            with (
                patch("agents.agent.Agent") as mock_agent_class,
                patch("agents.agent.get_knowledge_service", return_value=mock_knowledge_service),
                patch("agents.agent.create_model"),
                patch("agents.agent.PostgresDb"),
                patch("agents.agent.MemoryManager"),
            ):
                # Call with explicit debug_mode=True
                get_agent(
                    prompt=mock_prompt,
                    user_id="test_user",
                    session_id="test_session",
                    organizer_email="test@example.com",
                    tenant_id="test_tenant",
                    debug_mode=True,
                )

                # Verify Agent was instantiated with explicit debug_mode=True
                mock_agent_class.assert_called_once()
                call_kwargs = mock_agent_class.call_args[1]
                self.assertTrue(call_kwargs["debug_mode"])


if __name__ == "__main__":
    unittest.main()
