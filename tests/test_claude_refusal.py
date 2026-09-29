"""A Claude safety refusal must surface as an error, never as an empty reply.

The 2026-09-29 pf-budget-classifier outage: every claude-sonnet-5-5 call billed input,
reported 0 output tokens and raised nothing, because agno's Claude adapter does not read
`stop_reason == "refusal"`. These tests pin the three places the refusal has to be
caught - the non-streaming parse, the streaming parse, and agno's error handler, which
would otherwise re-wrap it as a generic 502 - and one run through a real Agent stream to
prove the reason reaches the event the gateway forwards to its caller.
"""

import asyncio
import unittest
from types import SimpleNamespace

from anthropic.types import Message, TextBlock, Usage

from agents.claude_refusal import (
    REFUSAL_MARKER,
    ModelRefusalError,
    RefusalAwareClaude,
    refusal_of,
)
from agno.exceptions import ModelProviderError

MODEL_ID = "claude-sonnet-5-5"


def refusal_message(category="reasoning_extraction", explanation="The request asks for the model's reasoning."):
    return SimpleNamespace(
        stop_reason="refusal",
        stop_details=SimpleNamespace(type="refusal", category=category, explanation=explanation),
        content=[],
        usage=SimpleNamespace(input_tokens=33403, output_tokens=0),
    )


def normal_message(text="[]"):
    return Message(
        id="msg_1",
        type="message",
        role="assistant",
        model=MODEL_ID,
        content=[TextBlock(type="text", text=text)],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=2),
    )


def model():
    return RefusalAwareClaude(id=MODEL_ID, api_key="test-key", max_tokens=8192)


class RefusalOfTest(unittest.TestCase):
    def test_reads_category_and_explanation(self):
        self.assertEqual(("cyber", "why"), refusal_of(refusal_message("cyber", "why")))

    def test_none_for_other_stop_reasons(self):
        for reason in ("end_turn", "max_tokens", "tool_use", "pause_turn", None):
            with self.subTest(reason=reason):
                self.assertIsNone(refusal_of(SimpleNamespace(stop_reason=reason)))

    def test_missing_details_still_counts_as_refusal(self):
        # stop_details can be null; the stop reason alone is enough to know.
        self.assertEqual((None, None), refusal_of(SimpleNamespace(stop_reason="refusal", stop_details=None)))


class NonStreamingTest(unittest.TestCase):
    def test_refusal_raises_with_the_category(self):
        with self.assertRaises(ModelRefusalError) as caught:
            model()._parse_provider_response(refusal_message())
        err = caught.exception
        self.assertEqual("reasoning_extraction", err.category)
        self.assertIn(REFUSAL_MARKER, str(err))
        self.assertIn("category=reasoning_extraction", str(err))
        self.assertIn(MODEL_ID, str(err))
        self.assertEqual(422, err.status_code)

    def test_normal_response_still_parses(self):
        response = model()._parse_provider_response(normal_message("hello"))
        self.assertEqual("hello", response.content)


class StreamingTest(unittest.TestCase):
    def test_refusal_on_message_stop_raises(self):
        event = SimpleNamespace(type="message_stop", message=refusal_message())
        with self.assertRaises(ModelRefusalError):
            model()._parse_provider_response_delta(event)

    def test_normal_message_stop_does_not_raise(self):
        event = SimpleNamespace(type="message_stop", message=normal_message())
        model()._parse_provider_response_delta(event)

    def test_other_events_are_untouched(self):
        # Only message_stop carries the final stop reason; a delta must not be read as one.
        event = SimpleNamespace(type="content_block_delta", message=refusal_message())
        model()._parse_provider_response_delta(event)


class ErrorHandlerTest(unittest.TestCase):
    def test_refusal_passes_through_unwrapped(self):
        err = ModelRefusalError("bio", None, model_id=MODEL_ID)
        with self.assertRaises(ModelRefusalError) as caught:
            model()._handle_api_error(err)
        self.assertIs(err, caught.exception)

    def test_other_errors_are_still_wrapped(self):
        with self.assertRaises(ModelProviderError) as caught:
            model()._handle_api_error(RuntimeError("boom"))
        self.assertNotIsInstance(caught.exception, ModelRefusalError)


class NativeReasoningDetectionTest(unittest.TestCase):
    """The subclass must not change which reasoning path agno picks.

    agno detects a native-thinking Claude by `__class__.__name__ == "Claude"`. Under its
    own name RefusalAwareClaude failed that, so configuring `thinking` would silently
    route reasoning through agno's manual chain-of-thought - the call Sonnet 5.5 refuses.
    """

    def test_presents_as_claude(self):
        self.assertEqual("Claude", model().__class__.__name__)

    def test_agno_detects_native_thinking_when_configured(self):
        from unittest.mock import patch

        import agno.reasoning.anthropic as agno_anthropic

        thinking = RefusalAwareClaude(id=MODEL_ID, api_key="test-key", max_tokens=8192, thinking={"type": "adaptive"})
        # agno 3.x also asks the Anthropic API whether the model can think; None means
        # "unknown" and defers to the configuration. Keep the test offline.
        with patch.object(agno_anthropic, "_api_thinking_supported", return_value=None, create=True):
            self.assertTrue(agno_anthropic.is_anthropic_reasoning_model(thinking))

    def test_without_thinking_it_is_still_not_native(self):
        import agno.reasoning.anthropic as agno_anthropic

        self.assertFalse(agno_anthropic.is_anthropic_reasoning_model(model()))


class _FakeStream:
    """Stands in for AsyncAnthropic().messages.stream(...): yields message_stop only."""

    def __init__(self, final_message):
        self._events = [SimpleNamespace(type="message_stop", message=final_message)]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for event in self._events:
                yield event

        return gen()


class AgentStreamTest(unittest.TestCase):
    """Through a real agno Agent, the way the gateway runs it (arun, stream=True)."""

    def test_refusal_reaches_the_run_error_event(self):
        from agno.agent import Agent

        claude = model()
        fake_client = SimpleNamespace(
            messages=SimpleNamespace(stream=lambda **kwargs: _FakeStream(refusal_message())),
        )
        claude.get_async_client = lambda: fake_client

        agent = Agent(model=claude, telemetry=False)

        async def collect():
            return [event async for event in agent.arun("classify these", stream=True)]

        events = asyncio.run(collect())

        errors = [e for e in events if getattr(e, "event", None) == "RunError"]
        self.assertEqual(1, len(errors), [getattr(e, "event", None) for e in events])
        self.assertIn(REFUSAL_MARKER, errors[0].content)
        self.assertIn("category=reasoning_extraction", errors[0].content)


if __name__ == "__main__":
    unittest.main()
