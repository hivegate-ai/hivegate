"""Hand a user's tool-approval decisions to agno 3 in the shape it actually reads.

The gateway's commit endpoints let a user confirm, reject or edit the tool calls a
paused run is waiting on. They apply those edits to the paused run's ToolExecution
objects and then resumed the run with `acontinue_run(run_id=..., updated_tools=...)`.

agno 3 removed `updated_tools`. It still accepts it - `acontinue_run` takes **kwargs -
but never reads it: with no `requirements` it falls through to its own admin-approval
lookup, so the user's decision would be silently dropped. In an approval flow that is
the worst possible failure: a rejected tool could run, an approved one never would, and
nothing would say so.

agno 3 resumes from `requirements`: one RunRequirement per pending tool, each wrapping
its ToolExecution. On resume it rebuilds the run's tools from `req.tool_execution` by
tool_call_id (agent/_run.py), and a requirement counts as resolved once either its own
`confirmation` or its tool's `confirmed` is set. `requirements_for` therefore puts the
user's edited tool into each matching requirement and mirrors the decision onto the
requirement the way RunRequirement.confirm()/reject() do - the same job as agno's own
private `_sync_requirements_with_tools`, done here so it does not depend on a private
helper.
"""

from typing import Any, Callable, Dict, List, NamedTuple, Optional, Set

from agno.run.requirement import RunRequirement
from agno.tools import Toolkit


class ChatContext(NamedTuple):
    """The chat a run belongs to. Only a commit from the same chat may decide its tool calls."""

    api_key_id: Optional[int]  # None when auth is off
    user_id: str
    session_id: str
    target_id: str  # the agent or team id from the route


class CachedRun(NamedTuple):
    """A run kept for a later commit, with the chat that started it and the tools it was planned with."""

    run: Any
    context: Optional[ChatContext]  # None matches no commit
    tools: Optional[List[Any]] = None  # None (MCP chat, teams) resumes with the agent's current tools


def approved_targets(run: Any) -> Set[str]:
    """The targets agno recorded when the approved calls still to run were planned. Opaque: compare, don't parse."""
    return {tool.toolkit for tool in run.tools or [] if tool.confirmed and tool.result is None and tool.toolkit}


def unavailable_targets(
    targets: Set[str], tools: List[Any], connections: Callable[[List[str]], Dict[str, bool]]
) -> Set[str]:
    """The targets a run can't dispatch to: not among its tools, or disconnected since it was planned.

    `connections` says which of the given targets the user has connected now. It must not be
    answered from a token cache: a cached token outlives its connection. It should raise if it
    can't tell, so that an outage isn't taken for a disconnect.
    """
    toolkits = {tool.recorded_name: tool for tool in tools if isinstance(tool, Toolkit)}
    missing = targets - toolkits.keys()
    # A toolkit with no account behind it has nothing to disconnect.
    needed = [target for target in targets - missing if getattr(toolkits[target], "auth", False)]
    status = connections(needed) if needed else {}
    return missing | {target for target in needed if not status.get(target)}


def requirements_for(paused_run: Any, tools: Optional[List[Any]]) -> List[RunRequirement]:
    """RunRequirements carrying the user's decisions on `tools`, for `acontinue_run`.

    Starts from the paused run's own requirements, so anything agno attached to them is
    kept. A paused run cached before it carried requirements gets one per tool instead.
    """
    tools = list(tools or [])
    by_id = {t.tool_call_id: t for t in tools if getattr(t, "tool_call_id", None)}

    requirements = list(getattr(paused_run, "requirements", None) or [])
    if not requirements:
        requirements = [RunRequirement(tool_execution=t) for t in tools]

    for requirement in requirements:
        tool = requirement.tool_execution
        call_id = getattr(tool, "tool_call_id", None)
        if call_id in by_id:
            tool = by_id[call_id]
            requirement.tool_execution = tool
        # Mirror RunRequirement.confirm()/reject(): the decision lives on both.
        if tool is not None and getattr(tool, "confirmed", None) is not None:
            requirement.confirmation = tool.confirmed
            requirement.confirmation_note = getattr(tool, "confirmation_note", None)
    return requirements
