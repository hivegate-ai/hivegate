import unittest
from unittest.mock import MagicMock, patch

from agents import Model
from agents.model_factory import create_model


class TestCreateModel(unittest.TestCase):
    """Test cases for the create_model() factory function."""

    @patch("agents.model_factory.Gemini")
    def test_create_gemini_model(self, mock_gemini_class):
        mock_gemini_class.return_value = MagicMock()
        result = create_model(Model.gemini_2_5_pro, gemini_api_key="test-key")

        mock_gemini_class.assert_called_once_with(id="gemini-2.5-pro", api_key="test-key")
        self.assertEqual(result, mock_gemini_class.return_value)

    @patch("agents.model_factory.Gemini")
    def test_create_gemini_with_max_tokens_uses_max_output_tokens(self, mock_gemini_class):
        mock_gemini_class.return_value = MagicMock()
        create_model(Model.gemini_2_5_flash, gemini_api_key="test-key", max_tokens=1000)

        call_kwargs = mock_gemini_class.call_args[1]
        self.assertIn("max_output_tokens", call_kwargs)
        self.assertNotIn("max_tokens", call_kwargs)
        self.assertEqual(call_kwargs["max_output_tokens"], 1000)

    def test_create_openai_model_uses_the_responses_api(self):
        """Real class, no mock: GPT-6 calls tools on Chat Completions only with reasoning
        off, so a factory that drifted back to OpenAIChat must fail here."""
        from agno.models.openai import OpenAIChat

        from agents.openai_responses import ReasoningAwareOpenAIResponses

        result = create_model(Model.gpt_6_sol, openai_api_key="test-key")

        self.assertIsInstance(result, ReasoningAwareOpenAIResponses)
        self.assertNotIsInstance(result, OpenAIChat)
        self.assertEqual(result.id, "gpt-6-sol")

    def test_create_openai_with_max_tokens_uses_max_output_tokens(self):
        result = create_model(Model.gpt_5_4_mini, openai_api_key="test-key", max_tokens=2000)
        self.assertEqual(result.max_output_tokens, 2000)

    def test_gpt6_is_treated_as_a_reasoning_model(self):
        """agno 3.0.11 recognises only gpt-5/o3/o4-mini; without this a GPT-6 tool
        round-trip goes back without the reasoning item that produced the call."""
        for model in [Model.gpt_6_luna, Model.gpt_6_sol, Model.gpt_6_astra, Model.gpt_5_6_terra]:
            with self.subTest(model=model):
                self.assertTrue(create_model(model, openai_api_key="k")._using_reasoning_model())

    def test_create_xai_model(self):
        from agno.models.xai import xAI

        result = create_model(Model.grok_4_7, xai_api_key="test-key", max_tokens=321)

        self.assertIsInstance(result, xAI)
        self.assertEqual(result.id, "grok-4.7")
        self.assertEqual(result.base_url, "https://api.x.ai/v1")
        self.assertEqual(result.max_tokens, 321)

    def test_create_zai_model_points_at_zais_openai_compatible_endpoint(self):
        from agno.models.openai.like import OpenAILike

        result = create_model(Model.glm_5_3, zai_api_key="test-key", max_tokens=321)

        self.assertIsInstance(result, OpenAILike)
        self.assertEqual(result.id, "glm-5.3")
        self.assertEqual(result.base_url, "https://api.z.ai/api/paas/v4/")
        self.assertEqual(result.api_key, "test-key")
        self.assertEqual(result.max_tokens, 321)

    def test_missing_zai_api_key_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_model(Model.glm_5_3)
        self.assertIn("Z.ai", str(ctx.exception))

    def test_create_deepseek_model(self):
        from agno.models.deepseek import DeepSeek

        result = create_model(Model.deepseek_v4_pro, deepseek_api_key="test-key", max_tokens=321)

        self.assertIsInstance(result, DeepSeek)
        self.assertEqual(result.id, "deepseek-v4-pro")
        self.assertEqual(result.base_url, "https://api.deepseek.com")
        self.assertEqual(result.max_tokens, 321)

    def test_missing_deepseek_api_key_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_model(Model.deepseek_flash)
        self.assertIn("DeepSeek", str(ctx.exception))

    def test_missing_key_is_a_provider_not_configured_error(self):
        from agents.model_factory import ProviderNotConfiguredError

        for model in [
            Model.gpt_6_luna,
            Model.gemini_3_8_flash,
            Model.claude_haiku_4_5,
            Model.grok_4_7,
            Model.glm_5_3,
            Model.deepseek_flash,
        ]:
            with self.subTest(model=model), self.assertRaises(ProviderNotConfiguredError):
                create_model(model)

    def test_missing_xai_api_key_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_model(Model.grok_4_7)
        self.assertIn("xAI", str(ctx.exception))

    def test_create_anthropic_model(self):
        """Claude ids get the refusal-aware subclass, not agno's bare Claude.

        Constructing the model makes no network call, so this uses the real class
        rather than mocking it out - a mock here would keep passing if the factory
        quietly went back to returning plain Claude, which silently swallows refusals.
        """
        from agents.claude_refusal import RefusalAwareClaude

        result = create_model(Model.claude_sonnet_4_6, anthropic_api_key="test-key", max_tokens=1234)

        self.assertIsInstance(result, RefusalAwareClaude)
        self.assertEqual(result.id, "claude-sonnet-4-6")
        self.assertEqual(result.max_tokens, 1234)

    def test_missing_gemini_api_key_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_model(Model.gemini_2_5_pro)
        self.assertIn("Gemini", str(ctx.exception))

    def test_missing_openai_api_key_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_model(Model.gpt_5_4)
        self.assertIn("OpenAI", str(ctx.exception))

    def test_missing_anthropic_api_key_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_model(Model.claude_opus_4_6)
        self.assertIn("Anthropic", str(ctx.exception))

    @patch("agents.model_factory.Gemini")
    def test_temperature_passthrough(self, mock_gemini_class):
        mock_gemini_class.return_value = MagicMock()
        create_model(Model.gemini_2_5_pro, gemini_api_key="test-key", temperature=0.7)

        call_kwargs = mock_gemini_class.call_args[1]
        self.assertEqual(call_kwargs["temperature"], 0.7)

    @patch("agents.model_factory.Gemini")
    def test_no_optional_params_when_none(self, mock_gemini_class):
        mock_gemini_class.return_value = MagicMock()
        create_model(Model.gemini_2_5_pro, gemini_api_key="test-key")

        call_kwargs = mock_gemini_class.call_args[1]
        self.assertNotIn("temperature", call_kwargs)
        self.assertNotIn("max_output_tokens", call_kwargs)

    @patch("agents.model_factory.Gemini")
    def test_string_to_enum_conversion(self, mock_gemini_class):
        mock_gemini_class.return_value = MagicMock()
        create_model("gemini-2.5-pro", gemini_api_key="test-key")

        mock_gemini_class.assert_called_once_with(id="gemini-2.5-pro", api_key="test-key")

    def test_invalid_model_string_raises(self):
        with self.assertRaises(ValueError):
            create_model("nonexistent-model", gemini_api_key="test-key")

    @patch("agents.model_factory.ReasoningAwareOpenAIResponses")
    def test_all_openai_models_create_openai_responses(self, mock_openai_class):
        mock_openai_class.return_value = MagicMock()
        openai_models = [m for m in Model.__members__.values() if m.value.startswith("gpt-")]
        for model in openai_models:
            with self.subTest(model=model):
                mock_openai_class.reset_mock()
                create_model(model, openai_api_key="test-key")
                mock_openai_class.assert_called_once()

    @patch("agents.model_factory.Gemini")
    def test_all_gemini_models_create_gemini(self, mock_gemini_class):
        mock_gemini_class.return_value = MagicMock()
        gemini_models = [m for m in Model.__members__.values() if m.value.startswith("gemini-")]
        for model in gemini_models:
            with self.subTest(model=model):
                mock_gemini_class.reset_mock()
                create_model(model, gemini_api_key="test-key")
                mock_gemini_class.assert_called_once()


if __name__ == "__main__":
    unittest.main()
