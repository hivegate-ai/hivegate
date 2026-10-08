"""db/usage_queries.py and migration 011 against a real Postgres.

The queries use Postgres-only SQL (to_timestamp bucketing, JSONB), so they are run
against a throwaway database when USAGE_TEST_DATABASE_URL is set, e.g.

    docker run -d --name usage-pg -e POSTGRES_PASSWORD=pw -p 15432:5432 postgres:16
    USAGE_TEST_DATABASE_URL=postgresql+psycopg://postgres:pw@localhost:15432/postgres \\
        pytest tests/test_usage_queries_pg.py

The test starts from the token_usage table as it was before migration 011, applies the
migration, writes rows through api/services/usage.write_row, and reads them back.
"""

import datetime
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

URL = os.getenv("USAGE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="USAGE_TEST_DATABASE_URL not set")

MIGRATION = Path(__file__).resolve().parent.parent / "db" / "migrations" / "011_token_usage_cost.sql"
OLD_TABLE = """
CREATE TABLE token_usage (
    id SERIAL PRIMARY KEY, agent_id VARCHAR(255), session_id VARCHAR(255), user_id VARCHAR(255),
    model VARCHAR(100), prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0, is_estimated BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW())
"""


@pytest.fixture(scope="module")
def session_factory():
    engine = create_engine(URL)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS token_usage"))
        conn.execute(text(OLD_TABLE))
        conn.execute(
            text(
                "INSERT INTO token_usage (agent_id, model, prompt_tokens, created_at) "
                "VALUES ('legacy', 'm', 5, NOW() - INTERVAL '2 days')"
            )
        )
        for _ in range(2):  # idempotent; run whole, as db/migrations/run_migration.py does
            conn.exec_driver_sql(MIGRATION.read_text())
    yield sessionmaker(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS token_usage"))


def _run(agent, tenant, metrics, status_event=None, model="claude-sonnet-5-5"):
    from api.services import usage

    c = usage.UsageCollector(usage.UsageContext(agent_id=agent, tenant_id=tenant, api_key_name="test-key"))
    c.set_model(model, "Anthropic")
    c.observe({"event": "ModelRequestCompleted", "model": model, "input_tokens": metrics.get("input_tokens", 0)})
    if status_event:
        c.observe(status_event)
    else:
        c.observe({"event": "RunCompleted", "run_id": f"{agent}-run", "metrics": metrics})
    return c.build_row()


def test_rows_round_trip_and_every_query_runs(session_factory):
    from api.services import usage
    from db import usage_queries as q

    memory_split = {
        "input_tokens": 2_000_000,
        "output_tokens": 100_000,
        "details": {
            "model": [{"id": "claude-sonnet-5-5", "input_tokens": 1_000_000, "output_tokens": 100_000}],
            "memory_model": [{"id": "claude-sonnet-5-5", "input_tokens": 1_000_000}],
        },
    }
    rows = [
        _run("pf-budget-classifier", "family", memory_split),
        _run("pf-cash-manager", "demo", {"input_tokens": 1000, "output_tokens": 10}),
        _run("pf-budget-classifier", "family", {"input_tokens": 500_000}, {"event": "RunError", "content": "refusal"}),
        _run("pf-cash-manager", "demo", {"input_tokens": 10}, model="gemini-3-flash-preview"),
    ]
    ids = [usage.write_row(r, session_factory=session_factory) for r in rows]
    assert all(ids)

    db = session_factory()
    try:
        live = q.live(db, 0, 10)
        assert [r["agent_id"] for r in live][-4:] == [r["agent_id"] for r in rows]
        assert q.live(db, ids[1], 10)[0]["id"] == ids[2]

        totals = q.totals(db, "24h")
        assert totals["calls"] == 4  # the pre-migration row is 2 days old, outside the window
        assert totals["failed_calls"] == 1 and totals["unpriced_calls"] == 1
        assert totals["failed_cost_usd"] == pytest.approx(1.0)  # 500k on Sonnet 5.5 ($2/M)

        by_agent = {g["key"]: g for g in q.summary(db, "24h", "agent")}
        classifier = by_agent["pf-budget-classifier"]
        assert classifier["calls"] == 2
        assert classifier["cost_usd"] == pytest.approx(4.0 + 1.0 + 1.0)  # 2M in + 100k out + failed 500k

        split = {m["model_type"]: m for m in q.model_type_split(db, "24h")}
        assert split["memory_model"]["cost_usd"] == pytest.approx(2.0)

        points = q.timeseries(db, "24h", "5m", "agent", top=1)
        assert {p["key"] for p in points} == {"pf-budget-classifier", "(other)"}

        top = q.calls(db, "24h", order="cost", limit=2)
        assert top[0]["agent_id"] == "pf-budget-classifier"
        assert q.calls(db, "24h", status="refused")[0]["error"] == "refusal"
        assert q.calls(db, "24h", tenant="demo", order="recent")[0]["model"] == "gemini-3-flash-preview"
        assert q.calls(db, "24h", min_cost=5)[0]["cost_usd"] == pytest.approx(5.0)  # main $3 + memory $2
    finally:
        db.close()


def test_old_rows_still_read(session_factory):
    from db import usage_queries as q

    db = session_factory()
    try:
        db.execute(
            text("UPDATE token_usage SET created_at = :t WHERE agent_id = 'legacy'"), {"t": datetime.datetime.utcnow()}
        )
        db.commit()
        legacy = q.calls(db, "1h", agent="legacy")[0]
        assert legacy["status"] == "completed" and legacy["cost_usd"] is None and legacy["cache_read_tokens"] == 0
    finally:
        db.close()
