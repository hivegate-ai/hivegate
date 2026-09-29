"""Resolve "latest of a tier" model aliases to a concrete vendor model id.

An alias such as ``anthropic:sonnet-latest`` names a vendor and a tier instead of a
specific model, so an agent follows the vendor's newest model in that tier without a
code change - and never jumps to a pricier tier. The concrete id is found by listing
the vendor's models through its own SDK and taking the newest one whose id matches the
tier. The answer is cached for ``RESOLVE_TTL_SECONDS``; if the vendor can't be asked
(no key, network error, nothing matches) a known model for the tier is used and the
lookup is retried after ``RETRY_AFTER_SECONDS``.

Concrete model ids pass through unchanged, so callers can resolve unconditionally.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

RESOLVE_TTL_SECONDS = 24 * 3600
RETRY_AFTER_SECONDS = 300
VENDOR_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Tier:
    vendor: str  # "anthropic" | "openai" | "google" | "xai"
    name: str
    fallback: str  # used when the vendor can't be asked

    @property
    def alias(self) -> str:
        return f"{self.vendor}:{self.name}-latest"


# alias -> tier. The alias is the Model enum value callers send.
TIERS: Dict[str, Tier] = {
    "anthropic:haiku-latest": Tier("anthropic", "haiku", "claude-haiku-4-5"),
    "anthropic:sonnet-latest": Tier("anthropic", "sonnet", "claude-sonnet-5"),
    "anthropic:opus-latest": Tier("anthropic", "opus", "claude-opus-5"),
    "openai:luna-latest": Tier("openai", "luna", "gpt-6-luna"),
    # GPT-6 has no terra, so this tier stays on 5.6 until OpenAI ships one.
    "openai:terra-latest": Tier("openai", "terra", "gpt-5.6-terra"),
    "openai:sol-latest": Tier("openai", "sol", "gpt-6-sol"),
    "openai:astra-latest": Tier("openai", "astra", "gpt-6-astra"),
    "google:flash-lite-latest": Tier("google", "flash-lite", "gemini-3.5-flash-lite"),
    "google:flash-latest": Tier("google", "flash", "gemini-3.8-flash"),
    "google:pro-latest": Tier("google", "pro", "gemini-3.1-pro-preview"),
    "xai:grok-latest": Tier("xai", "grok", "grok-4.7"),
}

# A listed model and the sort key that makes the newest one the largest.
Candidate = Tuple[str, tuple]


def is_alias(model_id: str) -> bool:
    return model_id in TIERS


# --- choosing the newest model of a tier from a vendor's listing ---------------------


def _version(text: str) -> tuple:
    return tuple(int(p) for p in text.split("."))


def pick_anthropic(models: List[Tuple[str, float]], tier: str) -> Optional[str]:
    """Newest ``claude-<tier>-...`` by ``created_at`` (epoch seconds)."""
    matching = [(mid, created) for mid, created in models if mid.startswith(f"claude-{tier}-")]
    return max(matching, key=lambda m: m[1])[0] if matching else None


_OPENAI_TIER = re.compile(r"^gpt-(\d+(?:\.\d+)*)-(\w+)$")


def pick_openai(models: List[Tuple[str, float]], tier: str) -> Optional[str]:
    """Highest ``gpt-<version>-<tier>`` (no dated snapshots or variants), then newest."""
    best: Optional[Tuple[tuple, float, str]] = None
    for mid, created in models:
        m = _OPENAI_TIER.match(mid)
        if m and m.group(2) == tier:
            key = (_version(m.group(1)), created, mid)
            best = key if best is None or key > best else best
    return best[2] if best else None


_GEMINI_TIER = re.compile(r"^gemini-(\d+(?:\.\d+)*)-(flash-lite|flash|pro)(-preview)?$")


def pick_google(models: List[str], tier: str) -> Optional[str]:
    """Highest ``gemini-<version>-<tier>[-preview]``; at the same version stable wins.

    ``flash-lite`` is its own tier, so the flash tier never drops to a lite model.
    Excludes image/tts variants and dated previews, which aren't the tier's
    general-purpose model.
    """
    best: Optional[Tuple[tuple, int, str]] = None
    for mid in models:
        m = _GEMINI_TIER.match(mid)
        if m and m.group(2) == tier:
            key = (_version(m.group(1)), 0 if m.group(3) else 1, mid)
            best = key if best is None or key > best else best
    return best[2] if best else None


_GROK = re.compile(r"^grok-(\d+(?:\.\d+)*)$")


def pick_xai(models: List[Tuple[str, float]], tier: str) -> Optional[str]:
    """Highest plain ``grok-<version>`` - xAI's recommended general model.

    Dated reasoning/non-reasoning snapshots, multi-agent, build, image and video models
    are skipped: none is the general-purpose line. ``tier`` is always "grok".
    """
    best: Optional[Tuple[tuple, float, str]] = None
    for mid, created in models:
        m = _GROK.match(mid)
        if m:
            key = (_version(m.group(1)), created, mid)
            best = key if best is None or key > best else best
    return best[2] if best else None


# --- listing a vendor's models through its SDK ---------------------------------------


def _list_anthropic(api_key: str) -> List[Tuple[str, float]]:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, timeout=VENDOR_TIMEOUT_SECONDS, max_retries=1)
    return [(m.id, m.created_at.timestamp()) for m in client.models.list()]


def _list_openai(api_key: str) -> List[Tuple[str, float]]:
    import openai

    client = openai.OpenAI(api_key=api_key, timeout=VENDOR_TIMEOUT_SECONDS, max_retries=1)
    return [(m.id, float(m.created)) for m in client.models.list()]


def _list_google(api_key: str) -> List[str]:
    from google import genai

    client = genai.Client(api_key=api_key)
    return [(m.name or "").removeprefix("models/") for m in client.models.list()]


def _list_xai(api_key: str) -> List[Tuple[str, float]]:
    import openai  # xAI's API is OpenAI-compatible, /v1/models included

    client = openai.OpenAI(
        api_key=api_key, base_url="https://api.x.ai/v1", timeout=VENDOR_TIMEOUT_SECONDS, max_retries=1
    )
    return [(m.id, float(m.created or 0)) for m in client.models.list()]


def _api_key(vendor: str) -> str:
    from api.settings import api_settings  # lazy: api imports agents

    return {
        "anthropic": api_settings.anthropic_api_key,
        "openai": api_settings.openai_api_key,
        "google": api_settings.gemini_api_key,
        "xai": api_settings.xai_api_key,
    }[vendor] or ""


def _ask_vendor(tier: Tier) -> Optional[str]:
    key = _api_key(tier.vendor)
    if not key:
        raise LookupError(f"no {tier.vendor} API key configured")
    if tier.vendor == "anthropic":
        return pick_anthropic(_list_anthropic(key), tier.name)
    if tier.vendor == "openai":
        return pick_openai(_list_openai(key), tier.name)
    if tier.vendor == "xai":
        return pick_xai(_list_xai(key), tier.name)
    return pick_google(_list_google(key), tier.name)


# --- cached resolution ----------------------------------------------------------------


class ModelResolver:
    def __init__(
        self,
        ask_vendor: Callable[[Tier], Optional[str]] = _ask_vendor,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._ask_vendor = ask_vendor
        self._clock = clock
        self._cache: Dict[str, Tuple[str, float]] = {}  # alias -> (model id, expires at)
        self._lock = threading.Lock()

    def resolve(self, model_id: str) -> str:
        tier = TIERS.get(model_id)
        if tier is None:
            return model_id
        now = self._clock()
        with self._lock:
            cached = self._cache.get(model_id)
            if cached and cached[1] > now:
                return cached[0]
            try:
                found = self._ask_vendor(tier)
            except Exception as e:  # noqa: BLE001 - any failure means "use the known model"
                found = None
                reason = f"{type(e).__name__}: {e}"
            else:
                reason = "no model in the listing matches the tier"
            if found:
                self._cache[model_id] = (found, now + RESOLVE_TTL_SECONDS)
                if not cached or cached[0] != found:
                    # Log the tier's own name, never the caller's string: model_id can
                    # come from a request body (log injection).
                    logger.info("Model alias %s -> %s", tier.alias, found)
                return found
            stale_or_fallback = cached[0] if cached else tier.fallback
            logger.warning("Could not resolve %s (%s); using %s", tier.alias, reason, stale_or_fallback)
            self._cache[model_id] = (stale_or_fallback, now + RETRY_AFTER_SECONDS)
            return stale_or_fallback

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_resolver = ModelResolver()


def resolve_model_id(model: Union[str, "object"]) -> str:
    """Concrete vendor model id for a Model enum member or model id string."""
    value = getattr(model, "value", model)
    return _resolver.resolve(str(value))
