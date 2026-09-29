"""
Basic tests to verify the test infrastructure works.
"""

import os
import unittest
from unittest.mock import patch

# Set environment variables before any imports
os.environ["TESTING"] = "true"
os.environ["OPENAI_API_KEY"] = "test-key"


class TestBasicFunctionality(unittest.TestCase):
    """Test basic functionality without full app startup."""

    def test_environment_setup(self):
        """Test that test environment is properly configured."""
        self.assertEqual(os.environ.get("TESTING"), "true")
        self.assertEqual(os.environ.get("OPENAI_API_KEY"), "test-key")

    def test_database_models_import(self):
        """Test that database models can be imported."""
        from db.db_models import AgentInfoDB, TeamInfoDB, TokenUsage

        # Verify classes exist
        self.assertTrue(hasattr(AgentInfoDB, "__tablename__"))
        self.assertTrue(hasattr(TeamInfoDB, "__tablename__"))
        self.assertTrue(hasattr(TokenUsage, "__tablename__"))
        self.assertEqual(AgentInfoDB.__tablename__, "agent_info")
        self.assertEqual(TeamInfoDB.__tablename__, "team_info")

    def test_settings_import(self):
        """Test that settings can be imported."""
        from api.settings import api_settings

        self.assertIsNotNone(api_settings)
        self.assertEqual(api_settings.title, "hivegate")

    @patch("agents.agent_utils.retrieve_prompts")
    def test_agent_utils_import(self, mock_retrieve):
        """Test that agent utilities can be imported."""
        mock_retrieve.return_value = {"test-agent": {"template": "test"}}

        from agents.agent_utils import retrieve_prompts

        result = retrieve_prompts()
        self.assertIn("test-agent", result)

    def test_crud_operations_import(self):
        """Test that CRUD operations can be imported."""
        from db.agent_info_crud import create_agent_info, get_agent_info
        from db.team_info_crud import create_team_info, get_team_info

        # Just verify functions exist
        self.assertTrue(callable(create_agent_info))
        self.assertTrue(callable(get_agent_info))
        self.assertTrue(callable(create_team_info))
        self.assertTrue(callable(get_team_info))


if __name__ == "__main__":
    unittest.main()
