import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from src.cli.commands import trace


class TraceCliTest(unittest.TestCase):
    def _args(self, **overrides):
        values = {
            "run": None,
            "execution": None,
            "layer": None,
            "direction": None,
            "kind": None,
            "limit": 200,
            "follow": False,
            "raw": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_filters_and_prints_matching_records(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            root = Path(directory)
            path = root / "2026-06-14" / "daemon.jsonl"
            path.parent.mkdir(parents=True)
            records = [
                {"timestamp": "2026-06-14T10:00:00+00:00", "direction": "INTERNAL", "layer": "agent", "kind": "agent.run_started", "run_id": "a", "data": {}},
                {"timestamp": "2026-06-14T10:00:01+00:00", "direction": "PROVIDER_TO_CORE", "layer": "llm", "kind": "llm.response_finished", "run_id": "b", "data": {"output_tokens": 4}},
            ]
            path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            args = self._args(run="b", layer="llm")
            output = io.StringIO()
            with patch("src.cli.commands.trace.trace_dir", return_value=root), redirect_stdout(output):
                result = trace.run(args, None)
            self.assertEqual(0, result)
            self.assertIn("llm.response_finished", output.getvalue())
            self.assertNotIn("agent.run_started", output.getvalue())

    def test_follow_switches_to_new_utc_date_without_repeating_old_records(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            root = Path(directory)
            first = root / "2026-06-14" / "daemon.jsonl"
            second = root / "2026-06-15" / "daemon.jsonl"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text('{"kind":"old"}\n', encoding="utf-8")
            second.write_text('{"kind":"new"}\n', encoding="utf-8")
            paths = iter((first, second, second))
            output = io.StringIO()

            def stop_after_two_polls(_seconds):
                if stop_after_two_polls.calls == 1:
                    raise KeyboardInterrupt
                stop_after_two_polls.calls += 1

            stop_after_two_polls.calls = 0
            with redirect_stdout(output):
                trace._follow(
                    root,
                    self._args(raw=True),
                    path_provider=lambda: next(paths),
                    sleep=stop_after_two_polls,
                )

            self.assertNotIn('"kind": "old"', output.getvalue())
            self.assertEqual(1, output.getvalue().count('"kind": "new"'))

    def test_follow_retries_incomplete_last_line(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            root = Path(directory)
            path = root / "2026-06-14" / "daemon.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("", encoding="utf-8")
            output = io.StringIO()

            def complete_then_stop(_seconds):
                if complete_then_stop.calls == 0:
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write('{"kind":"partial"')
                    complete_then_stop.calls += 1
                    return
                if complete_then_stop.calls == 1:
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write('}\n')
                    complete_then_stop.calls += 1
                    return
                raise KeyboardInterrupt

            complete_then_stop.calls = 0
            with redirect_stdout(output):
                trace._follow(
                    root,
                    self._args(raw=True),
                    path_provider=lambda: path,
                    sleep=complete_then_stop,
                )

            self.assertEqual(1, output.getvalue().count('"kind": "partial"'))


if __name__ == "__main__":
    unittest.main()
