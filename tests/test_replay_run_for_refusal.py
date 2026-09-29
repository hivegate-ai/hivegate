"""scripts/replay_run_for_refusal.py: rebuilds a stored run faithfully and reports why it stopped."""

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from unittest.mock import patch

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "replay_run_for_refusal.py"
_spec = importlib.util.spec_from_file_location("replay_run_for_refusal", _PATH)
assert _spec is not None and _spec.loader is not None
replay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay)

# The shape agno stores: system, replayed history (including an earlier failure's
# empty reply), the current message, and the empty reply under investigation.
STORED = [
    {"role": "system", "content": "SYSTEM"},
    {"role": "user", "content": "old question", "from_history": True},
    {"role": "assistant", "content": "[]", "from_history": True},
    {"role": "user", "content": "failed question", "from_history": True},
    {"role": "assistant", "content": "", "from_history": True},
    {"role": "user", "content": "current question"},
    {"role": "assistant", "content": ""},
]


def _has_manual_reasoning():
    return importlib.util.find_spec("agno.reasoning.default") is not None


class SplitMessagesTest(unittest.TestCase):
    def test_separates_system_history_and_current(self):
        system, history, current, dropped = replay.split_messages(STORED)
        self.assertEqual("SYSTEM", system)
        self.assertEqual({"role": "user", "content": "current question"}, current)
        self.assertEqual(1, dropped, "the replayed empty reply is dropped - the API rejects empty content")
        self.assertEqual(["old question", "[]", "failed question"], [m["content"] for m in history])

    def test_the_failed_reply_itself_is_not_sent(self):
        _, history, current, _ = replay.split_messages(STORED)
        self.assertNotEqual("assistant", (history + [current])[-1]["role"])

    def test_no_final_user_message_is_an_error(self):
        with self.assertRaises(SystemExit):
            replay.split_messages([{"role": "system", "content": "s"}, {"role": "assistant", "content": "x"}])


class BuildRequestsTest(unittest.TestCase):
    def setUp(self):
        self.system, self.history, self.current, _ = replay.split_messages(STORED)

    def test_main_variants(self):
        built = dict(
            (v, r)
            for v, r, _ in replay.build_requests(
                ["main_full", "main_no_history"], self.system, self.history, self.current, "claude-sonnet-5-5"
            )
        )
        self.assertEqual(len(self.history) + 1, len(built["main_full"]["messages"]))
        self.assertEqual([self.current], built["main_no_history"]["messages"])
        self.assertEqual("SYSTEM", built["main_full"]["system"])

    def test_reasoning_variant_is_skipped_where_agno_has_no_manual_step(self):
        # agno 3 removed the manual chain-of-thought step; the variant must say so
        # rather than crash the whole replay.
        if _has_manual_reasoning():
            self.skipTest("this agno still has the manual step")
        [(variant, request, note)] = replay.build_requests(
            ["reasoning_cot"], self.system, self.history, self.current, "claude-sonnet-5-5"
        )
        self.assertIn("removed in agno 3", request["skipped"])

    def test_reasoning_variant_is_built_the_way_agno_builds_it(self):
        if not _has_manual_reasoning():
            self.skipTest("agno 3 has no manual chain-of-thought step to build")
        [(variant, request, note)] = replay.build_requests(
            ["reasoning_cot"], self.system, self.history, self.current, "claude-sonnet-5-5"
        )
        # agno's reasoning prompt comes first, joined to the agent's with a single space
        # (utils/models/claude.py format_messages: " ".join(system_messages)).
        self.assertTrue(request["system"].endswith(" SYSTEM"))
        self.assertIn("Reasoning Agent", request["system"])
        self.assertEqual("structured-outputs-2025-11-13", request["extra_headers"]["anthropic-beta"])
        self.assertEqual("json_schema", request["extra_body"]["output_format"]["type"])
        self.assertIn("reasoning_steps", json.dumps(request["extra_body"]["output_format"]["schema"]))
        self.assertTrue(note.startswith("agno "))


def _response(stop_reason, category=None, explanation=None, text="", output_tokens=0):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category, explanation=explanation) if category else None,
        content=[SimpleNamespace(type="text", text=text)] if text else [],
        usage=SimpleNamespace(input_tokens=33403, output_tokens=output_tokens),
        _request_id="req_test",
    )


class EndToEndTest(unittest.TestCase):
    """From a stored-run file to the printed verdicts, with the API faked."""

    def run_script(self, responses, *extra):
        with NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"run_id": "r1", "model": "claude-sonnet-5-5", "messages": STORED}, f)
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return responses.pop(0)

        fake = SimpleNamespace(messages=SimpleNamespace(create=create))
        out = io.StringIO()
        with patch("anthropic.Anthropic", return_value=fake), redirect_stdout(out):
            replay.main(["--from-json", f.name, *extra])
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        return lines, calls

    def test_reports_the_refusal_category_per_variant(self):
        lines, calls = self.run_script(
            [
                _response("refusal", "reasoning_extraction", "asks for reasoning"),
                _response("end_turn", text="[]", output_tokens=3),
            ],
            "--variants",
            "main_full,main_no_history",
        )
        header, full, no_history = lines
        self.assertEqual("r1", header["run_id"])
        self.assertEqual(1, header["empty_history_turns_dropped"])
        self.assertEqual(("refusal", "reasoning_extraction"), (full["stop_reason"], full["category"]))
        self.assertEqual("end_turn", no_history["stop_reason"])
        self.assertEqual(8192, calls[0]["max_tokens"], "sent the way the gateway sends it")

    def test_text_is_not_printed_by_default(self):
        lines, _ = self.run_script(
            [_response("end_turn", text="SECRET TENANT DATA", output_tokens=5)], "--variants", "main_full"
        )
        self.assertNotIn("SECRET", json.dumps(lines))

    def test_control_model_replays_every_variant_again(self):
        lines, calls = self.run_script(
            [_response("refusal", "cyber"), _response("end_turn", text="[]", output_tokens=3)],
            "--variants",
            "main_full",
            "--control-model",
            "claude-sonnet-5",
        )
        self.assertEqual(["claude-sonnet-5-5", "claude-sonnet-5"], [c["model"] for c in calls])

    def test_one_failing_variant_does_not_stop_the_others(self):
        def boom():
            raise RuntimeError("400 bad request")

        with NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"model": "claude-sonnet-5-5", "messages": STORED}, f)
        responses = [None, _response("end_turn", text="[]", output_tokens=3)]

        def create(**kwargs):
            r = responses.pop(0)
            if r is None:
                boom()
            return r

        out = io.StringIO()
        with (
            patch("anthropic.Anthropic", return_value=SimpleNamespace(messages=SimpleNamespace(create=create))),
            redirect_stdout(out),
        ):
            replay.main(["--from-json", f.name, "--variants", "main_full,main_no_history"])
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertIn("RuntimeError", lines[1]["error"])
        self.assertEqual("end_turn", lines[2]["stop_reason"])

    def test_dry_run_calls_nothing(self):
        with NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"model": "claude-sonnet-5-5", "messages": STORED}, f)
        out = io.StringIO()
        with (
            patch("anthropic.Anthropic", side_effect=AssertionError("must not construct a client")),
            redirect_stdout(out),
        ):
            replay.main(["--from-json", f.name, "--dry-run"])
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        built = len([line for line in lines if "dry_run" in line])
        skipped = len([line for line in lines if "skipped" in line])
        self.assertEqual(3, built + skipped)
        self.assertEqual(2 if not _has_manual_reasoning() else 3, built)


if __name__ == "__main__":
    unittest.main()
