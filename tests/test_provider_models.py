"""The retry policy's two guards: no stream replay after output, no billing retries."""

import asyncio
import unittest
from unittest.mock import patch

from agno.exceptions import ModelProviderError
from agno.models.google import Gemini

from agents.model_factory import create_model
from agents.provider_models import GuardedGemini, StreamInterruptedError, is_billing_error


def _model() -> GuardedGemini:
    return GuardedGemini(id="gemini-3.8-flash", api_key="k", retries=2, delay_between_retries=0)


def _error(status: int = 503, message: str = "This model is currently experiencing high demand") -> ModelProviderError:
    return ModelProviderError(message=message, status_code=status, model_name="Gemini", model_id="gemini-3.8-flash")


class TestStreamIsNotReplayed(unittest.TestCase):
    def test_error_after_output_is_raised_not_retried(self):
        """agno restarts the whole stream on retry, which would send the first chunk twice."""
        calls = []

        def stream(self, **kwargs):
            calls.append(1)
            yield "chunk-1"
            raise _error()

        with patch.object(Gemini, "invoke_stream", stream):
            received = []
            with self.assertRaises(StreamInterruptedError):
                for chunk in _model()._invoke_stream_with_retry(messages=[]):
                    received.append(chunk)

        self.assertEqual(received, ["chunk-1"], "a chunk was sent twice")
        self.assertEqual(len(calls), 1)

    def test_error_before_output_is_still_retried(self):
        calls = []

        def stream(self, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise _error()
            yield "chunk-1"

        with patch.object(Gemini, "invoke_stream", stream):
            received = list(_model()._invoke_stream_with_retry(messages=[]))

        self.assertEqual(received, ["chunk-1"])
        self.assertEqual(len(calls), 2)

    def test_async_error_after_output_is_raised_not_retried(self):
        calls = []

        async def stream(self, **kwargs):
            calls.append(1)
            yield "chunk-1"
            raise _error()

        async def run():
            received = []
            with self.assertRaises(StreamInterruptedError):
                async for chunk in _model()._ainvoke_stream_with_retry(messages=[]):
                    received.append(chunk)
            return received

        with patch.object(Gemini, "ainvoke_stream", stream):
            self.assertEqual(asyncio.run(run()), ["chunk-1"])
        self.assertEqual(len(calls), 1)

    def test_a_non_retryable_error_mid_stream_keeps_its_own_type(self):
        """A refusal or bad request after output is not relabelled as an interruption."""

        def stream(self, **kwargs):
            yield "chunk-1"
            raise _error(status=422, message="refused")

        with patch.object(Gemini, "invoke_stream", stream):
            with self.assertRaises(ModelProviderError) as ctx:
                list(_model()._invoke_stream_with_retry(messages=[]))
        self.assertNotIsInstance(ctx.exception, StreamInterruptedError)
        self.assertEqual(ctx.exception.status_code, 422)


class TestBillingErrorsAreNotRetried(unittest.TestCase):
    def test_billing_shapes_seen_on_2026_09_29(self):
        self.assertTrue(is_billing_error(_error(402, "Insufficient Balance")))  # DeepSeek
        self.assertTrue(
            is_billing_error(_error(429, "Insufficient balance or no resource package. Please recharge."))
        )  # Z.ai
        self.assertTrue(is_billing_error(_error(403, "Your team has either used all available credits")))  # xAI
        self.assertFalse(is_billing_error(_error(429, "Rate limit exceeded")))
        self.assertFalse(is_billing_error(_error(503)))

    def test_a_billing_error_is_tried_once(self):
        calls = []

        def stream(self, **kwargs):
            calls.append(1)
            raise _error(402, "Insufficient Balance")
            yield  # pragma: no cover - makes this a generator

        with patch.object(Gemini, "invoke_stream", stream):
            with self.assertRaises(ModelProviderError):
                list(_model()._invoke_stream_with_retry(messages=[]))
        self.assertEqual(len(calls), 1)

    def test_a_rate_limit_is_still_retried(self):
        self.assertTrue(_model()._is_retryable_error(_error(429, "Rate limit exceeded")))


class TestClassNamesAreKept(unittest.TestCase):
    """agno picks native reasoning by class name; the wrappers must not change it."""

    def test_every_built_model_presents_its_agno_name(self):
        keys = dict(
            openai_api_key="k",
            gemini_api_key="k",
            anthropic_api_key="k",
            xai_api_key="k",
            zai_api_key="k",
            deepseek_api_key="k",
        )
        expected = {
            "gpt-6-luna": "OpenAIResponses",
            "gemini-3.8-flash": "Gemini",
            "claude-haiku-4-5-20251001": "Claude",
            "grok-4.7": "xAI",
            "glm-5.3": "OpenAILike",
            "deepseek-flash": "DeepSeek",
        }
        for model_id, name in expected.items():
            with self.subTest(model=model_id):
                built = create_model(model_id, **keys)
                self.assertEqual(type(built).__name__, name)
                self.assertTrue(hasattr(built, "_interrupted"), "not wrapped in StreamRestartGuard")

    def test_openai_does_not_store_conversations(self):
        self.assertIs(create_model("gpt-6-luna", openai_api_key="k").store, False)


if __name__ == "__main__":
    unittest.main()
