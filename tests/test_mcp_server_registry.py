"""MCP server registry (#91): CRUD, header encryption, and the /v2/mcp-servers routes.

Runs against an in-memory SQLite database with a real Fernet key, so encryption and the
soft-delete/reactivate behaviour are exercised end to end rather than mocked.
"""

import datetime
import logging
import os
import unittest
from typing import Any
from unittest.mock import patch

os.environ["TESTING"] = "true"

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.services.token_security import TokenEncryption, TokenSecurityError
from db.db_models import MCPServerDB
from db.mcp_server_crud import (
    create_mcp_server,
    get_all_mcp_servers,
    get_mcp_server,
    get_mcp_server_header_names,
    get_mcp_server_headers,
    soft_delete_mcp_server,
    update_mcp_server,
)
from db.session import get_db


class _RegistryTestBase(unittest.TestCase):
    engine: Any = None
    SessionLocal: Any = None

    @classmethod
    def setUpClass(cls):  # type: ignore[override]
        cls.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        MCPServerDB.__table__.create(bind=cls.engine, checkfirst=True)  # type: ignore[attr-defined]
        cls.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=cls.engine)

    @classmethod
    def tearDownClass(cls):
        MCPServerDB.__table__.drop(bind=cls.engine, checkfirst=True)  # type: ignore[attr-defined]

    def setUp(self):
        self.encryption = TokenEncryption(Fernet.generate_key().decode())
        patcher = patch("db.mcp_server_crud.get_token_encryption", return_value=self.encryption)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = self.SessionLocal()
        self.db.query(MCPServerDB).delete()
        self.db.commit()

    def tearDown(self):
        self.db.close()


class TestMCPServerCRUD(_RegistryTestBase):
    def test_create_without_headers(self):
        server = create_mcp_server(self.db, "docs", "Docs", "https://mcp.example.com/mcp")
        self.assertEqual(server.auth_type, "none")
        self.assertIsNone(server.encrypted_headers)
        self.assertIsNone(server.tenant_id)
        self.assertEqual(server.transport, "streamable-http")
        self.assertEqual(get_mcp_server_headers(server), {})

    def test_headers_are_encrypted_at_rest(self):
        secret = "Bearer sk-very-secret"
        server = create_mcp_server(
            self.db, "crm", "CRM", "https://crm.example.com/mcp", headers={"Authorization": secret, "X-Org": "acme"}
        )
        self.assertEqual(server.auth_type, "static_headers")
        self.assertNotIn(secret, server.encrypted_headers)
        self.assertNotIn("acme", server.encrypted_headers)
        self.assertEqual(get_mcp_server_header_names(server), ["Authorization", "X-Org"])
        self.assertEqual(get_mcp_server_headers(server), {"Authorization": secret, "X-Org": "acme"})

    def test_headers_unreadable_with_another_key(self):
        server = create_mcp_server(self.db, "crm", "CRM", "https://crm.example.com/mcp", headers={"A": "b"})
        other = TokenEncryption(Fernet.generate_key().decode())
        with patch("db.mcp_server_crud.get_token_encryption", return_value=other):
            with self.assertRaises(Exception):
                get_mcp_server_headers(server)

    def test_update_headers_keep_replace_clear(self):
        create_mcp_server(self.db, "crm", "CRM", "https://crm.example.com/mcp", headers={"Authorization": "one"})

        server = update_mcp_server(self.db, "crm", name="CRM 2")
        self.assertEqual(get_mcp_server_headers(server), {"Authorization": "one"})  # None keeps

        server = update_mcp_server(self.db, "crm", headers={"X-Key": "two"})
        self.assertEqual(get_mcp_server_headers(server), {"X-Key": "two"})  # dict replaces
        self.assertEqual(get_mcp_server_header_names(server), ["X-Key"])

        server = update_mcp_server(self.db, "crm", headers={})
        self.assertEqual(server.auth_type, "none")  # {} clears
        self.assertIsNone(server.encrypted_headers)
        self.assertEqual(get_mcp_server_header_names(server), [])

    def test_update_missing_returns_none(self):
        self.assertIsNone(update_mcp_server(self.db, "nope", name="x"))

    def test_soft_delete_and_reactivate(self):
        create_mcp_server(self.db, "crm", "CRM", "https://old.example.com/mcp", headers={"A": "old"})
        self.assertTrue(soft_delete_mcp_server(self.db, "crm"))
        self.assertIsNone(get_mcp_server(self.db, "crm"))
        self.assertFalse(soft_delete_mcp_server(self.db, "crm"))
        self.assertEqual(get_all_mcp_servers(self.db), [])
        self.assertEqual(len(get_all_mcp_servers(self.db, include_inactive=True)), 1)

        # Re-creating the id reactivates the row and overwrites everything, old headers included.
        server = create_mcp_server(self.db, "crm", "CRM new", "https://new.example.com/mcp")
        self.assertTrue(server.is_active)
        self.assertEqual(server.url, "https://new.example.com/mcp")
        self.assertEqual(server.auth_type, "none")
        self.assertEqual(get_mcp_server_headers(server), {})
        self.assertEqual(len(get_all_mcp_servers(self.db, include_inactive=True)), 1)

    def test_header_values_never_reach_the_logs(self):
        # TokenEncryption.encrypt_token_data logs a prefix of keys with these names.
        headers = {"api_key": "sk-live-SECRET1", "token": "tok-SECRET2", "access_token": "at-SECRET3"}
        with self.assertLogs(level=logging.DEBUG) as logs:
            server = create_mcp_server(self.db, "crm", "CRM", "https://crm.example.com/mcp", headers=headers)
            self.assertEqual(get_mcp_server_headers(server), headers)
            update_mcp_server(self.db, "crm", headers=headers)
        joined = "\n".join(logs.output)
        for prefix in ("sk-live", "tok-SEC", "at-SECR", "SECRET"):
            self.assertNotIn(prefix, joined)

    def test_delete_wipes_headers(self):
        create_mcp_server(self.db, "crm", "CRM", "https://crm.example.com/mcp", headers={"Authorization": "leaked"})
        soft_delete_mcp_server(self.db, "crm")
        row = self.db.query(MCPServerDB).filter(MCPServerDB.id == "crm").one()
        self.assertFalse(row.is_active)
        self.assertEqual(row.auth_type, "none")
        self.assertIsNone(row.encrypted_headers)
        self.assertIsNone(row.header_names)

    def test_reactivation_resets_created_at(self):
        server = create_mcp_server(self.db, "crm", "CRM", "https://crm.example.com/mcp")
        server.created_at = datetime.datetime(2020, 1, 1)
        self.db.commit()
        soft_delete_mcp_server(self.db, "crm")
        server = create_mcp_server(self.db, "crm", "CRM", "https://crm.example.com/mcp")
        self.assertGreater(server.created_at, datetime.datetime(2021, 1, 1))

    def test_description_unset_keeps_none_clears(self):
        create_mcp_server(self.db, "crm", "CRM", "https://crm.example.com/mcp", description="old")
        self.assertEqual(update_mcp_server(self.db, "crm", name="x").description, "old")
        self.assertIsNone(update_mcp_server(self.db, "crm", description=None).description)

    def test_list_is_ordered_by_id(self):
        create_mcp_server(self.db, "zeta", "Z", "https://z.example.com/mcp")
        create_mcp_server(self.db, "alpha", "A", "https://a.example.com/mcp")
        self.assertEqual([s.id for s in get_all_mcp_servers(self.db)], ["alpha", "zeta"])


class TestMCPServerRoutes(_RegistryTestBase):
    def setUp(self):
        super().setUp()
        from tests.test_utils import create_test_client

        _, app = create_test_client()
        from api.routes.v2_router import get_v2_router

        app.include_router(get_v2_router())

        def sqlite_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = sqlite_db
        self.client = TestClient(app)

    def _create(self, **overrides):
        body = {"id": "crm", "name": "CRM", "url": "https://crm.example.com/mcp"}
        body.update(overrides)
        return self.client.post("/v2/mcp-servers", json=body)

    def test_create_and_get_never_return_header_values(self):
        response = self._create(headers={"Authorization": "Bearer sk-secret"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("sk-secret", response.text)
        data = response.json()
        self.assertEqual(data["auth_type"], "static_headers")
        self.assertEqual(data["header_names"], ["Authorization"])
        self.assertIsNone(data["tenant_id"])

        for path in ("/v2/mcp-servers/crm", "/v2/mcp-servers"):
            got = self.client.get(path)
            self.assertEqual(got.status_code, 200)
            self.assertNotIn("sk-secret", got.text)
            self.assertNotIn("encrypted_headers", got.text)

    def test_duplicate_is_409(self):
        self.assertEqual(self._create().status_code, 201)
        self.assertEqual(self._create().status_code, 409)

    def test_transport_is_normalised(self):
        response = self._create(transport=" HTTP ")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["transport"], "http")

    def test_rejects_bad_input(self):
        cases = [
            {"id": "Has-Caps"},
            {"id": "-leading-dash"},
            {"id": "a" * 64},
            {"id": "has space"},
            {"url": "ftp://crm.example.com/mcp"},
            {"url": "/relative/mcp"},
            {"url": "not a url"},
            {"transport": "stdio"},
            {"transport": "sse"},
            {"headers": {"Bad Name": "x"}},
            {"headers": {"Authorization": ""}},
            {"headers": {"Authorization": 123}},
            {"headers": ["Authorization", "x"]},
            {"headers": {"Authorization": "a\r\nX-Injected: 1"}},
            {"headers": {"X-Key": "a", "x-key": "b"}},
            {"headers": {f"H{i}": "v" for i in range(21)}},
            {"headers": {"Authorization": "Bearer tok\u20acn"}},
            {"headers": {"Authorization": "Bearer x "}},
            {"headers": {"Authorization": " Bearer x"}},
            {"headers": {"Authorization": "a\tb"}},
            {"headers": {"Mcp-Session-Id": "pinned"}},
            {"headers": {"content-length": "0"}},
            {"headers": {"Host": "evil.example.com"}},
            {"headers": {"Accept": "text/plain"}},
            {"url": "https://user:pass@crm.example.com/mcp"},
            {"url": "https://token@crm.example.com/mcp"},
            {"url": "https:///mcp"},
            {"name": ""},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertEqual(self._create(**overrides).status_code, 422)
        self.assertEqual(self.client.get("/v2/mcp-servers").json(), [])

    def test_rejected_header_value_is_not_echoed(self):
        response = self._create(headers={"Authorization": "Bearer sk-SECRET "})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("sk-SECRET", response.text)

    def test_query_string_url_is_allowed(self):
        response = self._create(url="https://mcp.example.com/mcp?workspace=1")
        self.assertEqual(response.status_code, 201, response.text)

    def test_concurrent_duplicate_is_409_not_500(self):
        self.assertEqual(self._create().status_code, 201)
        # Simulate losing the race: the existence check misses the row the other request wrote.
        with patch("api.routes.v2.mcp_servers.get_mcp_server", return_value=None):
            response = self._create()
        self.assertEqual(response.status_code, 409, response.text)

    def test_missing_encryption_key_is_named(self):
        with patch("db.mcp_server_crud.get_token_encryption", side_effect=TokenSecurityError("no key")):
            response = self._create(headers={"Authorization": "x"})
            self.assertEqual(response.status_code, 500)
            self.assertIn("SECRET_TOKEN_ENC_KEY", response.json()["detail"])
            self.assertEqual(self.client.get("/v2/mcp-servers").json(), [])  # rolled back

            self.assertEqual(self._create().status_code, 201)  # no headers, no key needed
            response = self.client.put("/v2/mcp-servers/crm", json={"headers": {"A": "b"}})
            self.assertEqual(response.status_code, 500)
            self.assertIn("SECRET_TOKEN_ENC_KEY", response.json()["detail"])

    def test_update_nulls(self):
        self._create(description="old", headers={"Authorization": "one"})
        response = self.client.put("/v2/mcp-servers/crm", json={"name": "CRM 2"})
        self.assertEqual(response.json()["description"], "old")  # omitted keeps
        response = self.client.put("/v2/mcp-servers/crm", json={"description": None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["description"])  # null clears
        for field in ("name", "url", "transport", "headers"):
            with self.subTest(field=field):
                self.assertEqual(self.client.put("/v2/mcp-servers/crm", json={field: None}).status_code, 422)
        self.assertEqual(self.client.get("/v2/mcp-servers/crm").json()["header_names"], ["Authorization"])

    def test_update(self):
        self._create(headers={"Authorization": "one"})
        response = self.client.put("/v2/mcp-servers/crm", json={"url": "https://new.example.com/mcp"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["url"], "https://new.example.com/mcp")
        self.assertEqual(response.json()["header_names"], ["Authorization"])

        response = self.client.put("/v2/mcp-servers/crm", json={"headers": {}})
        self.assertEqual(response.json()["auth_type"], "none")

        self.assertEqual(self.client.put("/v2/mcp-servers/crm", json={"url": "ftp://x"}).status_code, 422)
        bad = self.client.put("/v2/mcp-servers/crm", json={"headers": {"Authorization": "sk-SECRET "}})
        self.assertEqual(bad.status_code, 422)
        self.assertNotIn("sk-SECRET", bad.text)
        self.assertEqual(self.client.put("/v2/mcp-servers/nope", json={"name": "x"}).status_code, 404)

    def test_delete(self):
        self._create(headers={"Authorization": "x"})
        self.assertEqual(self.client.delete("/v2/mcp-servers/crm").status_code, 204)
        self.assertEqual(self.client.get("/v2/mcp-servers/crm").status_code, 404)
        self.assertEqual(self.client.delete("/v2/mcp-servers/crm").status_code, 404)
        listed = self.client.get("/v2/mcp-servers?include_inactive=true").json()
        self.assertEqual([(s["id"], s["is_active"]) for s in listed], [("crm", False)])
        self.assertEqual(listed[0]["header_names"], [])  # wiped on delete
        # The id can be registered again after a delete.
        self.assertEqual(self._create().status_code, 201)


if __name__ == "__main__":
    unittest.main()
