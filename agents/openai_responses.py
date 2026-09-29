"""OpenAI models through the Responses API, with GPT-6 recognised as a reasoning model.

Why not agno's OpenAIChat (Chat Completions), which this gateway used until now: the
GPT-6 family supports function calling on Chat Completions **only** with
``reasoning_effort="none"``. Every gateway agent carries tools, so on Chat Completions a
GPT-6 model either rejects the tool list or has to run with reasoning switched off. The
Responses API has no such restriction and is what OpenAI documents for these models.

Why a subclass: agno 3.0.11 decides whether a model reasons by id prefix
(``o3``/``o4-mini``/``gpt-5``). A reasoning model's function calls must be sent back with
the reasoning item that produced them - agno does that (response chaining, or replaying
encrypted reasoning) only for ids it recognises. ``gpt-6-*`` is not one, so a second tool
round-trip would go out without its reasoning. This widens the check to every GPT
generation from 5 on, so the next one does not need another patch.
"""

import re
from dataclasses import dataclass

from agno.models.openai import OpenAIResponses

from agents.provider_models import StreamRestartGuard

_REASONING_GPT = re.compile(r"^gpt-(\d+)")


def is_reasoning_model_id(model_id: str) -> bool:
    if model_id.startswith(("o3", "o4-mini")):
        return True
    m = _REASONING_GPT.match(model_id)
    return m is not None and int(m.group(1)) >= 5


@dataclass
class ReasoningAwareOpenAIResponses(StreamRestartGuard, OpenAIResponses):
    def _using_reasoning_model(self) -> bool:
        return is_reasoning_model_id(self.id)


# agno/reasoning/openai.py recognises an OpenAI reasoning model by class *name*
# ("OpenAIResponses"); keep it, as agents/claude_refusal.py does for Claude.
ReasoningAwareOpenAIResponses.__name__ = "OpenAIResponses"
ReasoningAwareOpenAIResponses.__qualname__ = "OpenAIResponses"
