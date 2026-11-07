# Run against a disposable PostgreSQL database with AGENT_SESSION_TEST_DATABASE_URL.
# Each test owns a randomly named schema; no existing tables are changed.
import os
from time import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agno.agent import Agent
from agno.agent._storage import read_or_create_session
from agno.db.postgres import PostgresDb
from agno.memory import UserMemory
from agno.models.message import Message
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from agno.session import AgentSession
from sqlalchemy import MetaData, Table, create_engine, func, select
from sqlalchemy.schema import CreateSchema, DropSchema

from agents import agent as agent_module
from api.routes.v2 import agents as agents_routes
from api.services.models import PullPromptResponse
from tests.test_utils import create_test_client

DATABASE_URL = os.getenv("AGENT_SESSION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="AGENT_SESSION_TEST_DATABASE_URL not set")
AGENT_ID = "assistant-v2"
TABLE = "a_support_helper_agent"
CLEAR_URL = f"/v2/agents/{AGENT_ID}/session/clear"


def _assert_history(storage: PostgresDb, session_id: str, user_id: str | None = None) -> None:
    session = storage.get_session(session_id, user_id=user_id)
    assert isinstance(session, AgentSession)
    assert session.runs


@pytest.fixture
def persisted_sessions(monkeypatch):
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    schema = f"session_clear_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    original_postgres_db = PostgresDb
    agent_module.get_agent_db.cache_clear()
    monkeypatch.setattr(agent_module, "db_engine", engine)
    monkeypatch.setattr(
        agent_module,
        "PostgresDb",
        lambda **kwargs: original_postgres_db(db_schema=schema, **kwargs),
    )
    monkeypatch.setenv("PROMPT_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(
        agents_routes,
        "get_agent_info",
        lambda db, agent_id: SimpleNamespace(id=AGENT_ID, prompt_service_id="support-prompt"),
    )
    monkeypatch.setattr(
        agents_routes,
        "_get_prompt_from_local_storage",
        lambda db, prompt_id: PullPromptResponse(name="Support Helper", template="Help the user."),
    )
    client, app = create_test_client()
    app.include_router(agents_routes.v2_agents_router, prefix="/v2")

    def seed(table, session_id, user_id, prompt_name=None):
        storage = agent_module.get_agent_db(table)
        prompt_name = prompt_name or ("Support Helper" if table == TABLE else "Other Agent")
        storage.upsert_session(
            AgentSession(session_id=session_id, agent_id=prompt_name, user_id=user_id, created_at=int(time()))
        )
        storage.upsert_run(
            RunOutput(
                run_id=f"{table}-{session_id}",
                agent_id=prompt_name,
                session_id=session_id,
                user_id=user_id,
                status=RunStatus.completed,
                messages=[Message(role="user", content=f"Private history for {user_id}/{session_id}")],
            ),
            session_id=session_id,
            user_id=user_id,
            run_index=0,
        )
        return storage

    def count_rows(table):
        reflected = Table(table, MetaData(), schema=schema, autoload_with=engine)
        with engine.connect() as connection:
            return connection.scalar(select(func.count()).select_from(reflected))

    try:
        yield SimpleNamespace(client=client, seed=seed, count_rows=count_rows, schema=schema, engine=engine)
    finally:
        client.close()
        agent_module.get_agent_db.cache_clear()
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def test_clear_removes_persisted_history_and_preserves_other_users_and_agents(persisted_sessions):
    fixture = persisted_sessions
    storage = fixture.seed(TABLE, "same-session", "alice")
    fixture.seed(TABLE, "alice-other-session", "alice")
    fixture.seed(TABLE, "bob-session", "bob")
    fixture.seed("a_other_agent", "same-session", "alice")
    storage.upsert_user_memory(UserMemory(memory_id="keep-memory", memory="A separate preference", user_id="alice"))

    # Exercise the same database instance's deserialized run cache before clearing.
    agent = Agent(id="Support Helper", db=storage, user_id="alice", session_id="same-session", telemetry=False)
    assert read_or_create_session(agent, "same-session", "alice").runs

    response = fixture.client.post(CLEAR_URL, json={"session_id": "same-session", "user_id": "alice"})

    assert response.status_code == 200
    assert not read_or_create_session(agent, "same-session", "alice").runs
    agent_module.get_agent_db.cache_clear()
    reopened = agent_module.get_agent_db(TABLE)
    assert reopened.get_session("same-session") is None
    assert fixture.count_rows(TABLE) == 2
    assert fixture.count_rows(f"{TABLE}_runs") == 2
    _assert_history(reopened, "alice-other-session")
    _assert_history(reopened, "bob-session")
    _assert_history(agent_module.get_agent_db("a_other_agent"), "same-session")
    assert fixture.count_rows("a_other_agent_runs") == 1
    assert reopened.get_user_memory("keep-memory", user_id="alice") is not None

    missing = fixture.client.post(CLEAR_URL, json={"session_id": "same-session", "user_id": "alice"})
    assert missing.status_code == 404


def test_user_only_clear_deletes_only_that_users_persisted_sessions(persisted_sessions):
    fixture = persisted_sessions
    fixture.seed(TABLE, "alice-one", "alice")
    fixture.seed(TABLE, "alice-two", "alice")
    fixture.seed(TABLE, "bob-session", "bob")
    fixture.seed("a_other_agent", "alice-one", "alice")

    response = fixture.client.post(CLEAR_URL, json={"user_id": "alice"})

    assert response.status_code == 200
    agent_module.get_agent_db.cache_clear()
    reopened = agent_module.get_agent_db(TABLE)
    assert reopened.get_session("alice-one") is None
    assert reopened.get_session("alice-two") is None
    _assert_history(reopened, "bob-session")
    assert fixture.count_rows(TABLE) == fixture.count_rows(f"{TABLE}_runs") == 1
    _assert_history(agent_module.get_agent_db("a_other_agent"), "alice-one")
    assert fixture.client.post(CLEAR_URL, json={"user_id": "alice"}).status_code == 404


@pytest.mark.parametrize("payload", [{"session_id": "alice-session", "user_id": "bob"}, {}])
def test_wrong_owner_or_missing_selector_preserves_all_persisted_history(persisted_sessions, payload):
    fixture = persisted_sessions
    fixture.seed(TABLE, "alice-session", "alice")

    response = fixture.client.post(CLEAR_URL, json=payload)

    assert response.status_code == (404 if payload else 400)
    agent_module.get_agent_db.cache_clear()
    _assert_history(agent_module.get_agent_db(TABLE), "alice-session", "alice")
    assert fixture.count_rows(TABLE) == fixture.count_rows(f"{TABLE}_runs") == 1


def test_partial_clear_reports_committed_deletions_and_can_be_retried(persisted_sessions, monkeypatch):
    fixture = persisted_sessions
    storage = fixture.seed(TABLE, "alice-one", "alice")
    fixture.seed(TABLE, "alice-two", "alice")
    fixture.seed(TABLE, "alice-three", "alice")
    fixture.seed(TABLE, "bob-session", "bob")
    delete_session = storage.delete_session
    deleted_ids: list[str] = []

    def fail_after_first_delete(session_id, user_id=None):
        if deleted_ids:
            raise RuntimeError("Synthetic storage failure")
        result = delete_session(session_id=session_id, user_id=user_id)
        if result:
            deleted_ids.append(session_id)
        return result

    monkeypatch.setattr(storage, "delete_session", fail_after_first_delete)
    response = fixture.client.post(CLEAR_URL, json={"user_id": "alice"})

    assert response.status_code == 500
    assert "1 session(s) already deleted" in response.json()["detail"]
    assert len(deleted_ids) == 1
    assert storage.get_session(deleted_ids[0]) is None
    assert fixture.count_rows(TABLE) == fixture.count_rows(f"{TABLE}_runs") == 3

    monkeypatch.setattr(storage, "delete_session", delete_session)
    retry = fixture.client.post(CLEAR_URL, json={"user_id": "alice"})
    assert retry.status_code == 200
    assert fixture.count_rows(TABLE) == fixture.count_rows(f"{TABLE}_runs") == 1
    _assert_history(storage, "bob-session")


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "foreign-session"},
        {"session_id": "foreign-session", "user_id": "alice"},
        {"user_id": "alice"},
    ],
)
def test_prompt_names_sharing_a_table_do_not_clear_each_others_history(persisted_sessions, payload):
    fixture = persisted_sessions
    fixture.seed(TABLE, "own-session", "alice")
    fixture.seed(TABLE, "foreign-session", "alice", prompt_name="support-helper")

    response = fixture.client.post(CLEAR_URL, json=payload)

    assert response.status_code == (404 if "session_id" in payload else 200)
    agent_module.get_agent_db.cache_clear()
    reopened = agent_module.get_agent_db(TABLE)
    _assert_history(reopened, "foreign-session", "alice")
    if "session_id" in payload:
        _assert_history(reopened, "own-session", "alice")
        assert fixture.count_rows(TABLE) == fixture.count_rows(f"{TABLE}_runs") == 2
    else:
        assert reopened.get_session("own-session") is None
        assert fixture.count_rows(TABLE) == fixture.count_rows(f"{TABLE}_runs") == 1
