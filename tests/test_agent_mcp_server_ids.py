"""Agents referencing registered MCP servers by id (#91, PR 2).

Covers resolving ids for chat, attaching servers to an agent, the 400 for unknown ids, the
409 for deleting a server an agent uses, and the shared connect helper. Same in-memory
SQLite + real Fernet key setup as test_mcp_server_registry.
"""

import asyncio
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ["TESTING"] = "true"

from fastapi import HTTPException
from fastapi.testclient import TestClient

from agents.agent import build_mcp_toolkits, connect_mcp_toolkits
from db.agent_info_crud import (
    AgentConfig,
    create_agent_info,
    get_agent_config,
    get_agent_info,
    get_agents_using_mcp_server,
)
from db.mcp_server_crud import (
    UnknownMCPServersError,
    create_mcp_server,
    mcp_server_configs_for_chat,
    soft_delete_mcp_server,
)
from db.session import get_db
from supervisor.models import MCPServerConfig, WorkerConfig
from tests.test_mcp_server_registry import _RegistryTestBase


class _AgentRefsBase(_RegistryTestBase):
    def _server(self, server_id, headers=None, transport="streamable-http"):
        return create_mcp_server(
            self.db, server_id, server_id.upper(), f"https://{server_id}.example.com/mcp", transport, headers=headers
        )

    def _agent(self, agent_id, mcp_server_ids=(), **config):
        create_agent_info(
            self.db,
            agent_id,
            agent_id,
            f"p_{agent_id}",
            config=AgentConfig(mcp_server_ids=list(mcp_server_ids), **config),
        )


class TestResolution(_AgentRefsBase):
    def test_configs_in_order_with_decrypted_headers(self):
        self._server("crm", headers={"Authorization": "Bearer one"})
        self._server("docs", transport="http")
        configs = mcp_server_configs_for_chat(self.db, ["docs", "crm"])
        self.assertEqual([c.name for c in configs], ["docs", "crm"])
        self.assertEqual(configs[0].type, "http")
        self.assertEqual(configs[0].headers, {})
        self.assertEqual(configs[1].headers, {"Authorization": "Bearer one"})
        self.assertEqual(configs[1].url, "https://crm.example.com/mcp")

    def test_unknown_and_deleted_ids_are_all_named(self):
        self._server("crm")
        self._server("gone")
        soft_delete_mcp_server(self.db, "gone")
        with self.assertRaises(UnknownMCPServersError) as ctx:
            mcp_server_configs_for_chat(self.db, ["crm", "gone", "never"])
        self.assertEqual(ctx.exception.server_ids, ["gone", "never"])

    def test_chat_config_merges_embedded_and_registered(self):
        from api.routes.v2.agents import _with_registered_mcp_servers

        self._server("crm", headers={"Authorization": "Bearer one"})
        embedded = MCPServerConfig(name="pf", type="http", url="https://pf.example.com/mcp")
        config = AgentConfig(worker_config=WorkerConfig(mcp_servers=[embedded]), mcp_server_ids=["crm"])
        merged = _with_registered_mcp_servers(self.db, "a", config)
        self.assertEqual([s.name for s in merged.worker_config.mcp_servers], ["pf", "crm"])
        self.assertEqual(config.worker_config.mcp_servers, [embedded])  # the stored config is untouched
        self.assertEqual(len(build_mcp_toolkits(merged)), 2)

        # No worker_config at all: registered servers alone.
        merged = _with_registered_mcp_servers(self.db, "a", AgentConfig(mcp_server_ids=["crm"]))
        [toolkit] = build_mcp_toolkits(merged)
        self.assertEqual(toolkit.server_params.headers, {"Authorization": "Bearer one"})

    def test_chat_config_passthrough_without_ids(self):
        from api.routes.v2.agents import _with_registered_mcp_servers

        db = MagicMock()
        self.assertIsNone(_with_registered_mcp_servers(db, "a", None))
        config = AgentConfig()
        self.assertIs(_with_registered_mcp_servers(db, "a", config), config)
        db.query.assert_not_called()

    def test_chat_config_errors(self):
        from api.routes.v2.agents import _with_registered_mcp_servers
        from api.services.token_security import TokenSecurityError

        with self.assertRaises(HTTPException) as ctx:
            _with_registered_mcp_servers(self.db, "a", AgentConfig(mcp_server_ids=["never"]))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("never", ctx.exception.detail)

        self._server("crm", headers={"A": "b"})
        with patch("db.mcp_server_crud.get_token_encryption", side_effect=TokenSecurityError("no key")):
            with self.assertRaises(HTTPException) as ctx:
                _with_registered_mcp_servers(self.db, "a", AgentConfig(mcp_server_ids=["crm"]))
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("SECRET_TOKEN_ENC_KEY", ctx.exception.detail)

    def test_agents_using_a_server(self):
        self._server("crm")
        self._agent("b", ["crm"])
        self._agent("a", ["crm", "other"])
        self._agent("c")
        self._agent("deleted", ["crm"])
        get_agent_info(self.db, "deleted").is_active = False
        self.db.commit()
        self.assertEqual(get_agents_using_mcp_server(self.db, "crm"), ["a", "b"])
        self.assertEqual(get_agents_using_mcp_server(self.db, "nope"), [])


class TestRoutes(_AgentRefsBase):
    def setUp(self):
        super().setUp()
        from api.routes.v2_router import get_v2_router
        from tests.test_utils import create_test_client

        _, app = create_test_client()
        app.include_router(get_v2_router())

        def sqlite_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = sqlite_db
        self.client = TestClient(app)

    def _config(self, agent_id):
        self.db.expire_all()
        return get_agent_config(get_agent_info(self.db, agent_id))

    def test_attach_detach_keeps_other_config(self):
        self._server("crm")
        self._server("docs")
        self._agent("bot", num_history_runs=7)
        response = self.client.put("/v2/agents/bot/mcp-servers", json={"mcp_server_ids": ["crm", "docs", "crm"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"agent_id": "bot", "mcp_server_ids": ["crm", "docs"]})
        config = self._config("bot")
        self.assertEqual(config.mcp_server_ids, ["crm", "docs"])
        self.assertEqual(config.num_history_runs, 7)

        self.assertEqual(self.client.put("/v2/agents/bot/mcp-servers", json={"mcp_server_ids": []}).status_code, 200)
        self.assertEqual(self._config("bot").mcp_server_ids, [])

    def test_attach_rejects_unknown_deleted_and_malformed_ids(self):
        self._server("crm")
        self._server("gone")
        soft_delete_mcp_server(self.db, "gone")
        self._agent("bot", ["crm"])
        response = self.client.put("/v2/agents/bot/mcp-servers", json={"mcp_server_ids": ["crm", "gone", "never"]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("gone, never", response.json()["detail"])
        for bad in (["Has-Caps"], ["a b"], [f"s{i}" for i in range(21)]):
            with self.subTest(bad=bad):
                response = self.client.put("/v2/agents/bot/mcp-servers", json={"mcp_server_ids": bad})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self._config("bot").mcp_server_ids, ["crm"])  # unchanged
        self.assertEqual(self.client.put("/v2/agents/nope/mcp-servers", json={"mcp_server_ids": []}).status_code, 404)

    def test_create_agent_with_server_ids(self):
        self._server("crm")
        body = {"id": "bot", "name": "Bot", "template": "Hi", "config": {"mcp_server_ids": ["crm"]}}
        with patch("api.routes.v2.agents.get_prompt_storage") as storage:
            response = self.client.post("/v2/agents", json={**body, "config": {"mcp_server_ids": ["never"]}})
            self.assertEqual(response.status_code, 400)
            self.assertIn("never", response.json()["detail"])
            storage.assert_not_called()  # refused before the prompt is written

            response = self.client.post("/v2/agents", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self._config("bot").mcp_server_ids, ["crm"])

    def test_cannot_delete_a_server_in_use(self):
        self._server("crm", headers={"A": "b"})
        self._agent("bot", ["crm"])
        self._agent("other", ["crm"])
        response = self.client.delete("/v2/mcp-servers/crm")
        self.assertEqual(response.status_code, 409)
        self.assertIn("bot, other", response.json()["detail"])
        self.assertEqual(self.client.get("/v2/mcp-servers/crm").json()["header_names"], ["A"])  # untouched

        self.client.put("/v2/agents/bot/mcp-servers", json={"mcp_server_ids": []})
        self.client.put("/v2/agents/other/mcp-servers", json={"mcp_server_ids": []})
        self.assertEqual(self.client.delete("/v2/mcp-servers/crm").status_code, 204)

    def test_one_server_shared_by_two_agents_changes_in_one_place(self):
        """The issue's 'done when': create once, attach to two agents, change in one place."""
        from api.routes.v2.agents import _with_registered_mcp_servers

        self.client.post("/v2/mcp-servers", json={"id": "crm", "name": "CRM", "url": "https://old.example.com/mcp"})
        self._agent("a")
        self._agent("b")
        for agent_id in ("a", "b"):
            self.client.put(f"/v2/agents/{agent_id}/mcp-servers", json={"mcp_server_ids": ["crm"]})
        self.client.put("/v2/mcp-servers/crm", json={"url": "https://new.example.com/mcp"})
        for agent_id in ("a", "b"):
            merged = _with_registered_mcp_servers(self.db, agent_id, self._config(agent_id))
            self.assertEqual([s.url for s in merged.worker_config.mcp_servers], ["https://new.example.com/mcp"])


class _Toolkit:
    def __init__(self, fail=False, empty=False):
        self.fail, self.empty = fail, empty
        self._initialized = False
        self.functions: dict = {}
        self.exited = False

    async def __aenter__(self):
        if self.fail:
            raise ConnectionError("boom")
        self._initialized = True
        self.functions = {} if self.empty else {"tool": object()}
        return self

    async def __aexit__(self, *exc):
        self.exited = True
        return False


class TestConnectHelper(unittest.TestCase):
    def _use(self, toolkits):
        async def run():
            async with connect_mcp_toolkits(toolkits) as connected:
                return connected

        return asyncio.run(run())

    def test_connects_all_and_closes_on_exit(self):
        toolkits = [_Toolkit(), _Toolkit()]
        self.assertEqual(self._use(toolkits), toolkits)
        self.assertTrue(all(t.exited for t in toolkits))

    def test_failure_closes_the_ones_already_open(self):
        first = _Toolkit()
        with self.assertRaises(ConnectionError):
            self._use([first, _Toolkit(fail=True)])
        self.assertTrue(first.exited)

    def test_no_tools_is_an_error(self):
        toolkit = _Toolkit(empty=True)
        with self.assertRaises(RuntimeError):
            self._use([toolkit])
        self.assertTrue(toolkit.exited)


if __name__ == "__main__":
    unittest.main()
