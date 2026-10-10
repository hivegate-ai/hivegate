"""Per-call usage and cost metering for every model call made through the gateway.

Each chat/commit/team run gets a UsageContext (who and what was asked) and a
UsageCollector (what actually ran). The collector is fed the run's events - or, for
non-streaming calls, the final response - and `record()` turns it into one priced
`token_usage` row.

Why it is built like this:
  - Metrics are taken from ANY event that carries them. The old capture only looked
    inside events with `content`, so a RunCompleted without content fell back to a
    len/4 estimate.
  - Failed runs are recorded too, with their real tokens. A run that errors or is
    refused after the model was called is still billed, and a retry loop of such runs
    can spend a lot unseen. agno attaches no run metrics to RunError, so the
    per-request ModelRequestCompleted counts are summed as the fallback.
  - The row is priced per model in the run (main, memory, compression, ...), each at
    its own price (agents/pricing.py).
  - Writing never blocks the event loop (thread pool, own session) and never fails the
    caller's request.
  - A client that hangs up mid-stream still gets its run recorded, as `cancelled`. The
    server cancels the stream through an anyio cancel scope, where every later await
    raises again - including the write in the streamer's `finally` - so streamers wrap
    it in `shielded()`. Tokens of a model request still in flight at the disconnect are
    unknown; the row says so rather than presenting a partial count as complete.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, cast

from agents import pricing

ERROR_MAX = 2000

logger = logging.getLogger(__name__)

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


@dataclass
class UsageContext:
    agent_id: Optional[str]
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    tenant_id: Optional[str] = None
    api_key_id: Optional[int] = None
    api_key_name: Optional[str] = None
    request_model: Optional[str] = None
    kind: str = "chat"  # chat | commit | team
    started_at: float = field(default_factory=time.monotonic)


def context_for(agent_id: Optional[str], body: Any, api_key: Any = None, kind: str = "chat") -> UsageContext:
    """Build the context from a ChatRequest/CommitRequest-like body and the caller's key."""
    tenant = None
    for profile in ("tenant_profile", "user_profile"):
        tenant = tenant or getattr(getattr(body, profile, None), "tenant_id", None)
    model = getattr(body, "model", None)
    return UsageContext(
        agent_id=agent_id,
        user_id=getattr(body, "user_id", None),
        session_id=getattr(body, "session_id", None),
        tenant_id=tenant,
        api_key_id=getattr(api_key, "id", None),
        api_key_name=getattr(api_key, "name", None),
        request_model=getattr(model, "value", model) if model is not None else None,
        kind=kind,
    )


def _as_int(value: Any) -> int:
    if isinstance(value, list):
        value = sum(v or 0 for v in value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _metrics_dict(metrics: Any) -> Optional[Dict[str, Any]]:
    if metrics is None:
        return None
    if isinstance(metrics, dict):
        return metrics
    if hasattr(metrics, "to_dict"):
        return metrics.to_dict()
    return None


class UsageCollector:
    """Accumulates what one run used. Feed it events (`observe`) or a response."""

    def __init__(self, ctx: UsageContext):
        self.ctx = ctx
        self.run_id: Optional[str] = None
        self.metrics: Optional[Dict[str, Any]] = None
        self.request_tokens: Dict[str, int] = collections.defaultdict(int)
        self.request_models: Dict[Tuple[str, str], Dict[str, int]] = {}
        self.model_requests = 0
        self.requests_started = 0
        self.tool_calls = 0
        self.finished = False  # a top-level RunCompleted / RunError / RunCancelled arrived
        self.status = "completed"
        self.error: Optional[str] = None
        self.output_text = ""
        self.input_text = ""
        self.main_model: Optional[str] = None
        self.main_provider: Optional[str] = None
        self._top_level_metrics = False

    # -- feeding ----------------------------------------------------------------
    def observe(self, event: Dict[str, Any]) -> None:
        try:
            self._observe(event)
        except Exception:  # metering must never break a stream
            logger.exception("usage: could not read an event")

    def _observe(self, event: dict[str, Any]) -> None:
        # Teams emit Team* variants of the same events; a team member's own events
        # carry parent_run_id. Only top-level events decide the run's totals and
        # status - member events still count their model requests and tool calls.
        kind = event.get("event") or ""
        if kind.startswith("Team"):
            kind = kind[len("Team") :]
        top_level = not event.get("parent_run_id")
        if event.get("run_id") and top_level:
            self.run_id = event["run_id"]
        if isinstance(event.get("content"), str) and kind not in ("RunCompleted", "RunError") and top_level:
            self.output_text += event["content"]
        metrics = _metrics_dict(event.get("metrics"))
        if metrics and (top_level or not self._top_level_metrics):
            self.metrics = metrics
            self._top_level_metrics = self._top_level_metrics or top_level
        if kind == "ModelRequestStarted":
            self.requests_started += 1
        elif kind == "ModelRequestCompleted":
            self.model_requests += 1
            model = event.get("model") or ""
            provider = event.get("model_provider") or ""
            bucket = self.request_models.setdefault((model, provider), collections.defaultdict(int))
            for name in _TOKEN_FIELDS:
                n = _as_int(event.get(name))
                self.request_tokens[name] += n
                bucket[name] += n
        elif kind == "ToolCallStarted":
            self.tool_calls += 1
        elif kind == "RunCompleted" and top_level:
            self.finished = True
            if isinstance(event.get("content"), str) and not self.output_text:
                self.output_text = event["content"]
        elif kind == "RunError" and top_level:
            self.finished = True
            text = str(event.get("content") or event.get("error") or "run error")
            self.fail(text, refused="refusal" in text.lower())
        elif kind == "RunPaused":
            self.status = "paused"
        elif kind == "RunCancelled" and top_level:
            self.finished = True
            self.status = "cancelled"

    def observe_response(self, response: Any) -> None:
        """Non-streaming paths: the final RunOutput (or TeamRunOutput)."""
        try:
            self.run_id = getattr(response, "run_id", None) or self.run_id
            metrics = _metrics_dict(getattr(response, "metrics", None))
            if metrics:
                self.metrics = metrics
            content = getattr(response, "content", None)
            if isinstance(content, str):
                self.output_text = content
            tools = getattr(response, "tools", None) or []
            self.tool_calls = max(self.tool_calls, len(tools))
            status = str(getattr(response, "status", "") or "").lower()
            if "paused" in status:
                self.status = "paused"
            elif "cancel" in status:
                self.status = "cancelled"
            elif "error" in status and self.status == "completed":
                self.status = "error"
        except Exception:
            logger.exception("usage: could not read a response")

    def fail(self, error: str, refused: bool = False) -> None:
        self.status = "refused" if refused else "error"
        self.error = (error or "")[:ERROR_MAX]

    def in_flight(self) -> int:
        """Model requests that started but never reported their tokens."""
        return max(self.requests_started - self.model_requests, 0)

    def disconnect(self) -> None:
        """The client went away before the run finished (the stream was cancelled).

        The run is recorded as `cancelled`, not `completed`: its final metrics never
        arrived, so its counts are only the model requests that had already completed,
        and a request still in flight was billed by the provider for tokens nobody saw.
        """
        if self.finished or self.status != "completed":
            return  # the run had already ended; the client only missed the tail
        self.status = "cancelled"
        pending = self.in_flight()
        self.error = "client disconnected before the run finished" + (
            f"; {pending} model request(s) in flight, their tokens are not counted" if pending else ""
        )

    def set_model(self, model_id: Optional[str], provider: Optional[str] = None) -> None:
        """The agent's main model, used when the run carried no per-model details."""
        self.main_model = self.main_model or model_id
        self.main_provider = self.main_provider or provider

    # -- building the row ---------------------------------------------------------
    def _entries(self) -> List[Tuple[str, Optional[str], Optional[str], Dict[str, Any]]]:
        """(model_type, model_id, provider, token counts) for each model that ran."""
        details = (self.metrics or {}).get("details") or {}
        entries: List[Tuple[str, Optional[str], Optional[str], Dict[str, Any]]] = []
        for model_type, models in details.items():
            for m in models or []:
                m = _metrics_dict(m) or {}
                entries.append((model_type, m.get("id") or self.main_model, m.get("provider"), m))
        if entries:
            return entries
        if self.metrics and any(_as_int(self.metrics.get(k)) for k in _TOKEN_FIELDS):
            return [("model", self.main_model, self.main_provider, self.metrics)]
        if self.request_models:
            return [
                ("model", model or self.main_model, provider or self.main_provider, dict(tokens))
                for (model, provider), tokens in self.request_models.items()
            ]
        return []

    def build_row(self, at: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """The token_usage values for this run, or None if it used no model at all."""
        at = at or datetime.now(timezone.utc)
        entries = self._entries()
        estimated = False
        if not entries:
            if not self.output_text and not self.in_flight():
                return None  # nothing ran (e.g. an MCP connect failure)
            # Nothing was reported. Either the run gave no metrics, or the client hung
            # up while the first model request was in flight - its prompt was still
            # sent, so record an estimate rather than no row at all.
            estimated = True
            entries = [
                (
                    "model",
                    self.main_model,
                    self.main_provider,
                    {
                        "input_tokens": estimate_tokens(self.input_text),
                        "output_tokens": estimate_tokens(self.output_text),
                    },
                )
            ]

        totals: Dict[str, int] = collections.defaultdict(int)
        breakdown: Dict[str, List[Dict[str, Any]]] = {}
        for model_type, model_id, provider, tokens in entries:
            for name in _TOKEN_FIELDS:
                totals[name] += _as_int(tokens.get(name))
            cost, _ = pricing.cost_of([(model_id, tokens)], at)
            breakdown.setdefault(model_type, []).append(
                {
                    "id": model_id,
                    "provider": provider,
                    **{name: _as_int(tokens.get(name)) for name in _TOKEN_FIELDS},
                    "cost_usd": float(cost) if cost is not None else None,
                }
            )
        cost, unpriced = pricing.cost_of([(m, t) for _, m, _, t in entries], at)
        main = next((e for e in entries if e[0] == "model"), entries[0])
        total_tokens = totals["total_tokens"] or totals["input_tokens"] + totals["output_tokens"]
        metrics = self.metrics or {}
        duration = metrics.get("duration")
        ttft = metrics.get("time_to_first_token")
        ctx = self.ctx
        return {
            "agent_id": ctx.agent_id,
            "session_id": ctx.session_id,
            "user_id": ctx.user_id,
            "tenant_id": ctx.tenant_id,
            "api_key_id": ctx.api_key_id,
            "api_key_name": ctx.api_key_name,
            "request_model": ctx.request_model,
            "run_id": self.run_id,
            "model": main[1],
            "provider": main[2],
            "prompt_tokens": totals["input_tokens"],
            "completion_tokens": totals["output_tokens"],
            "total_tokens": total_tokens,
            "cache_read_tokens": totals["cache_read_tokens"],
            "cache_write_tokens": totals["cache_write_tokens"],
            "reasoning_tokens": totals["reasoning_tokens"],
            "model_requests": self.model_requests,
            "tool_calls": self.tool_calls,
            "duration_ms": int(float(duration) * 1000) if duration else int((time.monotonic() - ctx.started_at) * 1000),
            "ttft_ms": int(float(ttft) * 1000) if ttft else None,
            "cost_usd": cost,
            "unpriced_models": unpriced or None,
            "prices_version": pricing.PRICES_VERSION,
            "status": self.status,
            "error": self.error,
            "breakdown": breakdown,
            "is_estimated": estimated,
        }


def estimate_tokens(text: Optional[str]) -> int:
    """Rough fallback (~4 characters per token) for runs that reported no metrics."""
    return max(1, len(text) // 4) if text else 0


# -- writing ------------------------------------------------------------------------

# Writes started by shielded(), kept referenced until they finish so they aren't
# garbage-collected mid-flight.
_background: set = set()


async def shielded(awaitable: Any) -> Any:
    """Await `awaitable` in its own task, so cancelling the caller can't cancel it.

    For the usage write in a streamer's `finally`: when the client disconnects, the
    server cancels the stream through an anyio cancel scope, and every await inside a
    cancelled scope raises again - so a plain `await record(...)` there loses the row.
    Here the write carries on in the background; the caller still sees the
    cancellation and unwinds as it should.
    """
    task = asyncio.ensure_future(awaitable)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return await asyncio.shield(task)


def write_row(row: Dict[str, Any], session_factory=None) -> Optional[int]:
    """Insert one token_usage row in its own session. Never raises."""
    try:
        from db.db_models import TokenUsage

        if session_factory is None:
            from db.session import SessionLocal as session_factory  # noqa: N813
        db = session_factory()
        try:
            created = datetime.utcnow()
            record = TokenUsage(created_at=created, **row)
            db.add(record)
            db.commit()
            row_id = cast(Optional[int], record.id)
        finally:
            db.close()
        cost = row.get("cost_usd")
        logger.info(
            "usage agent=%s tenant=%s model=%s status=%s in=%s out=%s cache_read=%s requests=%s cost=%s",
            row.get("agent_id"),
            row.get("tenant_id"),
            row.get("model"),
            row.get("status"),
            row.get("prompt_tokens"),
            row.get("completion_tokens"),
            row.get("cache_read_tokens"),
            row.get("model_requests"),
            f"${cost:.6f}" if cost is not None else "unpriced",
        )
        return row_id
    except Exception:
        logger.exception("usage: could not store token usage (agent=%s)", row.get("agent_id"))
        return None


async def record(collector: UsageCollector, session_factory=None) -> Optional[Dict[str, Any]]:
    """Price and store the run without blocking the event loop. Never raises.

    Returns the usage summary (tokens, cost, status) for callers that report it back,
    or None when the run used no model.
    """
    try:
        row = collector.build_row()
    except Exception:
        logger.exception("usage: could not build the usage row")
        return None
    if row is None:
        return None
    from starlette.concurrency import run_in_threadpool

    await run_in_threadpool(write_row, row, session_factory)
    return summary(row)


def summary(row: Dict[str, Any]) -> Dict[str, Any]:
    """The client-facing slice of a usage row (non-streaming ChatResponse.token_usage)."""
    cost = row.get("cost_usd")
    return {
        "input_tokens": row["prompt_tokens"],
        "output_tokens": row["completion_tokens"],
        "total_tokens": row["total_tokens"],
        "cache_read_tokens": row["cache_read_tokens"],
        "cache_write_tokens": row["cache_write_tokens"],
        "reasoning_tokens": row["reasoning_tokens"],
        "model_requests": row["model_requests"],
        "model": row["model"],
        "cost_usd": float(cost) if cost is not None else None,
        "unpriced_models": row.get("unpriced_models"),
        "status": row["status"],
    }


def model_of(agent: Any) -> Tuple[Optional[str], Optional[str]]:
    model = getattr(agent, "model", None)
    return getattr(model, "id", None), getattr(model, "provider", None)
