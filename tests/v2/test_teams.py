"""
Integration tests for V2 teams API endpoints.
"""

import datetime
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from agents.hitl import CachedRun, ChatContext
from db.db_models import AgentInfoDB, TeamAgentDB, TeamInfoDB
from tests.test_utils import create_test_client


class TestV2TeamsAPI(unittest.TestCase):
    """Test V2 teams API endpoints."""

    def setUp(self):
        """Set up test fixtures."""
        self.client, self.app = create_test_client()
        # Manually register routers for testing
        from api.routes.v2_router import get_v2_router

        self.app.include_router(get_v2_router())

        # Recreate client with the updated app
        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)

        self.sample_team_data = {
            "id": "test-team-v2",
            "name": "Test Team V2",
            "description": "A test team for V2 API",
            "version": "2.0",
            "created_at": datetime.datetime.utcnow(),
            "updated_at": datetime.datetime.utcnow(),
        }

    @patch("api.routes.v2.teams.get_all_team_info")
    def test_get_teams_success(self, mock_get_all):
        """Test successful retrieval of V2 teams."""
        # Mock database teams
        now = datetime.datetime.utcnow()
        mock_teams = [
            TeamInfoDB(
                id="team-1", name="Team 1", description="First team", version="2.0", created_at=now, updated_at=now
            ),
            TeamInfoDB(
                id="team-2", name="Team 2", description="Second team", version="2.0", created_at=now, updated_at=now
            ),
        ]
        mock_get_all.return_value = mock_teams

        response = self.client.get("/v2/teams")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreater(len(data), 0)
        self.assertEqual(len(data), 2)

        # Verify structure of returned teams
        team1 = data[0]
        self.assertIn("id", team1)
        self.assertIn("name", team1)
        self.assertIn("description", team1)
        self.assertIn("version", team1)

    @patch("api.routes.v2.teams.get_all_team_info")
    def test_get_teams_empty(self, mock_get_all):
        """Test retrieval when no V2 teams are available."""
        mock_get_all.return_value = []

        response = self.client.get("/v2/teams")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data, [])

    @patch("api.routes.v2.teams.get_team_info")
    def test_get_team_by_id_success(self, mock_get_team):
        """Test successful retrieval of a specific V2 team."""
        # Mock database team
        mock_team = TeamInfoDB(**self.sample_team_data)
        mock_get_team.return_value = mock_team

        response = self.client.get("/v2/teams/test-team-v2")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], "test-team-v2")
        self.assertEqual(data["name"], "Test Team V2")

    @patch("api.routes.v2.teams.get_team_info")
    def test_get_team_by_id_not_found(self, mock_get_team):
        """Test retrieval of non-existent V2 team."""
        mock_get_team.return_value = None

        response = self.client.get("/v2/teams/non-existent-team")

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("detail", data)

    @patch("api.routes.v2.teams.team_info_exists")
    @patch("api.routes.v2.teams.create_team_info")
    def test_create_team_success(self, mock_create_team, mock_team_info_exists):
        """Test successful creation of a V2 team."""
        # Mock database creation
        mock_created_team = TeamInfoDB(**self.sample_team_data)
        mock_create_team.return_value = mock_created_team
        mock_team_info_exists.return_value = None

        create_request = {"id": "new-team-v2", "name": "New Team V2", "description": "A new test team"}

        response = self.client.post("/v2/teams", json=create_request)

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertIn("id", data)
        self.assertIn("message", data)

    def test_create_team_missing_required_fields(self):
        """Test creation with missing required fields."""
        incomplete_request = {
            "name": "Incomplete Team"
            # Missing required field: id
        }

        response = self.client.post("/v2/teams", json=incomplete_request)

        self.assertEqual(response.status_code, 422)  # Validation error

    @patch("api.routes.v2.teams.create_team_info")
    def test_create_team_duplicate_id(self, mock_create_team):
        """Test creation with duplicate team ID."""
        # Mock database error for duplicate
        mock_create_team.side_effect = Exception("UNIQUE constraint failed")

        create_request = {"id": "duplicate-team", "name": "Duplicate Team", "description": "This should fail"}

        response = self.client.post("/v2/teams", json=create_request)

        self.assertEqual(response.status_code, 409)  # Conflict

    @patch("api.routes.v2.teams.delete_team_info")
    @patch("api.routes.v2.teams.get_team_info")
    def test_delete_team_success(self, mock_get_team, mock_delete_team):
        """Test successful deletion (soft delete) of a V2 team."""
        # Mock team exists
        mock_get_team.return_value = TeamInfoDB(**self.sample_team_data)
        mock_delete_team.return_value = True

        response = self.client.delete("/v2/teams/test-team-v2")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("message", data)

    @patch("api.routes.v2.teams.delete_team_info")
    def test_delete_team_not_found(self, mock_delete_team):
        """Test deletion of non-existent V2 team."""
        mock_delete_team.return_value = False

        response = self.client.delete("/v2/teams/non-existent-team")

        self.assertEqual(response.status_code, 404)

    @patch("api.routes.v2.teams.get_team_info")
    def test_add_agent_to_nonexistent_team(self, mock_get_team):
        """Test adding agent to non-existent team."""
        mock_get_team.return_value = None

        add_request = {"agent_id": "test-agent"}

        response = self.client.post("/v2/teams/non-existent-team/agents", json=add_request)

        self.assertEqual(response.status_code, 404)

    @patch("db.team_info_crud.remove_agent_from_team")
    @patch("api.routes.v2.teams.get_team_info")
    def test_remove_agent_not_in_team(self, mock_get_team, mock_remove_agent):
        """Test removing agent that's not in the team."""
        # Mock team exists but agent removal fails
        mock_get_team.return_value = TeamInfoDB(**self.sample_team_data)
        mock_remove_agent.return_value = False

        response = self.client.delete("/v2/teams/test-team-v2/agents/non-member-agent")

        self.assertEqual(response.status_code, 404)


class TestV2TeamMembersAPI(unittest.TestCase):
    """Test V2 team members API endpoints."""

    def setUp(self):
        """Set up test fixtures."""
        self.client, self.app = create_test_client()
        # Manually register routers for testing
        from api.routes.v2_router import get_v2_router

        self.app.include_router(get_v2_router())

        # Recreate client with the updated app
        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)

        self.sample_team = TeamInfoDB(
            id="test-team",
            name="Test Team",
            description="A test team",
            version="2.0",
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )

        self.sample_agent = AgentInfoDB(
            id="test-agent",
            name="Test Agent",
            description="A test agent",
            version="2.0",
            prompt_service_id="prompt-123",
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )

        self.sample_team_agent = TeamAgentDB(
            id=1,
            team_id="test-team",
            agent_id="test-agent",
            role="developer",
            order_index=1,
            created_at=datetime.datetime.utcnow(),
        )

    @patch("api.routes.v2.teams.get_team_agents")
    @patch("api.routes.v2.teams.get_team_info")
    def test_get_team_members_success(self, mock_get_team, mock_get_agents):
        """Test successful retrieval of team members."""
        mock_get_team.return_value = self.sample_team
        mock_get_agents.return_value = [self.sample_team_agent]

        response = self.client.get("/v2/teams/test-team/members")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)

        member = data[0]
        self.assertEqual(member["agent_id"], "test-agent")
        self.assertEqual(member["role"], "developer")
        self.assertEqual(member["order_index"], 1)
        self.assertIn("created_at", member)

    @patch("api.routes.v2.teams.get_team_info")
    def test_get_team_members_team_not_found(self, mock_get_team):
        """Test retrieval of members from non-existent team."""
        mock_get_team.return_value = None

        response = self.client.get("/v2/teams/non-existent/members")

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("detail", data)
        self.assertIn("not found", data["detail"])

    @patch("api.routes.v2.teams.get_team_agents")
    @patch("api.routes.v2.teams.get_team_info")
    def test_get_team_members_empty(self, mock_get_team, mock_get_agents):
        """Test retrieval when team has no members."""
        mock_get_team.return_value = self.sample_team
        mock_get_agents.return_value = []

        response = self.client.get("/v2/teams/test-team/members")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data, [])

    @patch("api.routes.v2.teams.add_agent_to_team")
    @patch("api.routes.v2.teams.get_agent_info")
    @patch("api.routes.v2.teams.get_team_info")
    def test_add_team_member_success(self, mock_get_team, mock_get_agent, mock_add_agent):
        """Test successful addition of member to team."""
        mock_get_team.return_value = self.sample_team
        mock_get_agent.return_value = self.sample_agent
        mock_add_agent.return_value = self.sample_team_agent

        add_request = {"agent_id": "test-agent", "role": "developer", "order_index": 1}

        response = self.client.post("/v2/teams/test-team/members", json=add_request)

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertIn("message", data)
        self.assertIn("member", data)

        member = data["member"]
        self.assertEqual(member["agent_id"], "test-agent")
        self.assertEqual(member["role"], "developer")
        self.assertEqual(member["order_index"], 1)

    @patch("api.routes.v2.teams.get_team_info")
    def test_add_team_member_team_not_found(self, mock_get_team):
        """Test adding member to non-existent team."""
        mock_get_team.return_value = None

        add_request = {"agent_id": "test-agent"}

        response = self.client.post("/v2/teams/non-existent/members", json=add_request)

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("detail", data)
        self.assertIn("not found", data["detail"])

    @patch("api.routes.v2.teams.get_agent_info")
    @patch("api.routes.v2.teams.get_team_info")
    def test_add_team_member_agent_not_found(self, mock_get_team, mock_get_agent):
        """Test adding non-existent agent to team."""
        mock_get_team.return_value = self.sample_team
        mock_get_agent.return_value = None

        add_request = {"agent_id": "non-existent-agent"}

        response = self.client.post("/v2/teams/test-team/members", json=add_request)

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("detail", data)
        self.assertIn("not found", data["detail"])

    @patch("api.routes.v2.teams.add_agent_to_team")
    @patch("api.routes.v2.teams.get_agent_info")
    @patch("api.routes.v2.teams.get_team_info")
    def test_add_team_member_already_exists(self, mock_get_team, mock_get_agent, mock_add_agent):
        """Test adding agent that's already a team member."""
        mock_get_team.return_value = self.sample_team
        mock_get_agent.return_value = self.sample_agent
        mock_add_agent.side_effect = ValueError("Agent test-agent is already in team test-team")

        add_request = {"agent_id": "test-agent"}

        response = self.client.post("/v2/teams/test-team/members", json=add_request)

        self.assertEqual(response.status_code, 409)
        data = response.json()
        self.assertIn("detail", data)

    def test_add_team_member_missing_agent_id(self):
        """Test adding member without required agent_id."""
        add_request = {"role": "developer"}  # Missing agent_id

        response = self.client.post("/v2/teams/test-team/members", json=add_request)

        self.assertEqual(response.status_code, 422)  # Validation error

    @patch("api.routes.v2.teams.remove_agent_from_team")
    @patch("api.routes.v2.teams.get_team_info")
    def test_remove_team_member_success(self, mock_get_team, mock_remove_agent):
        """Test successful removal of team member."""
        mock_get_team.return_value = self.sample_team
        mock_remove_agent.return_value = True

        response = self.client.delete("/v2/teams/test-team/members/test-agent")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("message", data)
        self.assertIn("removed", data["message"])

    @patch("api.routes.v2.teams.get_team_info")
    def test_remove_team_member_team_not_found(self, mock_get_team):
        """Test removing member from non-existent team."""
        mock_get_team.return_value = None

        response = self.client.delete("/v2/teams/non-existent/members/test-agent")

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("detail", data)
        self.assertIn("not found", data["detail"])

    @patch("api.routes.v2.teams.remove_agent_from_team")
    @patch("api.routes.v2.teams.get_team_info")
    def test_remove_team_member_not_member(self, mock_get_team, mock_remove_agent):
        """Test removing agent that's not a team member."""
        mock_get_team.return_value = self.sample_team
        mock_remove_agent.return_value = False

        response = self.client.delete("/v2/teams/test-team/members/non-member-agent")

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("detail", data)
        self.assertIn("not a member", data["detail"])

    @patch("api.routes.v2.teams.update_team_agent_role")
    @patch("api.routes.v2.teams.get_team_info")
    def test_update_team_member_success(self, mock_get_team, mock_update_agent):
        """Test successful update of team member."""
        mock_get_team.return_value = self.sample_team

        updated_team_agent = TeamAgentDB(
            id=1,
            team_id="test-team",
            agent_id="test-agent",
            role="lead-developer",
            order_index=2,
            created_at=datetime.datetime.utcnow(),
        )
        mock_update_agent.return_value = updated_team_agent

        update_request = {"role": "lead-developer", "order_index": 2}

        response = self.client.put("/v2/teams/test-team/members/test-agent", json=update_request)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["agent_id"], "test-agent")
        self.assertEqual(data["role"], "lead-developer")
        self.assertEqual(data["order_index"], 2)

    @patch("api.routes.v2.teams.get_team_info")
    def test_update_team_member_team_not_found(self, mock_get_team):
        """Test updating member in non-existent team."""
        mock_get_team.return_value = None

        update_request = {"role": "new-role"}

        response = self.client.put("/v2/teams/non-existent/members/test-agent", json=update_request)

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("detail", data)
        self.assertIn("not found", data["detail"])

    @patch("api.routes.v2.teams.get_team_info")
    def test_update_team_member_no_fields(self, mock_get_team):
        """Test updating member without providing any fields."""
        mock_get_team.return_value = self.sample_team

        update_request: dict[str, Any] = {}  # No fields to update

        response = self.client.put("/v2/teams/test-team/members/test-agent", json=update_request)

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("detail", data)
        self.assertIn("At least one field", data["detail"])

    @patch("api.routes.v2.teams.update_team_agent_role")
    @patch("api.routes.v2.teams.get_team_info")
    def test_update_team_member_not_member(self, mock_get_team, mock_update_agent):
        """Test updating agent that's not a team member."""
        mock_get_team.return_value = self.sample_team
        mock_update_agent.return_value = None

        update_request = {"role": "new-role"}

        response = self.client.put("/v2/teams/test-team/members/non-member-agent", json=update_request)

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("detail", data)
        self.assertIn("not a member", data["detail"])

    @patch("api.routes.v2.teams.update_team_agent_role")
    @patch("api.routes.v2.teams.get_team_info")
    def test_update_team_member_role_only(self, mock_get_team, mock_update_agent):
        """Test updating only the role of a team member."""
        mock_get_team.return_value = self.sample_team

        updated_team_agent = TeamAgentDB(
            id=1,
            team_id="test-team",
            agent_id="test-agent",
            role="senior-developer",
            order_index=1,  # Keep original order
            created_at=datetime.datetime.utcnow(),
        )
        mock_update_agent.return_value = updated_team_agent

        update_request = {"role": "senior-developer"}

        response = self.client.put("/v2/teams/test-team/members/test-agent", json=update_request)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["role"], "senior-developer")
        self.assertEqual(data["order_index"], 1)  # Should remain unchanged

    @patch("api.routes.v2.teams.update_team_agent_role")
    @patch("api.routes.v2.teams.get_team_info")
    def test_update_team_member_order_only(self, mock_get_team, mock_update_agent):
        """Test updating only the order_index of a team member."""
        mock_get_team.return_value = self.sample_team

        updated_team_agent = TeamAgentDB(
            id=1,
            team_id="test-team",
            agent_id="test-agent",
            role="developer",  # Keep original role
            order_index=5,
            created_at=datetime.datetime.utcnow(),
        )
        mock_update_agent.return_value = updated_team_agent

        update_request = {"order_index": 5}

        response = self.client.put("/v2/teams/test-team/members/test-agent", json=update_request)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["role"], "developer")  # Should remain unchanged
        self.assertEqual(data["order_index"], 5)


class TestV2TeamsAPIExtended(unittest.TestCase):
    """Extended V2 Teams API tests (TM-002 to TM-054)."""

    def setUp(self):
        """Set up test fixtures."""
        self.client, self.app = create_test_client()
        from api.routes.v2_router import get_v2_router

        self.app.include_router(get_v2_router())

        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)

        self.sample_team_data = {
            "id": "test-team-v2",
            "name": "Test Team V2",
            "description": "A test team for V2 API",
            "version": "2.0",
            "created_at": datetime.datetime.utcnow(),
            "updated_at": datetime.datetime.utcnow(),
        }

    @patch("api.routes.v2.teams.team_info_exists")
    @patch("api.routes.v2.teams.create_team_info")
    def test_tm_002_create_team_without_agents(self, mock_create_team, mock_team_exists):
        """TM-002: Create team without agents (valid request)."""
        mock_team_exists.return_value = False
        mock_created_team = TeamInfoDB(**self.sample_team_data)
        mock_create_team.return_value = mock_created_team

        # Create team without agents (empty agents list)
        create_request = {
            "id": "team-no-agents",
            "name": "Team Without Agents",
            "description": "A team with no initial agents",
            "agents": [],
        }

        response = self.client.post("/v2/teams", json=create_request)

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["id"], "team-no-agents")
        self.assertIn("message", data)

    @patch("api.routes.v2.teams.get_agent_info")
    @patch("api.routes.v2.teams.team_info_exists")
    def test_tm_004_create_team_invalid_agent(self, mock_team_exists, mock_get_agent):
        """TM-004: Create team with invalid agent reference."""
        mock_team_exists.return_value = False
        mock_get_agent.return_value = None  # Agent not found

        create_request = {
            "id": "team-invalid-agent",
            "name": "Team With Invalid Agent",
            "agents": [{"agent_id": "non-existent-agent"}],
        }

        response = self.client.post("/v2/teams", json=create_request)

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("not found", data["detail"])

    @patch("api.routes.v2.teams.add_agent_to_team")
    @patch("api.routes.v2.teams.get_agent_info")
    @patch("api.routes.v2.teams.create_team_info")
    @patch("api.routes.v2.teams.team_info_exists")
    def test_tm_005_create_team_with_roles(self, mock_team_exists, mock_create_team, mock_get_agent, mock_add_agent):
        """TM-005: Create team with agent roles."""
        mock_team_exists.return_value = False
        mock_created_team = TeamInfoDB(**self.sample_team_data)
        mock_create_team.return_value = mock_created_team

        # Mock agent exists
        mock_agent = AgentInfoDB(
            id="agent-1",
            name="Agent 1",
            description="Test agent",
            version="2.0",
            prompt_service_id="prompt-123",
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )
        mock_get_agent.return_value = mock_agent

        # Mock add_agent_to_team
        mock_team_agent = TeamAgentDB(
            id=1,
            team_id="team-with-roles",
            agent_id="agent-1",
            role="developer",
            order_index=1,
            created_at=datetime.datetime.utcnow(),
        )
        mock_add_agent.return_value = mock_team_agent

        create_request = {
            "id": "team-with-roles",
            "name": "Team With Roles",
            "agents": [{"agent_id": "agent-1", "role": "developer", "order_index": 1}],
        }

        response = self.client.post("/v2/teams", json=create_request)

        self.assertEqual(response.status_code, 201)
        # Verify add_agent_to_team was called with role
        mock_add_agent.assert_called_once()
        call_kwargs = mock_add_agent.call_args[1]
        self.assertEqual(call_kwargs["role"], "developer")
        self.assertEqual(call_kwargs["order_index"], 1)

    @patch("api.routes.v2.teams.get_team_agents")
    @patch("api.routes.v2.teams.get_team_info")
    def test_tm_012_get_team_with_agent_details(self, mock_get_team, mock_get_agents):
        """TM-012: Get team with agent details included."""
        mock_team = TeamInfoDB(**self.sample_team_data)
        mock_get_team.return_value = mock_team

        # Mock agents in team
        mock_team_agents = [
            TeamAgentDB(
                id=1,
                team_id="test-team-v2",
                agent_id="agent-1",
                role="lead",
                order_index=1,
                created_at=datetime.datetime.utcnow(),
            ),
            TeamAgentDB(
                id=2,
                team_id="test-team-v2",
                agent_id="agent-2",
                role="developer",
                order_index=2,
                created_at=datetime.datetime.utcnow(),
            ),
        ]
        mock_get_agents.return_value = mock_team_agents

        response = self.client.get("/v2/teams/test-team-v2")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], "test-team-v2")
        self.assertIn("agents", data)
        self.assertEqual(len(data["agents"]), 2)

        # Verify agent details
        agent1 = data["agents"][0]
        self.assertEqual(agent1["agent_id"], "agent-1")
        self.assertEqual(agent1["role"], "lead")
        self.assertEqual(agent1["order_index"], 1)

    @patch("api.routes.v2.teams.get_all_team_info")
    def test_tm_021_list_with_include_inactive(self, mock_get_all):
        """TM-021: List teams with include_inactive filter."""
        now = datetime.datetime.utcnow()
        # Return teams when include_inactive is True
        mock_teams = [
            TeamInfoDB(
                id="active-team",
                name="Active Team",
                description="An active team",
                version="2.0",
                created_at=now,
                updated_at=now,
                is_active=True,
            ),
            TeamInfoDB(
                id="inactive-team",
                name="Inactive Team",
                description="A soft-deleted team",
                version="2.0",
                created_at=now,
                updated_at=now,
                is_active=False,
            ),
        ]
        mock_get_all.return_value = mock_teams

        response = self.client.get("/v2/teams?include_inactive=true")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)

        # Verify include_inactive was passed to the function
        mock_get_all.assert_called_once()
        call_args = mock_get_all.call_args
        self.assertTrue(call_args[1].get("include_inactive", False))


class TestV2TeamRunsAPI(unittest.TestCase):
    """Test V2 team run/chat endpoints (TM-050 to TM-054)."""

    def setUp(self):
        """Set up test fixtures."""
        self.client, self.app = create_test_client()
        from api.routes.v2_router import get_v2_router

        self.app.include_router(get_v2_router())

        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)

        self.sample_team = TeamInfoDB(
            id="test-team",
            name="Test Team",
            description="A test team",
            version="2.0",
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )

    @patch("api.routes.v2.teams.get_team_info")
    def test_tm_050_team_run_not_found(self, mock_get_team):
        """TM-050: Team run - team not found."""
        mock_get_team.return_value = None

        run_request = {
            "message": "Hello team",
            "stream": False,
        }

        response = self.client.post("/v2/teams/non-existent-team/runs", json=run_request)

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("not found", data["detail"])

    @patch("api.routes.v2.teams.get_team_agents")
    @patch("api.routes.v2.teams.get_team_info")
    def test_tm_051_team_run_no_agents(self, mock_get_team, mock_get_agents):
        """TM-051: Team run - team has no agents."""
        mock_get_team.return_value = self.sample_team
        mock_get_agents.return_value = []  # No agents

        run_request = {
            "message": "Hello team",
            "stream": False,
        }

        response = self.client.post("/v2/teams/test-team/runs", json=run_request)

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("no agents", data["detail"])

    def test_tm_052_team_run_missing_message(self):
        """TM-052: Team run - missing message field."""
        run_request = {
            "stream": False,
            # Missing "message" field
        }

        response = self.client.post("/v2/teams/test-team/runs", json=run_request)

        self.assertEqual(response.status_code, 422)  # Validation error

    @patch("api.routes.v2.teams._team_run_cache", {})
    def test_tm_054_commit_run_not_found(self):
        """TM-054: Commit - run_id not found in cache."""
        commit_request = {
            "run_id": "non-existent-run-id",
            "updated_tools": [{"tool_call_id": "call_1", "confirmed": True}],
            "stream": False,
        }

        response = self.client.post("/v2/teams/test-team/runs/commit", json=commit_request)

        # Should return 410 Gone (confirmation window elapsed)
        self.assertEqual(response.status_code, 410)
        data = response.json()
        self.assertIn("elapsed", data["detail"])

    @patch("api.routes.v2.teams._team_run_cache", {})
    def test_tm_055_commit_all_tools_denied(self):
        """TM-055: Commit - all tools denied by user (run not in cache)."""
        commit_request = {
            "run_id": "some-run-id",
            "updated_tools": [
                {"tool_call_id": "call_1", "confirmed": False},
                {"tool_call_id": "call_2", "confirmed": False},
            ],
            "stream": False,
        }

        response = self.client.post("/v2/teams/test-team/runs/commit", json=commit_request)

        # When all tools denied and run not in cache, should return cancelled
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "cancelled")
        self.assertIn("cancelled", data["content"])

    @patch("api.routes.v2.teams.record_usage", new_callable=AsyncMock)
    @patch("api.routes.v2.teams.create_team")
    @patch("api.routes.v2.teams.get_db_url", return_value="postgresql://test")
    @patch("api.routes.v2.teams.get_agent")
    @patch("api.routes.v2.teams.get_team_agents")
    @patch("api.routes.v2.teams.get_team_info")
    def test_tm_056_commit_all_tools_denied_resumes_cached_run(
        self, mock_get_team, mock_get_agents, mock_get_agent, mock_db_url, mock_create_team, mock_record
    ):
        """TM-056: Commit - all tools denied on a cached run resumes it, so the model replies."""
        from agno.models.response import ToolExecution

        tool = ToolExecution(
            tool_call_id="call_1", tool_name="delete_budget", tool_args={"budget_id": 7}, requires_confirmation=True
        )
        cached_run = SimpleNamespace(tools=[tool], requirements=None)
        # Fields the request leaves out get the same defaults on the run and on its commit.
        context = ChatContext(None, "anonymous", "session_test-team", "test-team")
        mock_get_team.return_value = TeamInfoDB(id="test-team", name="Test Team")
        mock_get_agents.return_value = [TeamAgentDB(team_id="test-team", agent_id="agent-1", order_index=0)]
        team = MagicMock()
        team.acontinue_run = AsyncMock(return_value=SimpleNamespace(content="OK, I won't delete it.", metrics=None))
        mock_create_team.return_value = team
        mock_record.return_value = None

        with patch.dict(
            "api.routes.v2.teams._team_run_cache", {"run-1": CachedRun(cached_run, context)}, clear=True
        ) as cache:
            response = self.client.post(
                "/v2/teams/test-team/runs/commit",
                json={
                    "run_id": "run-1",
                    "updated_tools": [{"tool_call_id": "call_1", "confirmed": False, "confirmation_note": "No"}],
                    "stream": False,
                },
            )
            self.assertNotIn("run-1", cache)

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["content"], "OK, I won't delete it.")
        [requirement] = team.acontinue_run.await_args.kwargs["requirements"]
        self.assertIs(requirement.confirmation, False)
        self.assertEqual(requirement.confirmation_note, "No")

    @patch("api.routes.v2.teams.get_team_info")
    def test_tm_057_commit_from_another_chat_is_refused(self, mock_get_team):
        """TM-057: Commit - a run from another chat is refused and left as it was."""
        from agno.models.response import ToolExecution

        from api.routes.v2 import teams

        self.client.app.dependency_overrides[teams.get_api_key] = lambda: SimpleNamespace(id=2, scopes=None)
        self.addCleanup(self.client.app.dependency_overrides.pop, teams.get_api_key)
        mock_get_team.return_value = None  # a commit that passes the check stops here with a 404

        for confirmed in (True, False):
            for context in (
                ChatContext(1, "alice", "s1", "test-team"),
                ChatContext(None, "alice", "s1", "test-team"),
                ChatContext(2, "bob", "s1", "test-team"),
                ChatContext(2, "alice", "s2", "test-team"),
                ChatContext(2, "alice", "s1", "other-team"),
                None,
            ):
                tool = ToolExecution(tool_call_id="call_1", tool_name="delete_budget", tool_args={"budget_id": 7})
                cached = CachedRun(SimpleNamespace(tools=[tool], requirements=None), context)
                decision = {"tool_call_id": "call_1", "confirmed": confirmed, "tool_args": {"budget_id": 8}}
                with (
                    self.subTest(context=context, confirmed=confirmed),
                    patch.dict("api.routes.v2.teams._team_run_cache", {"run-1": cached}, clear=True) as cache,
                ):
                    response = self.client.post(
                        "/v2/teams/test-team/runs/commit",
                        json={"run_id": "run-1", "updated_tools": [decision], "user_id": "alice", "session_id": "s1"},
                    )

                    self.assertEqual(response.status_code, 403, response.text)
                    self.assertIs(cache["run-1"], cached)
                    self.assertIsNone(tool.confirmed)
                    self.assertEqual(tool.tool_args, {"budget_id": 7})
        mock_get_team.assert_not_called()

        run = SimpleNamespace(tools=[], requirements=None)
        own = CachedRun(run, ChatContext(2, "alice", "s1", "test-team"))
        with patch.dict("api.routes.v2.teams._team_run_cache", {"run-1": own}, clear=True):
            response = self.client.post(
                "/v2/teams/test-team/runs/commit",
                json={"run_id": "run-1", "updated_tools": [], "user_id": "alice", "session_id": "s1"},
            )
        self.assertEqual(response.status_code, 404, response.text)

    @patch("api.routes.v2.teams.record_usage", new_callable=AsyncMock, return_value=None)
    @patch("api.routes.v2.teams.create_team")
    @patch("api.routes.v2.teams.get_db_url", return_value="postgresql://test")
    @patch("api.routes.v2.teams.get_agent")
    @patch("api.routes.v2.teams.get_team_agents")
    @patch("api.routes.v2.teams.get_team_info")
    def test_tm_058_streamed_pause_keeps_its_chat(
        self, mock_get_team, mock_get_agents, mock_get_agent, mock_db_url, mock_create_team, mock_record
    ):
        """TM-058: A member's streamed pause is cached under both run ids with the chat that started it."""
        from agno.run.agent import RunPausedEvent

        mock_get_team.return_value = TeamInfoDB(id="test-team", name="Test Team")
        mock_get_agents.return_value = [TeamAgentDB(team_id="test-team", agent_id="agent-1", order_index=0)]
        paused = RunPausedEvent(run_id="member-run", parent_run_id="team-run", session_id="s1")

        async def events(*args, **kwargs):
            yield paused

        mock_create_team.return_value = MagicMock(arun=events)

        with patch.dict("api.routes.v2.teams._team_run_cache", {}, clear=True) as cache:
            response = self.client.post(
                "/v2/teams/test-team/runs", json={"message": "hi", "user_id": "alice", "session_id": "s1"}
            )
            self.assertEqual(response.status_code, 200, response.text)
            expected = CachedRun(paused, ChatContext(None, "alice", "s1", "test-team"))
            self.assertEqual(dict(cache), {"member-run": expected, "team-run": expected})


if __name__ == "__main__":
    unittest.main()
