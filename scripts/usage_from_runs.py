"""Real token cost per agent from agno's stored runs (read-only).

Every agent run the gateway made is stored by agno with its metrics. This prices them
with agents/pricing.py, so the numbers exist before token_usage has cost columns.

    DB_HOST=... DB_PORT=... DB_USER=... DB_PASS=... DB_DATABASE=... \
        python scripts/usage_from_runs.py [--days 30] [--agent support-agent]

Connects with default_transaction_read_only=on; it never writes.
"""

import argparse
import collections
import datetime
import os
import statistics
import sys
from pathlib import Path
from typing import Any, Counter, Dict, List

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents import pricing  # noqa: E402

FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")


def num(v) -> int:
    if isinstance(v, list):
        return sum(num(x) for x in v)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def entries_of(run: dict):
    """(model_type, model_id, tokens) per model the run used."""
    metrics = run.get("metrics") or {}
    model = run.get("model")
    out = []
    for model_type, models in (metrics.get("details") or {}).items():
        for m in models or []:
            out.append((model_type, m.get("id") or model, m))
    if not out and any(num(metrics.get(k)) for k in FIELDS):
        out.append(("model", model, metrics))
    return out


def model_requests(run: dict) -> int:
    return sum(1 for m in run.get("messages") or [] if m.get("role") == "assistant")


def tool_calls(run: dict) -> int:
    return len(run.get("tools") or [])


def history_messages(run: dict) -> int:
    return sum(1 for m in run.get("messages") or [] if m.get("from_history"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--agent", help="detail for one agent id (default: the most expensive)")
    args = ap.parse_args()
    since = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=args.days)).timestamp())

    conn = psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        dbname=os.environ["DB_DATABASE"],
        options="-c default_transaction_read_only=on",
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT table_schema, table_name FROM information_schema.columns "
        "WHERE column_name = 'run_data' AND table_schema NOT IN ('pg_catalog', 'information_schema')"
    )
    tables = cur.fetchall()
    if not tables:
        sys.exit("no agno runs tables (column run_data) found")

    per_agent: Dict[str, Dict[str, float]] = collections.defaultdict(
        lambda: {"runs": 0, "cost": 0.0, "unpriced": 0, "failed": 0, **{f: 0 for f in FIELDS}}
    )
    per_type: Dict[str, Dict[str, float]] = collections.defaultdict(lambda: collections.defaultdict(float))
    per_day: Dict[str, Dict[str, float]] = collections.defaultdict(lambda: collections.defaultdict(float))
    runs_by_agent: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    unpriced_models: Counter[str] = collections.Counter()

    for schema, table in tables:
        cur.execute(
            f'SELECT agent_id, team_id, status, created_at, run_data FROM "{schema}"."{table}" '
            "WHERE created_at >= %s AND parent_run_id IS NULL",
            (since,),
        )
        for agent_id, team_id, status, created_at, run in cur.fetchall():
            key = agent_id or team_id or run.get("agent_id") or f"{table}"
            ents = entries_of(run)
            if not ents:
                continue
            at = datetime.datetime.fromtimestamp(created_at, datetime.timezone.utc)
            cost, unpriced = pricing.cost_of([(m, t) for _, m, t in ents], at)
            a = per_agent[key]
            a["runs"] += 1
            a["cost"] += float(cost or 0)
            a["unpriced"] += bool(unpriced)
            a["failed"] += str(status or run.get("status") or "").lower() in ("error", "cancelled")
            unpriced_models.update(unpriced)
            for f in FIELDS:
                a[f] += sum(num(t.get(f)) for _, _, t in ents)
            for model_type, model_id, t in ents:
                c, _ = pricing.cost_of([(model_id, t)], at)
                per_type[key][f"{model_type}:{model_id}"] += float(c or 0)
            per_day[key][at.date().isoformat()] += float(cost or 0)
            runs_by_agent[key].append(
                {
                    "at": at,
                    "cost": float(cost or 0),
                    "in": sum(num(t.get("input_tokens")) for _, _, t in ents),
                    "cache": sum(num(t.get("cache_read_tokens")) for _, _, t in ents),
                    "out": sum(num(t.get("output_tokens")) for _, _, t in ents),
                    "req": model_requests(run),
                    "tools": tool_calls(run),
                    "hist": history_messages(run),
                    "session": run.get("session_id"),
                    "model": run.get("model"),
                }
            )

    if not per_agent:
        sys.exit(f"\nNo completed runs with token metrics in the last {args.days} days.")
    total = sum(a["cost"] for a in per_agent.values())
    print(f"\nLast {args.days} days, all agents: ${total:,.2f} over {sum(a['runs'] for a in per_agent.values())} runs")
    print(f"{'agent':34} {'cost':>10} {'share':>6} {'runs':>6} {'$/run':>8} {'in/run':>9} {'cache%':>7} {'out/run':>8}")
    for key, a in sorted(per_agent.items(), key=lambda kv: -kv[1]["cost"]):
        n = a["runs"]
        cache_share = a["cache_read_tokens"] / max(a["input_tokens"] + a["cache_read_tokens"], 1)
        print(
            f"{key[:34]:34} ${a['cost']:>9,.2f} {a['cost'] / max(total, 1e-9):>6.0%} {n:>6} ${a['cost'] / n:>7.3f} "
            f"{a['input_tokens'] // n:>9,} {cache_share:>7.0%} {a['output_tokens'] // n:>8,}"
            + (f"  ({a['unpriced']} unpriced)" if a["unpriced"] else "")
        )
    if unpriced_models:
        print("\nNot priced (excluded from the $):", dict(unpriced_models))

    focus = args.agent or max(per_agent, key=lambda k: per_agent[k]["cost"])
    if focus not in runs_by_agent:
        # runs may be stored under the agent's display name ("Support Agent") rather
        # than its id ("acme-support-agent"); accept either
        def slug(text: str) -> str:
            return "-".join(text.lower().replace("_", " ").replace("-", " ").split())

        focus = next((k for k in runs_by_agent if slug(focus).endswith(slug(k))), focus)
    runs = runs_by_agent.get(focus, [])
    if not runs:
        sys.exit(f"\nno runs for {focus}")
    print(f"\n== {focus} ==")
    print("cost by model type:")
    for k, v in sorted(per_type[focus].items(), key=lambda kv: -kv[1]):
        print(f"  {k:50} ${v:,.2f}")
    print("per run (median / p90 / max):")
    for label, field in (
        ("cost $", "cost"),
        ("input tokens", "in"),
        ("cached tokens", "cache"),
        ("output tokens", "out"),
        ("model requests", "req"),
        ("tool calls", "tools"),
        ("history msgs", "hist"),
    ):
        vals = sorted(r[field] for r in runs)
        p90 = vals[int(len(vals) * 0.9) - 1] if len(vals) >= 10 else vals[-1]
        fmt = (lambda x: f"{x:.3f}") if field == "cost" else (lambda x: f"{int(x):,}")
        print(f"  {label:16} {fmt(statistics.median(vals)):>10} {fmt(p90):>10} {fmt(vals[-1]):>10}")
    print("models:", dict(collections.Counter(r["model"] for r in runs)))
    print("cost per day:")
    for day, v in sorted(per_day[focus].items())[-14:]:
        print(f"  {day} ${v:,.2f}")
    print("10 most expensive runs:")
    for r in sorted(runs, key=lambda r: -r["cost"])[:10]:
        print(
            f"  {r['at']:%m-%d %H:%M} ${r['cost']:.3f} in={r['in']:,} cache={r['cache']:,} out={r['out']:,} "
            f"req={r['req']} tools={r['tools']} hist={r['hist']} session={str(r['session'])[:12]}"
        )


if __name__ == "__main__":
    main()
