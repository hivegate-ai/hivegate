"""Read-side queries over token_usage for the usage API (api/routes/usage.py).

Postgres SQL (date bucketing, JSONB). Every query is bounded by a time window and
uses the (x, created_at) indexes from migration 011. created_at is stored as naive
UTC (datetime.utcnow), so windows are computed the same way.
"""

import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

WINDOWS = {
    "1h": datetime.timedelta(hours=1),
    "6h": datetime.timedelta(hours=6),
    "24h": datetime.timedelta(hours=24),
    "7d": datetime.timedelta(days=7),
    "30d": datetime.timedelta(days=30),
}
BUCKETS = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
# group_by name -> SQL expression (never user text)
GROUPS = {
    "agent": "agent_id",
    "tenant": "tenant_id",
    "model": "model",
    "api_key": "api_key_name",
    "user": "user_id",
    "status": "status",
}
FAILED = "('error', 'refused')"

_COLUMNS = (
    "id, created_at, agent_id, tenant_id, user_id, session_id, run_id, api_key_name, "
    "request_model, model, provider, status, error, prompt_tokens, completion_tokens, "
    "total_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, model_requests, "
    "tool_calls, duration_ms, ttft_ms, cost_usd, unpriced_models, is_estimated, breakdown"
)

_AGGREGATES = """
    COUNT(*) AS calls,
    COALESCE(SUM(cost_usd), 0) AS cost_usd,
    COALESCE(SUM(prompt_tokens), 0) AS input_tokens,
    COALESCE(SUM(completion_tokens), 0) AS output_tokens,
    COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
    COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
    COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
    COALESCE(SUM(model_requests), 0) AS model_requests,
    COUNT(*) FILTER (WHERE status IN {failed}) AS failed_calls,
    COALESCE(SUM(cost_usd) FILTER (WHERE status IN {failed}), 0) AS failed_cost_usd,
    COUNT(*) FILTER (WHERE cost_usd IS NULL) AS unpriced_calls,
    COUNT(*) FILTER (WHERE is_estimated) AS estimated_calls
""".format(failed=FAILED)


def since(window: str, now: Optional[datetime.datetime] = None) -> datetime.datetime:
    return (now or datetime.datetime.utcnow()) - WINDOWS[window]


def _row(r: Any) -> Dict[str, Any]:
    out = dict(r._mapping)
    for k, v in out.items():
        if hasattr(v, "is_finite"):  # Decimal
            out[k] = float(v)
        elif isinstance(v, datetime.datetime):
            out[k] = v.isoformat()
    return out


def _filters(agent: Optional[str], tenant: Optional[str], status: Optional[str]) -> tuple:
    clauses, params = [], {}
    for name, column, value in (
        ("agent", "agent_id", agent),
        ("tenant", "tenant_id", tenant),
        ("status", "status", status),
    ):
        if value:
            clauses.append(f"{column} = :{name}")
            params[name] = value
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def live(db: Session, after_id: int, limit: int) -> List[Dict[str, Any]]:
    """Calls with id > after_id, oldest first. On the first poll (after_id 0) only the
    latest `limit` calls, so a dashboard doesn't page through all history."""
    if after_id <= 0:
        rows = db.execute(
            text(f"SELECT {_COLUMNS} FROM token_usage ORDER BY id DESC LIMIT :limit"), {"limit": limit}
        ).fetchall()
        return [_row(r) for r in reversed(rows)]
    rows = db.execute(
        text(f"SELECT {_COLUMNS} FROM token_usage WHERE id > :after ORDER BY id ASC LIMIT :limit"),
        {"after": after_id, "limit": limit},
    ).fetchall()
    return [_row(r) for r in rows]


def totals(db: Session, window: str) -> Dict[str, Any]:
    r = db.execute(
        text(f"SELECT {_AGGREGATES} FROM token_usage WHERE created_at >= :since"), {"since": since(window)}
    ).fetchone()
    return _row(r)


def summary(db: Session, window: str, group_by: str, limit: int = 50) -> List[Dict[str, Any]]:
    column = GROUPS[group_by]
    rows = db.execute(
        text(
            f"SELECT COALESCE({column}, '(none)') AS key, {_AGGREGATES} FROM token_usage "
            f"WHERE created_at >= :since GROUP BY 1 ORDER BY cost_usd DESC, calls DESC LIMIT :limit"
        ),
        {"since": since(window), "limit": limit},
    ).fetchall()
    return [_row(r) for r in rows]


def model_type_split(db: Session, window: str) -> List[Dict[str, Any]]:
    """Cost and tokens per model type (main model, memory model, compression model, ...)."""
    rows = db.execute(
        text(
            """
            SELECT b.key AS model_type,
                   COUNT(*) AS entries,
                   COALESCE(SUM((m->>'cost_usd')::numeric), 0) AS cost_usd,
                   COALESCE(SUM((m->>'input_tokens')::bigint), 0) AS input_tokens,
                   COALESCE(SUM((m->>'output_tokens')::bigint), 0) AS output_tokens,
                   COALESCE(SUM((m->>'cache_read_tokens')::bigint), 0) AS cache_read_tokens
            FROM token_usage t
            CROSS JOIN LATERAL jsonb_each(t.breakdown) AS b(key, value)
            CROSS JOIN LATERAL jsonb_array_elements(b.value) AS m
            WHERE t.created_at >= :since AND t.breakdown IS NOT NULL
            GROUP BY b.key
            ORDER BY cost_usd DESC
            """
        ),
        {"since": since(window)},
    ).fetchall()
    return [_row(r) for r in rows]


def timeseries(db: Session, window: str, bucket: str, group_by: str, top: int = 8) -> List[Dict[str, Any]]:
    """Cost per time bucket per group. Groups outside the window's top `top` by cost are
    folded into '(other)' so a chart stays readable."""
    column = GROUPS[group_by]
    seconds = BUCKETS[bucket]
    rows = db.execute(
        text(
            f"""
            WITH ranked AS (
                SELECT COALESCE({column}, '(none)') AS key
                FROM token_usage WHERE created_at >= :since
                GROUP BY 1 ORDER BY COALESCE(SUM(cost_usd), 0) DESC LIMIT :top
            )
            SELECT to_timestamp(floor(extract(epoch FROM created_at) / :seconds) * :seconds)
                       AT TIME ZONE 'UTC' AS bucket,
                   CASE WHEN COALESCE({column}, '(none)') IN (SELECT key FROM ranked)
                        THEN COALESCE({column}, '(none)') ELSE '(other)' END AS key,
                   COUNT(*) AS calls,
                   COALESCE(SUM(cost_usd), 0) AS cost_usd,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM token_usage
            WHERE created_at >= :since
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        ),
        {"since": since(window), "seconds": seconds, "top": top},
    ).fetchall()
    return [_row(r) for r in rows]


def calls(
    db: Session,
    window: str,
    order: str = "cost",
    agent: Optional[str] = None,
    tenant: Optional[str] = None,
    status: Optional[str] = None,
    min_cost: Optional[float] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    where, params = _filters(agent, tenant, status)
    if min_cost is not None:
        where += " AND cost_usd >= :min_cost"
        params["min_cost"] = min_cost
    order_sql = "cost_usd DESC NULLS LAST, id DESC" if order == "cost" else "id DESC"
    rows = db.execute(
        text(f"SELECT {_COLUMNS} FROM token_usage WHERE created_at >= :since{where} ORDER BY {order_sql} LIMIT :limit"),
        {"since": since(window), "limit": limit, **params},
    ).fetchall()
    return [_row(r) for r in rows]
