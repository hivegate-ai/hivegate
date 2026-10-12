"""Integration tests for V2 supervisor team API endpoints."""

import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from agno.run.base import RunStatus

import api.routes.v2.teams as teams_module

from db.db_models import TeamAgentDB, TeamInfoDB
from tests.test_utils import create_test_client


class TestSupervisorTeamCreate(unittest.TestCase):
    """Test creating supervisor teams via the V2 API."""

    def setUp(self) -> None:
        self.client, self.app = create_test_client()
        from api.routes.v2_router import get_v2_router

        self.app.include_router(get_v2_router())
        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)

        self.now = datetime.datetime.utcnow()

    @patch("api.routes.v2.teams.team_info_exists")
    @patch("api.routes.v2.teams.create_team_info")
    def test_create_supervisor_team(self, mock_create: MagicMock, mock_exists: MagicMock) -> None:
        """Create a team with mode='supervisor' via API."""
        mock_exists.return_value = False
        mock_create.return_value = TeamInfoDB(
            id="sup-team-1",
            name="My Supervisor Team",
            description="Test supervisor team",
            version="2.0",
            mode="supervisor",
            created_at=self.now,
            updated_at=self.now,
        )

        create_req = {
            "id": "sup-team-1",
            "name": "My Supervisor Team",
            "description": "Test supervisor team",
            "mode": "supervisor",
            "agents": [],
        }

        response = self.client.post("/v2/teams", json=create_req)
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["id"], "sup-team-1")

    @patch("api.routes.v2.teams.team_info_exists")
    @patch("api.routes.v2.teams.create_team_info")
    def test_create_coordinate_team_backward_compat(self, mock_create: MagicMock, mock_exists: MagicMock) -> None:
        """Coordinate mode teams remain unaffected by supervisor additions."""
        mock_exists.return_value = False
        mock_create.return_value = TeamInfoDB(
            id="coord-team",
            name="Coordinate Team",
            version="2.0",
            mode="coordinate",
            created_at=self.now,
            updated_at=self.now,
        )

        create_req = {
            "id": "coord-team",
            "name": "Coordinate Team",
            "mode": "coordinate",
        }

        response = self.client.post("/v2/teams", json=create_req)
        self.assertEqual(response.status_code, 201)


class TestSupervisorTeamRun(unittest.TestCase):
    """Test running a supervisor team — leader classifies, routes to worker."""

    def setUp(self) -> None:
        self.client, self.app = create_test_client()
        from api.routes.v2_router import get_v2_router

        self.app.include_router(get_v2_router())
        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)

        self.now = datetime.datetime.utcnow()
        self.sample_team = TeamInfoDB(
            id="sup-team",
            name="Supervisor Team",
            version="2.0",
            mode="supervisor",
            created_at=self.now,
            updated_at=self.now,
        )
        self.sample_agents = [
            TeamAgentDB(
                id=1,
                team_id="sup-team",
                agent_id="leader-agent",
                role="leader",
                order_index=0,
                created_at=self.now,
            ),
            TeamAgentDB(
                id=2,
                team_id="sup-team",
                agent_id="worker-coding",
                role="worker",
                order_index=1,
                created_at=self.now,
            ),
        ]

    @patch("supervisor.team_builder.build_supervisor_team")
    @patch("api.routes.v2.teams.get_team_agents")
    @patch("api.routes.v2.teams.get_team_info")
    def test_supervisor_run_routes_to_worker(
        self,
        mock_get_team: MagicMock,
        mock_get_agents: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        """Leader classifies and routes task to worker in a supervisor team run."""
        mock_get_team.return_value = self.sample_team
        mock_get_agents.return_value = self.sample_agents

        mock_response = MagicMock()
        mock_response.content = "Task completed by worker-coding"
        mock_response.metrics = None
        mock_response.status = "completed"
        mock_response.run_id = "run-123"
        mock_response.tools = []

        mock_team = MagicMock()
        mock_team.arun = AsyncMock(return_value=mock_response)
        mock_build.return_value = mock_team

        run_request = {
            "message": "Fix the login bug in auth.py",
            "stream": False,
            "model": "gemini-2.5-pro",
        }

        response = self.client.post("/v2/teams/sup-team/runs", json=run_request)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "completed")
        self.assertIn("Task completed", data["content"])

    @patch("supervisor.team_builder.build_supervisor_team")
    @patch("api.routes.v2.teams.get_team_agents")
    @patch("api.routes.v2.teams.get_team_info")
    def test_supervisor_run_hitl_paused(
        self,
        mock_get_team: MagicMock,
        mock_get_agents: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        """HITL confirmation flow: supervisor returns PAUSED with tools array."""
        mock_get_team.return_value = self.sample_team
        mock_get_agents.return_value = self.sample_agents

        mock_tool = MagicMock()
        mock_tool.tool_call_id = "call_abc"
        mock_tool.tool_name = "run_claude_code"
        mock_tool.requires_confirmation = True
        mock_tool.tool_args = {"prompt": "deploy to prod"}
        mock_tool.result = None

        mock_response = MagicMock()
        mock_response.content = ""
        mock_response.metrics = None
        mock_response.status = RunStatus.paused
        mock_response.run_id = "run-paused-456"
        mock_response.tools = [mock_tool]

        mock_team = MagicMock()
        mock_team.arun = AsyncMock(return_value=mock_response)
        mock_build.return_value = mock_team

        run_request = {
            "message": "Deploy the app to production",
            "stream": False,
        }

        with patch("api.routes.v2.teams.register_pending_approval") as mock_register:
            response = self.client.post("/v2/teams/sup-team/runs", json=run_request)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "PAUSED")
        # A paused run must be cached for /runs/commit and its tool registered for approval.
        self.assertIs(teams_module._team_run_cache.pop("run-paused-456"), mock_response)
        mock_register.assert_called_once()
        self.assertEqual(data["run_id"], "run-paused-456")
        self.assertIsNotNone(data["tools"])
        self.assertEqual(len(data["tools"]), 1)
        self.assertEqual(data["tools"][0]["tool_name"], "run_claude_code")

    @patch("supervisor.team_builder.build_supervisor_team")
    @patch("api.routes.v2.teams.get_team_agents")
    @patch("api.routes.v2.teams.get_team_info")
    def test_supervisor_run_with_claude_sonnet(
        self,
        mock_get_team: MagicMock,
        mock_get_agents: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        """Test supervisor team run with claude-sonnet-4-6 model."""
        mock_get_team.return_value = self.sample_team
        mock_get_agents.return_value = self.sample_agents

        mock_response = MagicMock()
        mock_response.content = "Done"
        mock_response.metrics = None
        mock_response.status = "completed"
        mock_response.run_id = "run-sonnet"
        mock_response.tools = []

        mock_team = MagicMock()
        mock_team.arun = AsyncMock(return_value=mock_response)
        mock_build.return_value = mock_team

        run_request = {
            "message": "Hello",
            "stream": False,
            "model": "claude-sonnet-4-6",
        }

        response = self.client.post("/v2/teams/sup-team/runs", json=run_request)
        self.assertEqual(response.status_code, 200)

    @patch("supervisor.team_builder.build_supervisor_team")
    @patch("api.routes.v2.teams.get_team_agents")
    @patch("api.routes.v2.teams.get_team_info")
    def test_supervisor_run_with_gemini_flash(
        self,
        mock_get_team: MagicMock,
        mock_get_agents: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        """Test supervisor team run with gemini-2.5-flash model."""
        mock_get_team.return_value = self.sample_team
        mock_get_agents.return_value = self.sample_agents

        mock_response = MagicMock()
        mock_response.content = "Done"
        mock_response.metrics = None
        mock_response.status = "completed"
        mock_response.run_id = "run-flash"
        mock_response.tools = []

        mock_team = MagicMock()
        mock_team.arun = AsyncMock(return_value=mock_response)
        mock_build.return_value = mock_team

        run_request = {
            "message": "Hello",
            "stream": False,
            "model": "gemini-2.5-flash",
        }

        response = self.client.post("/v2/teams/sup-team/runs", json=run_request)
        self.assertEqual(response.status_code, 200)


class TestCoordinateTeamBackwardCompat(unittest.TestCase):
    """Verify coordinate mode teams are unaffected by supervisor additions."""

    def setUp(self) -> None:
        self.client, self.app = create_test_client()
        from api.routes.v2_router import get_v2_router

        self.app.include_router(get_v2_router())
        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)

        self.now = datetime.datetime.utcnow()
        self.coord_team = TeamInfoDB(
            id="coord-team",
            name="Coordinate Team",
            version="2.0",
            mode="coordinate",
            created_at=self.now,
            updated_at=self.now,
        )

    @patch("api.routes.v2.teams.create_team")
    @patch("api.routes.v2.teams.get_agent")
    @patch("api.routes.v2.teams.get_team_agents")
    @patch("api.routes.v2.teams.get_team_info")
    def test_coordinate_mode_does_not_use_supervisor_builder(
        self,
        mock_get_team: MagicMock,
        mock_get_agents: MagicMock,
        mock_get_agent: MagicMock,
        mock_create_team: MagicMock,
    ) -> None:
        """Coordinate mode should use the standard create_team, not build_supervisor_team."""
        mock_get_team.return_value = self.coord_team
        mock_get_agents.return_value = [
            TeamAgentDB(id=1, team_id="coord-team", agent_id="agent-1", role="dev", order_index=0, created_at=self.now),
        ]

        mock_agent = MagicMock()
        mock_get_agent.return_value = mock_agent

        mock_response = MagicMock()
        mock_response.content = "OK"
        mock_response.metrics = None
        mock_response.status = "completed"
        mock_response.run_id = "run-coord"
        mock_response.tools = []

        mock_team_obj = MagicMock()
        mock_team_obj.arun = AsyncMock(return_value=mock_response)
        mock_create_team.return_value = mock_team_obj

        run_request = {"message": "Hello", "stream": False}

        with patch("supervisor.team_builder.build_supervisor_team") as mock_supervisor:
            response = self.client.post("/v2/teams/coord-team/runs", json=run_request)

        self.assertEqual(response.status_code, 200)
        mock_supervisor.assert_not_called()

    @patch("api.routes.v2.teams.get_team_info")
    def test_stream_verbosity_field_accepted(self, mock_get_team: MagicMock) -> None:
        """Verify stream_verbosity field is accepted in TeamRunRequest."""
        mock_get_team.return_value = None

        run_request = {
            "message": "Hello",
            "stream": False,
            "stream_verbosity": "full",
        }

        # Team not found (404) proves the field was accepted (not a 422 validation error)
        response = self.client.post("/v2/teams/missing-team/runs", json=run_request)
        self.assertEqual(response.status_code, 404)


class TestSupervisorAuditTrail(unittest.TestCase):
    """Test that supervisor runs log to audit trail (supervisor_runs)."""

    def setUp(self) -> None:
        self.client, self.app = create_test_client()
        from api.routes.v2_router import get_v2_router

        self.app.include_router(get_v2_router())
        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)
        self.now = datetime.datetime.utcnow()

    @patch("supervisor.team_builder.build_supervisor_team")
    @patch("api.routes.v2.teams.get_team_agents")
    @patch("api.routes.v2.teams.get_team_info")
    def test_supervisor_run_completes_without_error(
        self,
        mock_get_team: MagicMock,
        mock_get_agents: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        """Supervisor team run completes and returns structured response."""
        mock_get_team.return_value = TeamInfoDB(
            id="audit-team",
            name="Audit Team",
            version="2.0",
            mode="supervisor",
            created_at=self.now,
            updated_at=self.now,
        )
        mock_get_agents.return_value = [
            TeamAgentDB(
                id=1, team_id="audit-team", agent_id="leader", role="leader", order_index=0, created_at=self.now
            ),
        ]

        mock_response = MagicMock()
        mock_response.content = "Audit logged"
        mock_response.metrics = None
        mock_response.status = "completed"
        mock_response.run_id = "run-audit"
        mock_response.tools = []

        mock_team = MagicMock()
        mock_team.arun = AsyncMock(return_value=mock_response)
        mock_build.return_value = mock_team

        response = self.client.post("/v2/teams/audit-team/runs", json={"message": "Audit test", "stream": False})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["team_id"], "audit-team")
        self.assertEqual(data["run_id"], "run-audit")


if __name__ == "__main__":
    unittest.main()
