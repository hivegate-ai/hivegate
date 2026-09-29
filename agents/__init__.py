import os
from enum import Enum
from typing import Union


class Model(str, Enum):
    # ===== OpenAI Models (GPT-5.4 series) =====
    gpt_5_4 = "gpt-5.4"
    gpt_5_4_mini = "gpt-5.4-mini"
    gpt_5_4_nano = "gpt-5.4-nano"

    # ===== OpenAI Models (GPT-5.6 series) =====
    # These are the tier fallbacks named in model_resolver.TIERS. Until now none of
    # them was a member here, so a tier whose vendor lookup failed resolved to a
    # value this enum rejects - see the test in tests/test_model_enum.py.
    gpt_5_6_luna = "gpt-5.6-luna"
    gpt_5_6_terra = "gpt-5.6-terra"
    gpt_5_6_sol = "gpt-5.6-sol"

    # ===== OpenAI Models (GPT-6 series, current flagship) =====
    # luna $0.1/$0.5, sol $2/$10, astra $10/$50 per MTok. There is no gpt-6-terra, so
    # openai:terra-latest keeps resolving to gpt-5.6-terra. Built on the Responses API
    # (agents/openai_responses.py): on Chat Completions GPT-6 calls tools only with
    # reasoning off.
    gpt_6_luna = "gpt-6-luna"
    gpt_6_sol = "gpt-6-sol"
    gpt_6_astra = "gpt-6-astra"

    # ===== Google Gemini Models (stable, 3.x) =====
    gemini_3_8_flash = "gemini-3.8-flash"
    gemini_3_7_flash = "gemini-3.7-flash"
    gemini_3_6_flash = "gemini-3.6-flash"
    gemini_3_5_flash = "gemini-3.5-flash"
    gemini_3_5_flash_lite = "gemini-3.5-flash-lite"
    gemini_3_1_flash_lite = "gemini-3.1-flash-lite"

    # ===== Google Gemini Models (2.5; Google now serves these to existing users only) =====
    gemini_2_5_pro = "gemini-2.5-pro"
    gemini_2_5_flash = "gemini-2.5-flash"
    gemini_2_5_flash_lite = "gemini-2.5-flash-lite"

    # ===== Google Gemini Models (preview) =====
    gemini_3_1_pro = "gemini-3.1-pro-preview"
    gemini_3_flash = "gemini-3-flash-preview"

    # ===== Anthropic Claude Models =====
    # Every id here is a pinned snapshot, including the dateless ones - from the 4.6
    # generation on, the dateless id IS the snapshot, not a moving pointer. Only the
    # anthropic:*-latest entries below move.
    #
    # The absence of the 5 and 5.5 generations is what made this enum a trap: a
    # caller could not pin the model the `anthropic:sonnet-latest` alias was already
    # resolving to and running, because a concrete id is validated against this enum
    # while the alias is not. On 2026-09-29 the alias moved to claude-sonnet-5-5 and
    # broke, and the newest Sonnet that could be pinned in its place was 4.6 - a
    # legacy model at $3/$15 per MTok against the current $2/$10.
    #
    # Current lineup (Anthropic's model overview):
    claude_fable_5_1 = "claude-fable-5-1"
    claude_opus_5_5 = "claude-opus-5-5"
    # The current Sonnet. It is also the one that returns an empty reasoning
    # response through agno's manual chain-of-thought path - the 2026-09-29 outage.
    # That is a bug to fix on this side, not a reason to leave it unpinnable.
    claude_sonnet_5_5 = "claude-sonnet-5-5"
    claude_haiku_4_5 = "claude-haiku-4-5-20251001"
    # The dateless alias for Haiku 4.5, which is what model_resolver's haiku tier
    # falls back to. Kept alongside the dated member rather than replacing it: the
    # dated string is what callers pin in production, and changing what
    # claude_haiku_4_5 evaluates to would move them silently.
    claude_haiku_4_5_undated = "claude-haiku-4-5"
    # Legacy, still available and still servable - so still pinnable:
    claude_fable_5 = "claude-fable-5"
    claude_opus_5 = "claude-opus-5"
    claude_opus_4_8 = "claude-opus-4-8"
    claude_opus_4_7 = "claude-opus-4-7"
    claude_opus_4_6 = "claude-opus-4-6"
    claude_opus_4_5 = "claude-opus-4-5"
    claude_sonnet_5 = "claude-sonnet-5"
    claude_sonnet_4_6 = "claude-sonnet-4-6"
    claude_sonnet_4_5 = "claude-sonnet-4-5"

    # ===== xAI Grok Models =====
    grok_4_7 = "grok-4.7"
    grok_4_6 = "grok-4.6"
    grok_4_5 = "grok-4.5"
    grok_4_3 = "grok-4.3"
    grok_4_20_reasoning = "grok-4.20-0309-reasoning"
    grok_4_20_non_reasoning = "grok-4.20-0309-non-reasoning"

    # ===== Z.ai GLM Models =====
    # Served through Z.ai's OpenAI-compatible endpoint (agno has no Z.ai class). No
    # latest-of-a-tier alias: Z.ai documents no model-listing endpoint to resolve one.
    glm_5_3 = "glm-5.3"
    glm_5_3_flashx = "glm-5.3-flashx"
    glm_5_3_flash = "glm-5.3-flash"
    glm_5_2 = "glm-5.2"
    glm_5_1 = "glm-5.1"
    glm_5 = "glm-5"
    glm_4_7 = "glm-4.7"
    glm_4_7_flash = "glm-4.7-flash"

    # ===== Latest of a tier (resolved per vendor, see agents/model_resolver.py) =====
    # Follow the vendor's newest model in a tier without a code change, never jumping
    # to a pricier tier. Prefer these over the pinned ids above for defaults.
    anthropic_haiku_latest = "anthropic:haiku-latest"
    anthropic_sonnet_latest = "anthropic:sonnet-latest"
    anthropic_opus_latest = "anthropic:opus-latest"
    openai_luna_latest = "openai:luna-latest"
    openai_terra_latest = "openai:terra-latest"
    openai_sol_latest = "openai:sol-latest"
    openai_astra_latest = "openai:astra-latest"
    google_flash_lite_latest = "google:flash-lite-latest"
    google_flash_latest = "google:flash-latest"
    google_pro_latest = "google:pro-latest"
    xai_grok_latest = "xai:grok-latest"


class ModelProvider(str, Enum):
    OPENAI = "openai"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    XAI = "xai"
    ZAI = "zai"


def get_provider(model: Union[Model, str]) -> ModelProvider:
    """Determine provider from a Model enum member, a tier alias, or a vendor model id."""
    model_value = model.value if isinstance(model, Model) else str(model)
    if model_value.startswith(("gpt-", "openai:")):
        return ModelProvider.OPENAI
    elif model_value.startswith(("gemini-", "google:")):
        return ModelProvider.GEMINI
    elif model_value.startswith(("claude-", "anthropic:")):
        return ModelProvider.ANTHROPIC
    elif model_value.startswith(("grok-", "xai:")):
        return ModelProvider.XAI
    elif model_value.startswith("glm-"):
        return ModelProvider.ZAI
    raise ValueError(f"Unknown provider for: {model_value}")


def _default_model() -> Model:
    """Model used when a chat request omits `model`.

    Set via the DEFAULT_CHAT_MODEL env var so the deployed default is visible in (and
    changeable from) the deployment config without a code release. Falls back to
    gemini-3-flash-preview: gemini-2.5-pro was retired by Google (404 NOT_FOUND) and
    gemini-3.1-pro-preview needs pro-tier quota (429 where not provisioned). An unknown
    value fails fast at startup rather than 422-ing every chat that omits `model`.
    """
    raw = os.getenv("DEFAULT_CHAT_MODEL", "").strip()
    if not raw:
        return Model.gemini_3_flash
    try:
        return Model(raw)
    except ValueError:
        valid = ", ".join(m.value for m in Model)
        raise ValueError(f"DEFAULT_CHAT_MODEL={raw!r} is not a known model; expected one of: {valid}") from None


DEFAULT_MODEL = _default_model()

__all__ = ["DEFAULT_MODEL", "Model", "ModelProvider", "get_provider"]
