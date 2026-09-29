#!/usr/bin/env python3
"""Replay a stored agent run straight against the Anthropic API and report why it stopped.

agno keeps each run's messages but not the model's `stop_reason` or `stop_details`, so
a run that came back empty cannot say whether a safety classifier declined it, which
category, or whether the request itself was the problem. This rebuilds the stored
request, sends it with the official SDK, and prints exactly that - without going
through agno, which is the layer that loses the information.

It sends up to three variants, so the verdicts can be compared:

  main_full        the stored request as agno sent it: system prompt, the replayed
                   history (num_history_runs) and the current message
  main_no_history  the same, minus the history - isolates whether replayed history
                   is what triggers it
  reasoning_cot    (agno 2 only - agno 3 removed the step, and the variant is reported as
                   skipped) agno's manual chain-of-thought step: agno's reasoning prompt joined
                   before the agent's (with a space, as agno's format_messages does),
                   the same messages, and the ReasoningSteps structured output sent the
                   way agno sends it (beta `output_format`). Rendered from whichever
                   agno is installed; the version is printed.

Not replayed: the MCP tool definitions the live agent carries. A refusal is a verdict
on content, so they are unlikely to matter, but the replay is not byte-identical.

Content is never printed unless --show-text is given: these requests carry tenants'
financial data.

Usage (from the repo root, with its venv):

    GATEWAY_DB_URL=postgresql://... ANTHROPIC_API_KEY=sk-ant-... \\
      python scripts/replay_run_for_refusal.py \\
        --session-id '00000000-0000-0000-0000-000000000001:pf-budget-classifier:matcher:00000000-0000-0000-0000-000000000001' \\
        --source-model claude-sonnet-5-5

    # the same requests against a model that used to answer them, as a control
    ... --control-model claude-sonnet-5

Each call is billed. The classifier's requests run 10-35k input tokens, so a full
three-variant replay on Sonnet is well under a dollar.
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

VARIANTS = ("main_full", "main_no_history", "reasoning_cot")
DEFAULT_TABLE = "ai.a_budget_classifier_agent"
STRUCTURED_OUTPUTS_BETA = "structured-outputs-2025-11-13"


# --- loading the stored run ---------------------------------------------------------


def load_run_from_db(db_url: str, table: str, session_id: str, run_id: Optional[str], source_model: Optional[str]):
    """Return (run_id, run_model, messages) for one stored run."""
    import psycopg

    schema, _, name = table.rpartition(".")
    if not schema or not name.replace("_", "").isalnum() or not schema.replace("_", "").isalnum():
        raise SystemExit(f"--table must be schema.table, got {table!r}")

    sql = f"""
        SELECT r.elem->>'run_id', r.elem->>'model', r.elem->'messages'
          FROM {schema}.{name} s,
               jsonb_array_elements(s.runs) WITH ORDINALITY AS r(elem, ord)
         WHERE s.session_id = %(session_id)s
           AND (%(run_id)s::text IS NULL OR r.elem->>'run_id' = %(run_id)s)
           AND (%(model)s::text IS NULL OR r.elem->>'model' = %(model)s)
         ORDER BY (r.elem->>'created_at')::bigint
         LIMIT 1
    """
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(sql, {"session_id": session_id, "run_id": run_id, "model": source_model})
        row = cur.fetchone()
    if row is None:
        raise SystemExit("No matching run. Check --session-id, --run-id and --source-model.")
    found_run_id, run_model, messages = row
    if isinstance(messages, str):
        messages = json.loads(messages)
    return found_run_id, run_model, messages


def load_run_from_json(path: str):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("run_id", "from-json"), data.get("model"), data["messages"]


def split_messages(stored: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, str]], Dict[str, str], int]:
    """(system, history, current_user, dropped) from agno's stored message list.

    The stored run ends with the assistant reply that came back empty - that is the
    failure being investigated, so it is not part of the request. Empty history turns
    (earlier failed replies, replayed by num_history_runs) are dropped too: the API
    rejects empty content, which is also why they are counted and reported.
    """

    def text(m):
        c = m.get("content")
        return c if isinstance(c, str) else ("" if c is None else json.dumps(c, ensure_ascii=False))

    systems = [text(m) for m in stored if m.get("role") in ("system", "developer")]
    turns = [m for m in stored if m.get("role") in ("user", "assistant")]
    while turns and turns[-1].get("role") == "assistant":
        turns.pop()
    if not turns or turns[-1].get("role") != "user":
        raise SystemExit("Stored run has no final user message to replay.")

    current = {"role": "user", "content": text(turns[-1])}
    history, dropped = [], 0
    for m in turns[:-1]:
        body = text(m)
        if not body.strip():
            dropped += 1
            continue
        history.append({"role": m["role"], "content": body})
    return " ".join(s for s in systems if s), history, current, dropped


# --- agno's reasoning step ----------------------------------------------------------


class NoManualReasoning(RuntimeError):
    """The installed agno has no manual chain-of-thought step to replay.

    agno 3 removed it: a model without native thinking now gets a reasoning error
    event instead ("use ReasoningTools for manual chain-of-thought"). That step is the
    one Claude Sonnet 5.5 refused as reasoning_extraction, so on agno 3 there is
    nothing of that shape left to replay.
    """


def agno_reasoning_request(model_id: str) -> Tuple[str, Dict[str, Any], str]:
    """(reasoning system prompt, output_format, agno version) as agno would build them."""
    import agno
    from agno.models.anthropic import Claude

    try:
        from agno.reasoning.default import get_default_reasoning_agent
    except ModuleNotFoundError as e:
        raise NoManualReasoning("this agno has no manual chain-of-thought step (removed in agno 3)") from e
    from agno.reasoning.step import ReasoningSteps
    from agno.session import AgentSession

    agent = get_default_reasoning_agent(
        reasoning_model=Claude(id=model_id, api_key="unused-for-rendering"),
        min_steps=1,
        max_steps=10,
        telemetry=False,
    )
    if agent is None:
        raise SystemExit("agno did not build a reasoning agent")
    message = agent.get_system_message(session=AgentSession(session_id="replay"))
    try:
        from anthropic import transform_schema

        schema = transform_schema(ReasoningSteps.model_json_schema())
    except (ImportError, AttributeError):
        schema = ReasoningSteps.model_json_schema()
    version = getattr(agno, "__version__", None)
    if version is None:
        try:
            from importlib.metadata import version as dist_version

            version = dist_version("agno")
        except Exception:
            version = "unknown"
    return (message.content if message else ""), {"type": "json_schema", "schema": schema}, version


# --- the call -----------------------------------------------------------------------


def build_requests(variants, system, history, current, model_id) -> List[Tuple[str, Dict[str, Any], Optional[str]]]:
    requests: List[Tuple[str, Dict[str, Any], Optional[str]]] = []
    for variant in variants:
        if variant == "main_full":
            requests.append((variant, {"system": system, "messages": history + [current]}, None))
        elif variant == "main_no_history":
            requests.append((variant, {"system": system, "messages": [current]}, None))
        elif variant == "reasoning_cot":
            try:
                reasoning_system, output_format, version = agno_reasoning_request(model_id)
            except NoManualReasoning as e:
                requests.append((variant, {"skipped": str(e)}, None))
                continue
            requests.append(
                (
                    variant,
                    {
                        "system": " ".join(s for s in (reasoning_system, system) if s),
                        "messages": history + [current],
                        "extra_headers": {"anthropic-beta": STRUCTURED_OUTPUTS_BETA},
                        "extra_body": {"output_format": output_format},
                    },
                    f"agno {version}",
                )
            )
    return requests


def describe(variant: str, model_id: str, response=None, error: Optional[Exception] = None, show_text: bool = False):
    line: Dict[str, Any] = {"variant": variant, "model": model_id}
    if error is not None:
        line["error"] = f"{type(error).__name__}: {getattr(error, 'status_code', '')} {str(error)[:300]}".strip()
        return line
    details = getattr(response, "stop_details", None)
    text = "".join(getattr(b, "text", "") for b in (response.content or []) if getattr(b, "type", None) == "text")
    line.update(
        {
            "stop_reason": response.stop_reason,
            "category": getattr(details, "category", None),
            "explanation": getattr(details, "explanation", None),
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "text_chars": len(text),
            "request_id": getattr(response, "_request_id", None),
        }
    )
    if show_text:
        line["text_head"] = text[:300]
    return line


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session-id", help="Stored agno session id (required unless --from-json)")
    parser.add_argument("--run-id", help="A specific run; default is the earliest run matching --source-model")
    parser.add_argument("--source-model", help="Pick the earliest run that ran on this model, e.g. claude-sonnet-5-5")
    parser.add_argument("--model", help="Model to replay against (default: the run's own model)")
    parser.add_argument("--control-model", help="Also replay every variant against this model, as a control")
    parser.add_argument("--variants", default=",".join(VARIANTS), help=f"Comma list from {', '.join(VARIANTS)}")
    parser.add_argument("--table", default=DEFAULT_TABLE, help="agno session table (default %(default)s)")
    parser.add_argument("--db-url", default=os.environ.get("GATEWAY_DB_URL"), help="Default: $GATEWAY_DB_URL")
    parser.add_argument("--from-json", help="Load {model, messages} from a file instead of the database")
    parser.add_argument("--max-tokens", type=int, default=8192, help="As the gateway sends it (default 8192)")
    parser.add_argument("--dry-run", action="store_true", help="Build and describe the requests; call nothing")
    parser.add_argument("--show-text", action="store_true", help="Print the first 300 chars of each reply")
    args = parser.parse_args(argv)

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = sorted(set(variants) - set(VARIANTS))
    if unknown:
        parser.error(f"unknown variant(s): {', '.join(unknown)}")

    if args.from_json:
        run_id, run_model, stored = load_run_from_json(args.from_json)
    else:
        if not args.session_id or not args.db_url:
            parser.error("--session-id and --db-url (or $GATEWAY_DB_URL) are required unless --from-json")
        run_id, run_model, stored = load_run_from_db(
            args.db_url, args.table, args.session_id, args.run_id, args.source_model
        )

    system, history, current, dropped = split_messages(stored)
    targets = [args.model or run_model or args.source_model]
    if not targets[0]:
        parser.error("Cannot tell which model to replay against; pass --model")
    if args.control_model:
        targets.append(args.control_model)

    print(
        json.dumps(
            {
                "run_id": run_id,
                "run_model": run_model,
                "system_chars": len(system),
                "history_turns": len(history),
                "empty_history_turns_dropped": dropped,
                "current_message_chars": len(current["content"]),
                "not_replayed": "MCP tool definitions",
            },
            ensure_ascii=False,
        )
    )

    client = None
    if not args.dry_run:
        import anthropic

        client = anthropic.Anthropic()

    for model_id in targets:
        for variant, request, note in build_requests(variants, system, history, current, model_id):
            if "skipped" in request:
                print(json.dumps({"variant": variant, "model": model_id, "skipped": request["skipped"]}))
                continue
            shape = {
                "variant": variant,
                "model": model_id,
                "system_chars": len(request["system"]),
                "messages": len(request["messages"]),
            }
            if note:
                shape["rendered_with"] = note
            if args.dry_run:
                print(json.dumps({"dry_run": shape}, ensure_ascii=False))
                continue
            assert client is not None  # constructed above unless --dry-run
            try:
                response = client.messages.create(model=model_id, max_tokens=args.max_tokens, **request)
                print(json.dumps(describe(variant, model_id, response, show_text=args.show_text), ensure_ascii=False))
            except Exception as e:  # report and carry on: one variant failing is itself a finding
                print(json.dumps(describe(variant, model_id, error=e), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
