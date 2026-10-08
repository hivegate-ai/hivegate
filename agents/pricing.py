"""Model prices, for turning a run's token counts into dollars.

Prices are USD per million tokens. A model that is not in the table is *unpriced*:
its calls are recorded with cost NULL and the model listed in `unpriced_models`, so
the dashboard can show what it can't price instead of showing a guessed number. Add
or correct a price without a redeploy through the PRICING_OVERRIDES env var (JSON):

    PRICING_OVERRIDES='{"claude-opus-4-5": {"input": 5, "output": 25,
                                            "cache_read": 0.5, "cache_write": 6.25}}'

Providers report tokens differently, and pricing has to follow each convention (see
agno's per-provider metrics parsing):
  - Anthropic: input_tokens EXCLUDES cache reads and writes, which are reported
    separately; thinking is part of output_tokens.
  - Gemini and OpenAI: input_tokens INCLUDES cached tokens (cache_read is a subset);
    OpenAI counts reasoning inside output, Gemini reports thinking separately
    (reasoning_tokens) and bills it at the output rate.

Sources (checked 2026-10-08): Anthropic - the claude-api reference (cache reads are
0.1x input except where listed; cache writes 1.25x for the 5-minute TTL); Google -
ai.google.dev/gemini-api/docs/pricing (paid tier, prompts <= 200k tokens); OpenAI
GPT-6 - the prices noted in agents/__init__.py.
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

PRICES_VERSION = "2026-10-08"
_MTOK = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    input: Decimal
    output: Decimal
    cache_read: Optional[Decimal] = None  # None: cached tokens billed as input
    cache_write: Optional[Decimal] = None  # None: cache writes billed as input
    input_includes_cache: bool = False  # Gemini/OpenAI: cache_read is part of input
    reasoning_billed_separately: bool = False  # Gemini: thinking not in output_tokens
    until: Optional[date] = None  # last day this price applies (inclusive)


def _anthropic(inp, out, cache_read=None) -> Price:
    i = Decimal(str(inp))
    return Price(
        input=i,
        output=Decimal(str(out)),
        cache_read=Decimal(str(cache_read)) if cache_read is not None else i / 10,
        cache_write=i * Decimal("1.25"),
    )


def _gemini(inp, out, cache_read=None, until=None) -> Price:
    return Price(
        input=Decimal(str(inp)),
        output=Decimal(str(out)),
        cache_read=Decimal(str(cache_read)) if cache_read is not None else None,
        input_includes_cache=True,
        reasoning_billed_separately=True,
        until=until,
    )


def _openai(inp, out) -> Price:
    return Price(input=Decimal(str(inp)), output=Decimal(str(out)), input_includes_cache=True)


_GEMINI_2026 = date(2026, 12, 31)

# model id -> price periods, oldest first. The last period has no `until`.
PRICES: Dict[str, List[Price]] = {
    # Anthropic
    "claude-fable-5-1": [_anthropic(10, 50, cache_read=0.25)],
    "claude-mythos-5-1": [_anthropic(10, 50, cache_read=0.25)],
    "claude-fable-5": [_anthropic(10, 50, cache_read=1)],
    "claude-mythos-5": [_anthropic(10, 50, cache_read=1)],
    "claude-opus-5-5": [_anthropic(4, 20, cache_read=0.20)],
    "claude-opus-5": [_anthropic(5, 25)],
    "claude-opus-4-8": [_anthropic(5, 25)],
    "claude-opus-4-7": [_anthropic(5, 25)],
    "claude-opus-4-6": [_anthropic(5, 25)],
    "claude-sonnet-5-5": [_anthropic(2, 10, cache_read=0.20)],
    "claude-sonnet-5": [_anthropic(2, 10)],
    "claude-sonnet-4-6": [_anthropic(3, 15)],
    "claude-haiku-4-5": [_anthropic(1, 5)],
    # Google (paid tier; Pro models at the <= 200k-token prompt rate)
    "gemini-3.8-flash": [_gemini(0.75, 3.75, 0.075, until=_GEMINI_2026), _gemini(1.50, 7.50, 0.15)],
    "gemini-3.7-flash": [_gemini(0.75, 3.75, 0.075, until=_GEMINI_2026), _gemini(1.50, 7.50, 0.15)],
    "gemini-3.6-flash": [_gemini(0.75, 3.75, 0.075, until=_GEMINI_2026), _gemini(1.50, 7.50, 0.15)],
    "gemini-3.5-flash": [_gemini(1.50, 9.00, 0.15)],
    "gemini-3.5-flash-lite": [_gemini(0.30, 2.50)],
    "gemini-3.1-flash-lite": [_gemini(0.25, 1.50, 0.025)],
    "gemini-3.1-pro-preview": [_gemini(2.00, 12.00, 0.20)],
    "gemini-2.5-pro": [_gemini(1.25, 10.00, 0.125)],
    "gemini-2.5-flash": [_gemini(0.30, 2.50, 0.03)],
    "gemini-2.5-flash-lite": [_gemini(0.10, 0.40, 0.01)],
    # OpenAI
    "gpt-6-luna": [_openai(0.1, 0.5)],
    "gpt-6-sol": [_openai(2, 10)],
    "gpt-6-astra": [_openai(10, 50)],
}

_DATE_SUFFIX = re.compile(r"-\d{8}$")


def _overrides() -> Dict[str, List[Price]]:
    raw = os.getenv("PRICING_OVERRIDES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        out = {}
        for model_id, p in data.items():
            out[model_id] = [
                Price(
                    input=Decimal(str(p["input"])),
                    output=Decimal(str(p["output"])),
                    cache_read=Decimal(str(p["cache_read"])) if p.get("cache_read") is not None else None,
                    cache_write=Decimal(str(p["cache_write"])) if p.get("cache_write") is not None else None,
                    input_includes_cache=bool(p.get("input_includes_cache", False)),
                    reasoning_billed_separately=bool(p.get("reasoning_billed_separately", False)),
                )
            ]
        return out
    except (ValueError, KeyError, TypeError, AttributeError):
        logging.exception("PRICING_OVERRIDES is not valid JSON of {model: {input, output, ...}}; ignored")
        return {}


def price_for(model_id: Optional[str], at: Optional[datetime] = None) -> Optional[Price]:
    """The price of `model_id` on the date of `at` (default now), or None if unpriced.

    Exact id first (overrides win), then the id without a date suffix:
    claude-haiku-4-5-20251001 -> claude-haiku-4-5.
    """
    if not model_id:
        return None
    table = {**PRICES, **_overrides()}
    periods = table.get(model_id) or table.get(_DATE_SUFFIX.sub("", model_id))
    if not periods:
        return None
    day = (at or datetime.now(timezone.utc)).date()
    for p in periods:
        if p.until is None or day <= p.until:
            return p
    return periods[-1]


def _tokens(m: Any, name: str) -> int:
    value = m.get(name) if isinstance(m, dict) else getattr(m, name, None)
    if isinstance(value, list):  # some runtimes report a one-element list
        value = sum(v or 0 for v in value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def cost_of_tokens(price: Price, m: Any) -> Decimal:
    """USD for one model's token counts (a dict or a metrics object)."""
    inp = _tokens(m, "input_tokens")
    out = _tokens(m, "output_tokens")
    cache_read = _tokens(m, "cache_read_tokens")
    cache_write = _tokens(m, "cache_write_tokens")
    reasoning = _tokens(m, "reasoning_tokens")
    uncached = max(inp - cache_read, 0) if price.input_includes_cache else inp
    billed_out = out + (reasoning if price.reasoning_billed_separately else 0)
    usd = (
        Decimal(uncached) * price.input
        + Decimal(cache_read) * (price.cache_read if price.cache_read is not None else price.input)
        + Decimal(cache_write) * (price.cache_write if price.cache_write is not None else price.input)
        + Decimal(billed_out) * price.output
    ) / _MTOK
    return usd


def cost_of(
    entries: Iterable[Tuple[Optional[str], Any]], at: Optional[datetime] = None
) -> Tuple[Optional[Decimal], List[str]]:
    """Total USD for (model_id, token counts) entries, and the models it couldn't price.

    Returns (None, [...]) when nothing could be priced, so an all-unpriced call never
    shows as $0.
    """
    total = Decimal(0)
    priced_any = False
    unpriced: List[str] = []
    for model_id, m in entries:
        if not any(
            _tokens(m, k)
            for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")
        ):
            continue
        price = price_for(model_id, at)
        if price is None:
            unpriced.append(model_id or "unknown")
            continue
        total += cost_of_tokens(price, m)
        priced_any = True
    return (total if priced_any else None), sorted(set(unpriced))
