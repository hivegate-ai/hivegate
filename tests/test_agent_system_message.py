"""The system message agno builds for a get_agent() agent (#232).

get_agent() used to pass the stored prompt as agno's `system_message`, which agno returns
as-is, so the current date, the skills list, memory guidance and the markdown instruction
never reached the model. These tests build a real agno Agent (in-memory db, no network)
and read the system message it would send.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from agno.agent._init import initialize_agent
from agno.db.in_memory import InMemoryDb
from agno.run import RunContext
from agno.session import AgentSession

from agents import Model
from agents.agent import get_agent
from api.services.models import PullPromptResponse
from db.agent_info_crud import AgentConfig
from db.db_models import SkillDB

TEMPLATE = "You are Demo, assisting {user_name}.\nBe concise."
TIMEZONE = "Asia/Kolkata"


def _system_message(config=None, db_skills=None) -> str:
    prompt = PullPromptResponse(name="demo", template=TEMPLATE, tags=[])
    knowledge = MagicMock()
    knowledge.get_dynamic_kb.return_value = None
    with (
        patch("agents.agent.get_agent_db", return_value=InMemoryDb()),
        patch("agents.agent.get_knowledge_service", return_value=knowledge),
    ):
        agent = get_agent(
            prompt,
            "user-1",
            "session-1",
            "",
            "tenant-1",
            timezone=TIMEZONE,
            model_id=Model.gpt_5_4_mini.value,
            config=config,
            db_skills=db_skills,
        )
    # What agno does at the start of a run (it sets add_memories_to_context, among others).
    initialize_agent(agent)
    if agent.memory_manager is not None:
        agent.memory_manager = MagicMock(get_user_memories=MagicMock(return_value=[]))
    run_context = RunContext(
        run_id="run-1", session_id="session-1", user_id="user-1", session_state={"user_name": "Alice"}
    )
    message = agent.get_system_message(session=AgentSession(session_id="session-1"), run_context=run_context)
    assert message is not None
    return str(message.content)


def test_stored_prompt_comes_first_with_placeholders_resolved():
    content = _system_message()
    assert content.startswith("You are Demo, assisting Alice.\nBe concise.")


def test_current_date_in_the_agents_timezone():
    now = datetime.now(ZoneInfo(TIMEZONE))
    content = _system_message()
    # Either side of midnight, in case the test runs across it.
    dates = {now.strftime("%Y-%m-%d"), (now + timedelta(minutes=1)).strftime("%Y-%m-%d")}
    assert "The current time is" in content
    assert any(f"The current time is {d}" in content for d in dates)
    assert "+05:30" in content


def test_markdown_instruction():
    assert "Use markdown to format your answers." in _system_message()


def test_skills_list_when_skills_are_attached():
    skill = SkillDB(id="refund-policy", name="Refund policy", description="How to answer refunds", instructions="-")
    content = _system_message(db_skills=[skill])
    assert "<name>refund-policy</name>" in content
    assert "How to answer refunds" in content
    assert "<skills_system>" not in _system_message()


def test_memory_guidance_follows_enable_memory():
    assert "<updating_user_memories>" in _system_message()
    assert "<updating_user_memories>" not in _system_message(config=AgentConfig(enable_memory=False))


def test_stored_prompt_is_not_the_agent_description():
    """A team leader lists each member's description, so the prompt must not live there."""
    prompt = PullPromptResponse(name="demo", template=TEMPLATE, tags=[])
    with patch("agents.agent.get_agent_db", return_value=InMemoryDb()), patch("agents.agent.get_knowledge_service"):
        agent = get_agent(prompt, "u", "s", "", "t", model_id=Model.gpt_5_4_mini.value)
    assert agent.description is None
    assert agent.system_message is None
    assert agent.instructions == TEMPLATE
