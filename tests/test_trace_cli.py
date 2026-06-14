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
            args = argparse.Namespace(
                run="b",
                execution=None,
                layer="llm",
                direction=None,
                kind=None,
                limit=200,
                follow=False,
                raw=False,
            )
            output = io.StringIO()
            with patch("src.cli.commands.trace.trace_dir", return_value=root), redirect_stdout(output):
                result = trace.run(args, None)
            self.assertEqual(0, result)
            self.assertIn("llm.response_finished", output.getvalue())
            self.assertNotIn("agent.run_started", output.getvalue())


if __name__ == "__main__":
    unittest.main()
