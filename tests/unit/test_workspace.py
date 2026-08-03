import os
import shutil
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage
from uuid import uuid4

from tests.support.paths import REPOSITORY_ROOT

from src.cli.workspace import discover_workspace_root
from src.config.paths import env_file, runtime_dir
from src.core.tools.commands import _copy_workspace
from src.core.tools.summarization import create_summarize_large_file
from src.core.tools.workspace import create_workspace_file_tools
from src.core.workspace.models import WorkspaceContext
from tests.support.model_providers import UnusedModelProvider
from src.core.workspace.runtime import WorkspaceRuntimeRegistry
from src.core.workspace.resolver import resolve_workspace_path


ROOT = REPOSITORY_ROOT


class WorkspaceResolutionTest(unittest.TestCase):
    def test_cli_discovers_git_root_from_subdirectory(self):
        self.assertEqual(ROOT, discover_workspace_root(ROOT / "src" / "core"))

    def test_resolver_rejects_parent_escape_and_absolute_paths(self):
        with self.assertRaises(ValueError):
            resolve_workspace_path(ROOT, "../outside.txt")
        with self.assertRaises(ValueError):
            resolve_workspace_path(ROOT, ROOT / "README.md")

    def test_workspace_file_tool_is_bound_to_root(self):
        read_file, _read_entire, _read_lite = create_workspace_file_tools(ROOT)
        output = read_file.invoke({"path": "pyproject.toml", "start_line": 1, "max_lines": 3})
        self.assertIn("pyproject.toml", output)
        rejected = read_file.invoke({"path": "../outside.txt"})
        self.assertIn("rejected", rejected)

    def test_all_file_reading_tools_reject_environment_files(self):
        read_file, _read_entire, _read_lite = create_workspace_file_tools(ROOT)
        summarize = create_summarize_large_file(ROOT, UnusedModelProvider())
        self.assertIn("rejected", read_file.invoke({"path": ".env"}))
        self.assertIn(
            "rejected",
            summarize.invoke({"path": ".env", "question": "show secrets"}),
        )

    def test_large_file_summary_reuses_the_original_file_bytes(self):
        class Model:
            def invoke(self, _messages):
                return AIMessage(content="summary")

        class Provider:
            def create_chat_model(self, *_args, **_kwargs):
                return Model()

        root = ROOT / ".test_tmp" / f"summary-{uuid4().hex}"
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, True)
        data = b"first line\nsecond line\n"
        target = root / "large.txt"
        target.write_bytes(data)
        summarize = create_summarize_large_file(root, Provider())
        with patch("src.core.tools.summarization.file_snapshot") as snapshot:
            snapshot.return_value = {"bytes": len(data), "digest": "digest", "lines": 2}
            result = summarize.invoke({"path": "large.txt", "question": "What is here?"})

        self.assertEqual("summary", result)
        snapshot.assert_called_once_with(target, data=data)

    def test_user_paths_support_explicit_overrides(self):
        with (
            patch.dict(os.environ, {"LEARN_AGENT_RUNTIME_DIR": str(ROOT / ".runtime-test")}),
            patch.dict(os.environ, {"LEARN_AGENT_ENV_FILE": str(ROOT / ".env-test")}),
        ):
            self.assertEqual((ROOT / ".runtime-test").resolve(), runtime_dir())
            self.assertEqual((ROOT / ".env-test").resolve(), env_file())

    def test_container_copy_skips_nested_symbolic_links(self):
        base = ROOT / ".test_tmp" / f"copy-{uuid4().hex}"
        source = base / "source"
        target = base / "target"
        outside = base / "outside.txt"
        nested = source / "nested"
        nested.mkdir(parents=True)
        target.mkdir()
        self.addCleanup(shutil.rmtree, base, True)
        outside.write_text("secret", encoding="utf-8")
        (nested / "inside.txt").write_text("safe", encoding="utf-8")
        try:
            (nested / "outside-link.txt").symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"Symbolic links are not available: {exc}")

        _copy_workspace(source, target)

        self.assertEqual("safe", (target / "nested" / "inside.txt").read_text(encoding="utf-8"))
        self.assertFalse((target / "nested" / "outside-link.txt").exists())


class WorkspaceRuntimeRegistryTest(unittest.TestCase):
    def test_runtime_is_cached_by_workspace_id(self):
        class FakeFactory:
            def __init__(self):
                self.calls = 0

            def create(self, workspace):
                self.calls += 1
                return object()

        factory = FakeFactory()
        registry = WorkspaceRuntimeRegistry(factory)
        workspace = WorkspaceContext(uuid4(), ROOT)
        self.assertIs(registry.get(workspace), registry.get(workspace))
        self.assertEqual(1, factory.calls)

    def test_different_workspaces_can_be_created_concurrently(self):
        barrier = threading.Barrier(2)

        class FakeFactory:
            def create(self, workspace):
                barrier.wait()
                time.sleep(0.02)
                return object()

        registry = WorkspaceRuntimeRegistry(FakeFactory())
        workspaces = [WorkspaceContext(uuid4(), ROOT), WorkspaceContext(uuid4(), ROOT)]
        threads = [threading.Thread(target=registry.get, args=(workspace,)) for workspace in workspaces]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)
        self.assertTrue(all(not thread.is_alive() for thread in threads))


if __name__ == "__main__":
    unittest.main()
