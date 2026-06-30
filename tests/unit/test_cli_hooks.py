import io
import json
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.cli.commands.hooks import run
from src.core.hooks.config import build_hook_dispatcher


def _tmp_dir() -> Path:
    root = Path(".test_tmp") / "cli-hooks" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


class CliHooksCommandTest(unittest.TestCase):
    def test_path_reports_default_hook_config_location(self):
        root = _tmp_dir()
        output = io.StringIO()
        with (
            patch("src.config.hooks.user_config_dir", return_value=root),
            redirect_stdout(output),
        ):
            code = run(SimpleNamespace(hooks_action="path", workspace=None), None)

        self.assertEqual(0, code)
        rendered = output.getvalue()
        self.assertIn("hooks_enabled=True", rendered)
        self.assertIn(str(root / "hooks.json"), rendered)
        self.assertIn("(missing)", rendered)

    def test_init_writes_safe_template_that_loader_accepts(self):
        root = _tmp_dir()
        target = root / "hooks.json"
        output = io.StringIO()
        with redirect_stdout(output):
            code = run(
                SimpleNamespace(
                    hooks_action="init",
                    path=str(target),
                    project=False,
                    workspace=None,
                    force=False,
                ),
                None,
            )

        self.assertEqual(0, code)
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual({}, document["hooks"])
        self.assertIn("PreToolUse", document["_examples"])
        build_hook_dispatcher((target,), enabled=True)
        self.assertIn(str(target), output.getvalue())

    def test_init_refuses_to_overwrite_without_force(self):
        root = _tmp_dir()
        target = root / "hooks.json"
        target.write_text('{"hooks": {}}\n', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Refusing to overwrite"):
            run(
                SimpleNamespace(
                    hooks_action="init",
                    path=str(target),
                    project=False,
                    workspace=None,
                    force=False,
                ),
                None,
            )


if __name__ == "__main__":
    unittest.main()