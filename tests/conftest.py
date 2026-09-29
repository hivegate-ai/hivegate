"""
Test configuration and fixtures for the hivegate test suite.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Set test environment variables early
os.environ["TESTING"] = "true"
os.environ["OPENAI_API_KEY"] = "test-key"

from api.main import create_app
from db.db_models import Base
from db.session import get_db

# Test database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Override database dependency for testing."""
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


@pytest.fixture(scope="session")
def test_app():
    """Create a test FastAPI application."""
    # Create tables
    Base.metadata.create_all(bind=engine)

    # Mock the prompts loading to avoid lifespan issues
    with patch("api.prompts_state.all_prompts") as mock_prompts:
        mock_prompts.keys.return_value = ["test-agent"]
        mock_prompts.__getitem__.return_value = {"template": "Test template"}
        mock_prompts.update = MagicMock()
        mock_prompts.clear = MagicMock()

        # Create app with test database
        app = create_app()
        app.dependency_overrides[get_db] = override_get_db

        yield app

    # Cleanup
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(test_app):
    """Create a test client."""
    return TestClient(test_app)


@pytest.fixture
def db_session():
    """Create a database session for testing."""
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def sample_agent_data():
    """Sample agent data for testing."""
    return {
        "id": "test-agent",
        "name": "Test Agent",
        "description": "A test agent for unit testing",
        "prompt_service_id": "test-prompt-123",
        "tags": '["test", "demo"]',
        "version": "2.0",
    }


@pytest.fixture
def sample_team_data():
    """Sample team data for testing."""
    return {"id": "test-team", "name": "Test Team", "description": "A test team for unit testing", "version": "2.0"}


@pytest.fixture
def mock_prompts_client():
    """Mock the prompts service client."""
    with patch("api.services.prompts_client.prompts_client") as mock_client:
        mock_client.get_prompt.return_value = MagicMock(
            name="test-prompt", template="You are a helpful assistant.", system_message="Test system message"
        )
        mock_client.list_prompts.return_value = ["test-prompt-123"]
        mock_client.create_prompt.return_value = True
        mock_client.delete_prompt.return_value = True
        yield mock_client


@pytest.fixture
def mock_agno_agent():
    """Mock the Agno agent for testing."""
    with patch("agents.agent.get_agent") as mock_agent:
        mock_instance = MagicMock()
        mock_instance.run.return_value = "Test agent response"
        mock_agent.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_cloud_run_service():
    """Mock cloud run service calls."""
    with patch("agents.agent_utils.call_cloud_run_service") as mock_service:
        mock_response = MagicMock()
        mock_response.json.return_value = {"prompts": ["test-prompt-123"]}
        mock_response.raise_for_status.return_value = None
        mock_service.return_value = mock_response
        yield mock_service
