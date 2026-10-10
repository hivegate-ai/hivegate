"""agents/pricing.py: lookups, per-provider token conventions, overrides."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from agents import pricing


def usd(x) -> Decimal:
    return Decimal(str(x))


def test_exact_and_date_suffixed_ids():
    assert pricing.price_for("claude-sonnet-5-5").input == usd(2)
    assert pricing.price_for("claude-haiku-4-5-20251001").output == usd(5)  # -> claude-haiku-4-5
    assert pricing.price_for("no-such-model") is None
    assert pricing.price_for(None) is None


def test_anthropic_cache_prices_are_per_model_not_a_flat_ratio():
    assert pricing.price_for("claude-fable-5-1").cache_read == usd("0.25")  # 0.025x
    assert pricing.price_for("claude-opus-5-5").cache_read == usd("0.20")  # 0.05x
    assert pricing.price_for("claude-sonnet-4-6").cache_read == usd("0.3")  # 0.1x
    assert pricing.price_for("claude-opus-5-5").cache_write == usd(5)  # 1.25x input


def test_anthropic_cost_counts_cache_separately_from_input():
    # Anthropic's input_tokens excludes cached tokens, so all four are added.
    p = pricing.price_for("claude-sonnet-5-5")
    m = {
        "input_tokens": 1_000_000,
        "output_tokens": 100_000,
        "cache_read_tokens": 1_000_000,
        "cache_write_tokens": 200_000,
    }
    # 2 + 0.2*10 ... = input 2 + output 1 + cache read 0.2 + cache write 0.5
    assert pricing.cost_of_tokens(p, m) == usd("3.7")


def test_gemini_cost_subtracts_cache_from_input_and_bills_thinking_as_output():
    p = pricing.price_for("gemini-3.5-flash")  # 1.50 / 9.00, cached 0.15
    m = {"input_tokens": 1_000_000, "cache_read_tokens": 400_000, "output_tokens": 100_000, "reasoning_tokens": 100_000}
    # uncached 600k*1.5 = 0.9; cached 400k*0.15 = 0.06; output (100k+100k)*9 = 1.8
    assert pricing.cost_of_tokens(p, m) == usd("2.76")


def test_openai_reasoning_is_not_double_counted():
    p = pricing.price_for("gpt-6-sol")
    m = {"input_tokens": 1_000_000, "output_tokens": 100_000, "reasoning_tokens": 50_000}
    assert pricing.cost_of_tokens(p, m) == usd(3)  # 2 + 0.1*10; reasoning is inside output


def test_dated_price_periods():
    before = datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)
    after = datetime(2027, 1, 1, 1, 0, tzinfo=timezone.utc)
    assert pricing.price_for("gemini-3.8-flash", before).input == usd("0.75")
    assert pricing.price_for("gemini-3.8-flash", after).input == usd("1.50")


def test_cost_of_reports_unpriced_models_and_never_prices_them_as_zero():
    cost, unpriced = pricing.cost_of([("mystery-model", {"input_tokens": 10})])
    assert cost is None and unpriced == ["mystery-model"]
    cost, unpriced = pricing.cost_of(
        [("claude-haiku-4-5", {"input_tokens": 1_000_000}), ("mystery-model", {"input_tokens": 5})]
    )
    assert cost == usd(1) and unpriced == ["mystery-model"]


def test_entries_with_no_tokens_are_ignored():
    assert pricing.cost_of([("mystery-model", {"input_tokens": 0})]) == (None, [])


def test_list_token_values_are_summed():
    p = pricing.price_for("claude-haiku-4-5")
    assert pricing.cost_of_tokens(p, {"input_tokens": [500_000, 500_000]}) == usd(1)


def test_overrides(monkeypatch):
    monkeypatch.setenv("PRICING_OVERRIDES", '{"claude-opus-4-5": {"input": 5, "output": 25, "cache_read": 0.5}}')
    p = pricing.price_for("claude-opus-4-5")
    assert (p.input, p.output, p.cache_read, p.cache_write) == (usd(5), usd(25), usd("0.5"), None)
    monkeypatch.setenv("PRICING_OVERRIDES", '{"claude-sonnet-5-5": {"input": 9, "output": 9}}')
    assert pricing.price_for("claude-sonnet-5-5").input == usd(9)  # override wins


@pytest.mark.parametrize("bad", ["not json", '{"m": {"input": 1}}', "[1, 2]"])
def test_bad_overrides_are_ignored(monkeypatch, bad):
    monkeypatch.setenv("PRICING_OVERRIDES", bad)
    assert pricing.price_for("claude-sonnet-5-5").input == usd(2)
