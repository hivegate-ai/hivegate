"""Latest-of-a-tier model aliases: picking the newest model, caching, fallbacks, wiring."""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from agents import Model, ModelProvider, get_provider
from agents.model_factory import PROVIDER_RETRY, create_model
from agents.model_resolver import (
    RESOLVE_TTL_SECONDS,
    RETRY_AFTER_SECONDS,
    TIERS,
    ModelResolver,
    pick_anthropic,
    pick_google,
    pick_openai,
    pick_xai,
)

ANTHROPIC_LISTING = [
    ("claude-sonnet-4-6", 100.0),
    ("claude-sonnet-5", 300.0),
    ("claude-opus-4-8", 200.0),
    ("claude-opus-5", 310.0),
    ("claude-fable-5-1", 400.0),  # a pricier tier - never picked for sonnet/opus
    ("claude-haiku-4-5-20251001", 150.0),
]

OPENAI_LISTING = [
    ("gpt-5.4-nano", 100.0),
    ("gpt-5.6-luna", 200.0),
    ("gpt-5.6-luna-2026-07-01", 250.0),  # dated snapshot - excluded
    ("gpt-5.6-terra", 200.0),
    ("gpt-5.6-cyber", 260.0),  # a variant, not a tier
    ("gpt-5.9-sol", 300.0),
    ("gpt-5.10-sol", 290.0),  # 5.10 > 5.9 even though listed as older
]

# The live shape on 2026-09-29: GPT-6 ships luna/sol/astra but no terra.
OPENAI_LISTING_GPT6 = OPENAI_LISTING + [
    ("gpt-6-luna", 400.0),
    ("gpt-6-sol", 400.0),
    ("gpt-6-astra", 400.0),
]

XAI_LISTING = [
    ("grok-4.3", 100.0),
    ("grok-4.7", 300.0),
    ("grok-4.20-0309-reasoning", 350.0),  # dated snapshot - excluded (and 4.20 > 4.7)
    ("grok-4.20-multi-agent-0309", 350.0),
    ("grok-build-0.1", 360.0),
    ("grok-4.6", 250.0),
]

GOOGLE_LISTING = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",  # lite variant - excluded
    "gemini-3-flash-preview",
    "gemini-3-flash",  # stable wins over preview at the same version
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash-preview-05-20",  # dated preview - excluded
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro",
    "text-embedding-004",
]


class TestPickNewestOfTier(unittest.TestCase):
    def test_anthropic_newest_within_the_tier(self):
        self.assertEqual(pick_anthropic(ANTHROPIC_LISTING, "sonnet"), "claude-sonnet-5")
        self.assertEqual(pick_anthropic(ANTHROPIC_LISTING, "opus"), "claude-opus-5")
        self.assertEqual(pick_anthropic(ANTHROPIC_LISTING, "haiku"), "claude-haiku-4-5-20251001")

    def test_anthropic_no_match(self):
        self.assertIsNone(pick_anthropic([("claude-opus-5", 1.0)], "sonnet"))

    def test_openai_highest_version_of_the_tier(self):
        self.assertEqual(pick_openai(OPENAI_LISTING, "luna"), "gpt-5.6-luna")
        self.assertEqual(pick_openai(OPENAI_LISTING, "terra"), "gpt-5.6-terra")
        self.assertEqual(pick_openai(OPENAI_LISTING, "sol"), "gpt-5.10-sol")

    def test_openai_gpt6_moves_the_tiers_it_has_and_leaves_terra(self):
        self.assertEqual(pick_openai(OPENAI_LISTING_GPT6, "luna"), "gpt-6-luna")
        self.assertEqual(pick_openai(OPENAI_LISTING_GPT6, "sol"), "gpt-6-sol")
        self.assertEqual(pick_openai(OPENAI_LISTING_GPT6, "astra"), "gpt-6-astra")
        self.assertEqual(pick_openai(OPENAI_LISTING_GPT6, "terra"), "gpt-5.6-terra")

    def test_openai_no_match(self):
        self.assertIsNone(pick_openai([("gpt-5.4-nano", 1.0)], "luna"))

    def test_google_highest_version_stable_first(self):
        self.assertEqual(pick_google(GOOGLE_LISTING, "flash"), "gemini-3-flash")
        self.assertEqual(pick_google(GOOGLE_LISTING, "pro"), "gemini-3.1-pro-preview")

    def test_google_flash_and_flash_lite_are_separate_tiers(self):
        listing = GOOGLE_LISTING + ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.9-flash-lite-preview"]
        self.assertEqual(pick_google(listing, "flash"), "gemini-3.8-flash")
        self.assertEqual(pick_google(listing, "flash-lite"), "gemini-3.9-flash-lite-preview")
        self.assertEqual(pick_google(GOOGLE_LISTING, "flash-lite"), "gemini-3.1-flash-lite-preview")

    def test_xai_highest_plain_grok_version(self):
        self.assertEqual(pick_xai(XAI_LISTING, "grok"), "grok-4.7")

    def test_xai_no_match(self):
        self.assertIsNone(pick_xai([("grok-imagine-image", 1.0)], "grok"))

    def test_google_no_match(self):
        self.assertIsNone(pick_google(["text-embedding-004"], "flash"))


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestModelResolver(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.ask = MagicMock(return_value="claude-sonnet-5")
        self.resolver = ModelResolver(ask_vendor=self.ask, clock=self.clock)

    def test_a_concrete_id_passes_through_without_asking(self):
        self.assertEqual(self.resolver.resolve("claude-sonnet-4-6"), "claude-sonnet-4-6")
        self.ask.assert_not_called()

    def test_an_alias_is_resolved_and_cached(self):
        self.assertEqual(self.resolver.resolve("anthropic:sonnet-latest"), "claude-sonnet-5")
        self.assertEqual(self.resolver.resolve("anthropic:sonnet-latest"), "claude-sonnet-5")
        self.ask.assert_called_once_with(TIERS["anthropic:sonnet-latest"])

    def test_the_vendor_is_asked_again_after_the_ttl(self):
        self.resolver.resolve("anthropic:sonnet-latest")
        self.ask.return_value = "claude-sonnet-6"
        self.clock.now += RESOLVE_TTL_SECONDS + 1

        self.assertEqual(self.resolver.resolve("anthropic:sonnet-latest"), "claude-sonnet-6")
        self.assertEqual(self.ask.call_count, 2)

    def test_when_the_vendor_cannot_be_asked_the_tier_fallback_is_used(self):
        self.ask.side_effect = LookupError("no anthropic API key configured")

        self.assertEqual(self.resolver.resolve("anthropic:sonnet-latest"), TIERS["anthropic:sonnet-latest"].fallback)

    def test_no_matching_model_uses_the_fallback(self):
        self.ask.return_value = None
        self.assertEqual(self.resolver.resolve("google:pro-latest"), TIERS["google:pro-latest"].fallback)

    def test_a_failure_is_retried_after_a_short_wait_not_the_full_ttl(self):
        self.ask.side_effect = ConnectionError("down")
        self.resolver.resolve("anthropic:sonnet-latest")
        self.resolver.resolve("anthropic:sonnet-latest")
        self.assertEqual(self.ask.call_count, 1)  # not hammered while down

        self.ask.side_effect = None
        self.clock.now += RETRY_AFTER_SECONDS + 1
        self.assertEqual(self.resolver.resolve("anthropic:sonnet-latest"), "claude-sonnet-5")

    def test_a_failure_after_a_success_keeps_the_last_known_model(self):
        self.resolver.resolve("anthropic:sonnet-latest")
        self.ask.side_effect = ConnectionError("down")
        self.clock.now += RESOLVE_TTL_SECONDS + 1

        self.assertEqual(self.resolver.resolve("anthropic:sonnet-latest"), "claude-sonnet-5")

    def test_the_log_names_the_tier_not_the_callers_string(self):
        with self.assertLogs("agents.model_resolver", level="INFO") as logs:
            self.resolver.resolve("anthropic:sonnet-latest")
        self.assertIn("Model alias anthropic:sonnet-latest -> claude-sonnet-5", logs.output[0])

    def test_every_tier_alias_matches_its_key(self):
        for alias, tier in TIERS.items():
            self.assertEqual(tier.alias, alias)

    def test_every_alias_is_a_model_and_every_fallback_is_a_concrete_id_of_its_vendor(self):
        prefixes = {"anthropic": "claude-", "openai": "gpt-", "google": "gemini-", "xai": "grok-"}
        for alias, tier in TIERS.items():
            self.assertEqual(Model(alias).value, alias)
            self.assertTrue(tier.fallback.startswith(prefixes[tier.vendor]), tier.fallback)


class TestAliasesAreWiredIn(unittest.TestCase):
    def test_provider_of_an_alias(self):
        self.assertEqual(get_provider(Model.anthropic_sonnet_latest), ModelProvider.ANTHROPIC)
        self.assertEqual(get_provider("openai:terra-latest"), ModelProvider.OPENAI)
        self.assertEqual(get_provider("google:flash-latest"), ModelProvider.GEMINI)
        self.assertEqual(get_provider("xai:grok-latest"), ModelProvider.XAI)

    @patch("agents.model_factory.resolve_model_id", return_value="claude-sonnet-5")
    def test_create_model_builds_the_resolved_model(self, _resolve):
        # The factory builds RefusalAwareClaude (agents/claude_refusal.py), not agno's
        # bare Claude, so that is the constructor that must receive the resolved id.
        claude = MagicMock()
        with patch("agents.claude_refusal.RefusalAwareClaude", claude):
            create_model(Model.anthropic_sonnet_latest, anthropic_api_key="k")
        claude.assert_called_once_with(id="claude-sonnet-5", api_key="k", **PROVIDER_RETRY)

    @patch("agents.model_factory.ReasoningAwareOpenAIResponses")
    def test_create_model_accepts_a_resolved_id_not_in_the_enum(self, openai_responses):
        create_model("gpt-6-terra", openai_api_key="k")
        openai_responses.assert_called_once_with(id="gpt-6-terra", api_key="k", **PROVIDER_RETRY)

    def test_default_chat_model_accepts_an_alias(self):
        from agents import _default_model

        with patch.dict("os.environ", {"DEFAULT_CHAT_MODEL": "anthropic:sonnet-latest"}):
            self.assertEqual(_default_model(), Model.anthropic_sonnet_latest)

    @patch("api.routes.v2.agents.resolve_model_id", side_effect=["claude-sonnet-5", "claude-sonnet-6"])
    def test_the_agent_cache_key_follows_the_resolved_model(self, _resolve):
        from api.routes.v2.agents import compute_cache_key

        before = compute_cache_key("tmpl", "a", Model.anthropic_sonnet_latest, "u", "s")
        after = compute_cache_key("tmpl", "a", Model.anthropic_sonnet_latest, "u", "s")
        self.assertNotEqual(before, after)
        self.assertIn("claude-sonnet-5", before)


class TestVendorListingsGoThroughTheSdks(unittest.TestCase):
    """The four list calls, with each SDK's client patched at its import."""

    def test_anthropic(self):
        from agents import model_resolver

        created = datetime(2026, 5, 1, tzinfo=timezone.utc)
        client = MagicMock()
        client.models.list.return_value = [MagicMock(id="claude-sonnet-5", created_at=created)]
        with patch("anthropic.Anthropic", return_value=client):
            self.assertEqual(model_resolver._list_anthropic("k"), [("claude-sonnet-5", created.timestamp())])

    def test_openai(self):
        from agents import model_resolver

        client = MagicMock()
        client.models.list.return_value = [MagicMock(id="gpt-5.6-terra", created=123)]
        with patch("openai.OpenAI", return_value=client):
            self.assertEqual(model_resolver._list_openai("k"), [("gpt-5.6-terra", 123.0)])

    def test_xai_lists_through_the_openai_sdk_at_xais_base_url(self):
        from agents import model_resolver

        client = MagicMock()
        client.models.list.return_value = [MagicMock(id="grok-4.7", created=456)]
        with patch("openai.OpenAI", return_value=client) as ctor:
            self.assertEqual(model_resolver._list_xai("k"), [("grok-4.7", 456.0)])
        self.assertEqual(ctor.call_args.kwargs["base_url"], "https://api.x.ai/v1")

    def test_google_strips_the_models_prefix(self):
        from agents import model_resolver

        client = MagicMock()
        client.models.list.return_value = [MagicMock(name="x")]
        client.models.list.return_value[0].name = "models/gemini-3-flash"
        with patch("google.genai.Client", return_value=client):
            self.assertEqual(model_resolver._list_google("k"), ["gemini-3-flash"])


if __name__ == "__main__":
    unittest.main()
