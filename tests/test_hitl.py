"""A user's tool-approval decision must reach agno and decide whether the tool runs.

agno 3 removed `updated_tools` from continue_run but still accepts it through **kwargs
and never reads it, so the gateway's commit flow would have resumed paused runs with the
user's confirm/reject silently dropped. These tests pin agents/hitl.requirements_for,
and then run a real agno Agent through pause -> decision -> resume to prove the decision
is what decides whether the tool executes.
"""

import asyncio
import unittest
from types import SimpleNamespace

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from agents.hitl import requirements_for
from agno.models.response import ToolExecution
from agno.run.requirement import RunRequirement


def tool(call_id="call_1", confirmed=None, note=None):
    return ToolExecution(
        tool_call_id=call_id,
        tool_name="delete_budget",
        tool_args={"budget_id": 7},
        requires_confirmation=True,
        confirmed=confirmed,
        confirmation_note=note,
    )


class RequirementsForTest(unittest.TestCase):
    def test_carries_a_confirmation(self):
        paused = SimpleNamespace(requirements=[RunRequirement(tool_execution=tool())])
        [req] = requirements_for(paused, [tool(confirmed=True)])
        self.assertTrue(req.tool_execution.confirmed)
        self.assertTrue(req.confirmation)
        self.assertFalse(req.needs_confirmation, "agno must see the requirement as resolved")

    def test_carries_a_rejection_and_its_note(self):
        paused = SimpleNamespace(requirements=[RunRequirement(tool_execution=tool())])
        [req] = requirements_for(paused, [tool(confirmed=False, note="not this one")])
        self.assertIs(req.confirmation, False)
        self.assertEqual("not this one", req.confirmation_note)
        self.assertFalse(req.needs_confirmation)

    def test_edited_arguments_reach_the_requirement(self):
        paused = SimpleNamespace(requirements=[RunRequirement(tool_execution=tool())])
        edited = tool(confirmed=True)
        edited.tool_args = {"budget_id": 8}
        [req] = requirements_for(paused, [edited])
        self.assertEqual({"budget_id": 8}, req.tool_execution.tool_args)

    def test_matched_by_tool_call_id_not_position(self):
        paused = SimpleNamespace(
            requirements=[RunRequirement(tool_execution=tool("a")), RunRequirement(tool_execution=tool("b"))]
        )
        reqs = requirements_for(paused, [tool("b", confirmed=False), tool("a", confirmed=True)])
        self.assertEqual({"a": True, "b": False}, {r.tool_execution.tool_call_id: r.confirmation for r in reqs})

    def test_a_paused_run_without_requirements_gets_one_per_tool(self):
        reqs = requirements_for(SimpleNamespace(requirements=None), [tool(confirmed=True)])
        self.assertEqual(1, len(reqs))
        self.assertTrue(reqs[0].confirmation)

    def test_an_undecided_tool_stays_pending(self):
        paused = SimpleNamespace(requirements=[RunRequirement(tool_execution=tool())])
        [req] = requirements_for(paused, [tool(confirmed=None)])
        self.assertTrue(req.needs_confirmation)


class _ScriptedClaude:
    """Anthropic client stub: first asks for the tool, then answers in text."""

    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return Message(
                id="m1",
                type="message",
                role="assistant",
                model="claude-sonnet-5-5",
                content=[ToolUseBlock(type="tool_use", id="toolu_1", name="delete_budget", input={"budget_id": 7})],
                stop_reason="tool_use",
                stop_sequence=None,
                usage=Usage(input_tokens=5, output_tokens=5),
            )
        return Message(
            id=f"m{self.calls}",
            type="message",
            role="assistant",
            model="claude-sonnet-5-5",
            content=[TextBlock(type="text", text="done")],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=5, output_tokens=1),
        )


class ApprovalThroughARealAgentTest(unittest.TestCase):
    """pause -> the user's decision via requirements_for -> resume, on real agno."""

    def run_with_decision(self, confirmed):
        from agno.agent import Agent
        from agno.db.in_memory import InMemoryDb
        from agno.tools import tool as agno_tool

        from agents.claude_refusal import RefusalAwareClaude

        executed = []

        @agno_tool(requires_confirmation=True)
        def delete_budget(budget_id: int) -> str:
            """Delete a budget."""
            executed.append(budget_id)
            return "deleted"

        model = RefusalAwareClaude(id="claude-sonnet-5-5", api_key="k")
        scripted = _ScriptedClaude()

        async def acreate(**kwargs):
            return scripted.create(**kwargs)

        model.get_async_client = lambda: SimpleNamespace(messages=SimpleNamespace(create=acreate))
        agent = Agent(model=model, tools=[delete_budget], db=InMemoryDb(), session_id="s1", telemetry=False)

        async def go():
            paused = await agent.arun("delete budget 7")
            self.assertTrue(paused.is_paused, "the tool requires confirmation, so the run must pause")
            # What the gateway's commit endpoint does: apply the user's decision to the
            # paused run's tools, then resume with requirements built from them.
            for t in paused.tools:
                t.confirmed = confirmed
            return await agent.acontinue_run(
                run_id=paused.run_id, requirements=requirements_for(paused, paused.tools), stream=False
            )

        result = asyncio.run(go())
        return executed, result

    def test_a_confirmed_tool_runs(self):
        executed, result = self.run_with_decision(True)
        self.assertEqual([7], executed)
        self.assertFalse(result.is_paused)

    def test_a_rejected_tool_does_not_run(self):
        executed, result = self.run_with_decision(False)
        self.assertEqual([], executed)
        self.assertFalse(result.is_paused)


if __name__ == "__main__":
    unittest.main()
