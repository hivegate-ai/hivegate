"""api/services/usage.py: what a run is recorded as, from its events or final response."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api.services import usage

AT = datetime(2026, 10, 8, tzinfo=timezone.utc)


def collector(**ctx):
    c = usage.UsageCollector(usage.UsageContext(agent_id="pf-cash-manager", **ctx))
    c.set_model("claude-sonnet-5-5", "Anthropic")
    return c


def completed(metrics, content="done"):
    return {"event": "RunCompleted", "run_id": "r1", "content": content, "metrics": metrics}


def test_metrics_on_a_completion_without_content_are_used():
    # The old capture only read metrics inside events carrying `content`.
    c = collector()
    c.observe({"event": "RunCompleted", "run_id": "r1", "metrics": {"input_tokens": 1000, "output_tokens": 100}})
    row = c.build_row(AT)
    assert (row["prompt_tokens"], row["completion_tokens"], row["is_estimated"]) == (1000, 100, False)
    assert row["cost_usd"] > 0


def test_each_model_type_is_priced_at_its_own_model():
    c = collector()
    c.observe(
        completed(
            {
                "input_tokens": 2_000_000,
                "output_tokens": 0,
                "details": {
                    "model": [{"id": "claude-sonnet-5-5", "provider": "Anthropic", "input_tokens": 1_000_000}],
                    "memory_model": [
                        {"id": "claude-haiku-4-5-20251001", "provider": "Anthropic", "input_tokens": 1_000_000}
                    ],
                },
            }
        )
    )
    row = c.build_row(AT)
    assert float(row["cost_usd"]) == pytest.approx(3.0)  # sonnet 2 + haiku 1
    assert row["breakdown"]["memory_model"][0]["cost_usd"] == pytest.approx(1.0)
    assert row["model"] == "claude-sonnet-5-5"


def test_a_failed_run_is_recorded_with_the_tokens_its_requests_used():
    # RunError carries no run metrics; the per-request events are what was billed.
    c = collector()
    for _ in range(3):
        c.observe(
            {
                "event": "ModelRequestCompleted",
                "model": "claude-sonnet-5-5",
                "model_provider": "Anthropic",
                "input_tokens": 100_000,
                "output_tokens": 0,
            }
        )
    c.observe({"event": "RunError", "content": "stop_reason=refusal category=reasoning_extraction"})
    row = c.build_row(AT)
    assert row["status"] == "refused"
    assert row["model_requests"] == 3
    assert row["prompt_tokens"] == 300_000
    assert float(row["cost_usd"]) == pytest.approx(0.6)


def test_tool_calls_pauses_and_cancellations():
    c = collector()
    c.observe({"event": "ToolCallStarted"})
    c.observe({"event": "ToolCallStarted"})
    c.observe({"event": "RunPaused", "run_id": "r9"})
    c.observe({"event": "ModelRequestCompleted", "model": "claude-sonnet-5-5", "input_tokens": 10})
    row = c.build_row(AT)
    assert (row["tool_calls"], row["status"], row["run_id"]) == (2, "paused", "r9")
    c2 = collector()
    c2.observe({"event": "RunCancelled"})
    c2.observe({"event": "ModelRequestCompleted", "model": "x", "input_tokens": 1})
    assert c2.build_row(AT)["status"] == "cancelled"


def test_team_events_and_member_events():
    c = collector()
    # a member's completion and error don't decide the team run's totals or status
    c.observe({"event": "RunCompleted", "parent_run_id": "team-run", "metrics": {"input_tokens": 5}})
    c.observe({"event": "RunError", "parent_run_id": "team-run", "content": "member failed"})
    c.observe({"event": "TeamModelRequestCompleted", "model": "claude-sonnet-5-5", "input_tokens": 7})
    c.observe({"event": "TeamRunCompleted", "run_id": "team-run", "metrics": {"input_tokens": 50, "output_tokens": 5}})
    row = c.build_row(AT)
    assert (row["status"], row["prompt_tokens"], row["model_requests"], row["run_id"]) == (
        "completed",
        50,
        1,
        "team-run",
    )


def test_no_model_call_means_no_row():
    assert collector().build_row(AT) is None


def test_estimate_only_when_nothing_was_reported():
    c = collector()
    c.input_text = "x" * 400
    c.observe({"event": "RunContent", "content": "y" * 40})
    row = c.build_row(AT)
    assert row["is_estimated"] is True and (row["prompt_tokens"], row["completion_tokens"]) == (100, 10)


def test_unpriced_models_are_listed_and_cost_is_null():
    c = usage.UsageCollector(usage.UsageContext(agent_id="a"))
    c.set_model("gemini-3-flash-preview", "Google")
    c.observe(completed({"input_tokens": 10, "output_tokens": 1}))
    row = c.build_row(AT)
    assert row["cost_usd"] is None and row["unpriced_models"] == ["gemini-3-flash-preview"]


def test_non_streaming_response():
    c = collector()
    response = SimpleNamespace(
        run_id="r2",
        content="answer",
        status="COMPLETED",
        tools=[object(), object()],
        metrics=SimpleNamespace(to_dict=lambda: {"input_tokens": 10, "output_tokens": 2, "duration": 1.5}),
    )
    c.observe_response(response)
    row = c.build_row(AT)
    assert (row["run_id"], row["tool_calls"], row["duration_ms"], row["status"]) == ("r2", 2, 1500, "completed")


def test_context_from_a_request_body_and_key():
    body = SimpleNamespace(
        user_id="u",
        session_id="s",
        model=SimpleNamespace(value="anthropic:sonnet-latest"),
        tenant_profile=None,
        user_profile=SimpleNamespace(tenant_id="t-1"),
    )
    ctx = usage.context_for("pf-x", body, SimpleNamespace(id=7, name="classifier"))
    assert (ctx.agent_id, ctx.tenant_id, ctx.api_key_id, ctx.api_key_name, ctx.request_model) == (
        "pf-x",
        "t-1",
        7,
        "classifier",
        "anthropic:sonnet-latest",
    )


def test_a_bad_event_never_breaks_the_stream():
    c = collector()
    c.observe({"event": "ModelRequestCompleted", "input_tokens": object()})  # unparsable
    c.observe(completed({"input_tokens": 3, "output_tokens": 1}))
    assert c.build_row(AT)["prompt_tokens"] == 3


def test_write_row_never_raises_and_record_returns_a_summary():
    def broken_session():
        raise RuntimeError("db down")

    assert usage.write_row({"agent_id": "a"}, session_factory=broken_session) is None
    c = collector()
    c.observe(completed({"input_tokens": 1_000_000, "output_tokens": 0}))
    out = asyncio.run(usage.record(c, session_factory=broken_session))
    assert out["cost_usd"] == pytest.approx(2.0) and out["status"] == "completed"
