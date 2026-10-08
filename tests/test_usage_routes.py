"""The /usage API: who may read it, parameter validation, and the /v2 guard that keeps
read-only usage keys away from the agents."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import usage as usage_routes
from api.services import auth
from db.session import get_db


def key(*scopes):
    return SimpleNamespace(id=1, name="k", scopes=json.dumps(list(scopes)) if scopes else None)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    monkeypatch.setenv("ADMIN_SECRET", "s3cret")
    a = FastAPI()
    a.include_router(usage_routes.usage_router)
    a.dependency_overrides[get_db] = lambda: None
    with (
        patch.object(usage_routes.q, "live", return_value=[{"id": 5}]),
        patch.object(usage_routes.q, "totals", return_value={"calls": 1}),
        patch.object(usage_routes.q, "summary", return_value=[]),
        patch.object(usage_routes.q, "model_type_split", return_value=[]),
        patch.object(usage_routes.q, "timeseries", return_value=[]),
        patch.object(usage_routes.q, "calls", return_value=[]),
    ):
        yield a


def client_with_key(app, api_key):
    app.dependency_overrides[auth.get_optional_api_key] = lambda: api_key
    return TestClient(app)


def test_usage_read_key_and_admin_key_are_allowed(app):
    for k in (key("usage:read"), key("admin")):
        assert client_with_key(app, k).get("/usage/live").status_code == 200


def test_a_key_without_the_scope_is_forbidden(app):
    assert client_with_key(app, key("read", "write")).get("/usage/summary").status_code == 403
    assert client_with_key(app, key()).get("/usage/summary").status_code == 403


def test_admin_secret_is_allowed_and_nothing_is_unauthorized(app):
    c = client_with_key(app, None)
    assert c.get("/usage/live").status_code == 401
    assert c.get("/usage/live", headers={"X-Admin-Secret": "wrong"}).status_code == 401
    assert c.get("/usage/live", headers={"X-Admin-Secret": "s3cret"}).status_code == 200


def test_live_returns_the_last_id(app):
    body = client_with_key(app, key("usage:read")).get("/usage/live?after_id=3").json()
    assert body == {"calls": [{"id": 5}], "last_id": 5}
    usage_routes.q.live.assert_called_with(None, 3, 200)


def test_parameters_are_validated(app):
    c = client_with_key(app, key("usage:read"))
    assert c.get("/usage/summary?window=2y").status_code == 422
    assert c.get("/usage/summary?group_by=password").status_code == 422
    assert c.get("/usage/timeseries?bucket=1s").status_code == 422
    assert c.get("/usage/calls?min_cost=-1").status_code == 422
    assert c.get("/usage/summary?window=7d&group_by=tenant").status_code == 200


def test_prices_endpoint(app):
    body = client_with_key(app, key("usage:read")).get("/usage/prices").json()
    assert body["prices"]["claude-sonnet-5-5"]["input"] == 2.0


@pytest.mark.parametrize(
    "scopes, allowed",
    [((), True), (("read", "write"), True), (("usage:read", "write"), True), (("usage:read",), False)],
)
def test_v2_refuses_keys_that_can_only_read_usage(scopes, allowed):
    import asyncio

    k = key(*scopes)
    if allowed:
        assert asyncio.run(auth.reject_usage_only_keys(k)) is None
    else:
        with pytest.raises(Exception) as exc:
            asyncio.run(auth.reject_usage_only_keys(k))
        assert getattr(exc.value, "status_code", None) == 403
    assert asyncio.run(auth.reject_usage_only_keys(None)) is None
