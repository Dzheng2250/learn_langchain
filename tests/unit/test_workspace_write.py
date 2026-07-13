import os
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from langchain_core.tools import tool

from src.core.tools.catalog import (
    ApprovalRequirement, SandboxMode, ToolAudience, ToolCapability, ToolRisk, ToolSpec,
)
from src.core.tools.security.models import PolicyAction, ToolCallContext
from src.core.tools.security.policy import DefaultToolPolicyEngine
from src.core.tools.security.pipeline import ToolExecutionPipeline
from src.core.tools.command_changes import create_staged_command_tools
from src.core.tools.registry import create_workspace_toolset
from src.core.tools.workspace_write import create_workspace_write_tools
from src.core.tools.workspace import resolve_workspace_mutation_path
from src.core.tools.workspace import create_workspace_file_tools
from src.core.resource_activity import bind_resource_activity
from src.core.workspace.models import WorkspaceContext
from tests.support.model_providers import UnusedModelProvider
from tests.support.paths import REPOSITORY_ROOT


class WorkspaceWriteToolsTest(unittest.TestCase):
    def setUp(self):
        self.root = REPOSITORY_ROOT / ".test_tmp" / f"write-{uuid4().hex}"
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        tools = create_workspace_write_tools(self.root, max_bytes=64)
        self.write, self.replace, self.mkdir, self.move, self.delete = tools

    def test_write_replace_move_and_delete(self):
        result = self.write.invoke({"path": "src/demo.txt", "content": "alpha"})
        self.assertIn("5 bytes", result)
        self.assertEqual("alpha", (self.root / "src/demo.txt").read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            self.write.invoke({"path": "src/demo.txt", "content": "other"})
        self.replace.invoke({
            "path": "src/demo.txt", "old_text": "alpha", "new_text": "beta",
            "expected_count": 1,
        })
        self.assertEqual("beta", (self.root / "src/demo.txt").read_text(encoding="utf-8"))
        self.mkdir.invoke({"path": "archive"})
        self.move.invoke({"source": "src/demo.txt", "destination": "archive/demo.txt"})
        self.delete.invoke({"path": "archive/demo.txt"})
        self.assertFalse((self.root / "archive/demo.txt").exists())

    def test_resource_observation_uses_exact_ranges_and_both_move_uris(self):
        class Recorder:
            def __init__(self): self.items = []
            def record(self, _context, observation):
                self.items.append(observation)
                return f"activity-{len(self.items)}"

        recorder = Recorder()
        context = SimpleNamespace(tool_call_id="call", tool_name="test")
        target = self.root / "source.txt"
        target.write_text("one\ntwo\n", encoding="utf-8")
        read, _entire, _lite = create_workspace_file_tools(self.root)
        with bind_resource_activity(recorder, context):
            read.invoke({"path": "./source.txt", "start_line": 1, "max_lines": 20})
        self.assertEqual("exact", recorder.items[-1].observation_mode.value)
        with bind_resource_activity(recorder, context):
            self.move.invoke({"source": "source.txt", "destination": "moved.txt"})
        moves = [item for item in recorder.items if item.operation.value == "move"]
        self.assertEqual(
            ["workspace://source.txt", "workspace://moved.txt"],
            [item.resource_uri for item in moves],
        )
        self.assertEqual(["source", "destination"], [item.metadata["move_role"] for item in moves])
    def test_replacement_count_mismatch_is_non_mutating(self):
        target = self.root / "sample.txt"
        target.write_text("same same", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.replace.invoke({
                "path": "sample.txt", "old_text": "same", "new_text": "changed",
                "expected_count": 1,
            })
        self.assertEqual("same same", target.read_text(encoding="utf-8"))

    def test_write_rejects_escape_sensitive_path_and_size_limit(self):
        for path in ("../outside.txt", ".env", ".git/config", ".agent_runtime/state.db"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.write.invoke({"path": path, "content": "secret"})
        with self.assertRaises(ValueError):
            self.write.invoke({"path": "large.txt", "content": "x" * 65})

    def test_mutations_reject_symbolic_links_without_touching_their_targets(self):
        target = self.root / "target.txt"
        target.write_text("preserved", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symbolic links are unavailable: {exc}")

        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.write.invoke({
                "path": "link.txt", "content": "changed", "overwrite": True,
            })
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.move.invoke({
                "source": "link.txt", "destination": "moved.txt",
            })
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.delete.invoke({"path": "link.txt"})

        self.assertTrue(link.is_symlink())
        self.assertEqual("preserved", target.read_text(encoding="utf-8"))

    def test_mutation_path_rejects_a_symbolic_parent_component(self):
        original = Path.is_symlink
        with patch.object(
            Path, "is_symlink",
            lambda path: path.name == "alias" or original(path),
        ):
            with self.assertRaisesRegex(ValueError, "symbolic links"):
                resolve_workspace_mutation_path(self.root, "alias/nested.txt")


    def test_recursive_operations_enforce_entry_limit(self):
        _write, _replace, _mkdir, _move, delete = create_workspace_write_tools(
            self.root, max_bytes=64, max_entries=1
        )
        nested = self.root / "many"
        nested.mkdir()
        (nested / "one.txt").write_text("1", encoding="utf-8")
        (nested / "two.txt").write_text("2", encoding="utf-8")
        with self.assertRaises(ValueError):
            delete.invoke({"path": "many", "recursive": True})
        self.assertTrue(nested.exists())
    def test_recursive_delete_must_be_explicit(self):
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "file.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(OSError):
            self.delete.invoke({"path": "nested"})
        self.delete.invoke({"path": "nested", "recursive": True})
        self.assertFalse(nested.exists())

    def test_parent_receives_write_tools_but_subagent_does_not(self):
        @tool
        def delegate_to_subagent(task: str) -> str:
            """Test delegate."""
            return task

        with patch("src.core.tools.registry.create_delegate_tool", return_value=delegate_to_subagent):
            toolset = create_workspace_toolset(
                WorkspaceContext(uuid4(), self.root), UnusedModelProvider(),
                approval_repository=None,
            )
        parent_names = {tool.name for tool in toolset.parent_tools}
        child_names = {tool.name for tool in toolset.base_tools}
        self.assertIn("write_workspace_file", parent_names)
        self.assertIn("delete_workspace_path", parent_names)
        self.assertNotIn("write_workspace_file", child_names)
        self.assertNotIn("delete_workspace_path", child_names)


class WorkspaceWritePolicyTest(unittest.TestCase):
    class Rules:
        def __init__(self, effect=None):
            self.effect = effect

        def matching_rule(self, _context, _rule_key):
            return self.effect

    @staticmethod
    def context(tool, *, args, approval=ApprovalRequirement.POLICY):
        spec = ToolSpec(
            name=tool.name,
            tool=tool,
            audiences=frozenset({ToolAudience.PARENT}),
            risk=ToolRisk.CONTROLLED_EXECUTION,
            capabilities=frozenset({ToolCapability.FILE_WRITE}),
            approval=approval,
            sandbox=SandboxMode.WORKSPACE_WRITE,
        )
        return ToolCallContext(
            tool.name, "call", args, "workspace", "session", "execution", "run",
            "parent", spec, str(REPOSITORY_ROOT),
        )

    def test_destructive_write_requires_fresh_non_persistable_approval(self):
        root = REPOSITORY_ROOT / ".test_tmp" / f"policy-{uuid4().hex}"
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, True)
        write, _replace, _mkdir, _move, delete = create_workspace_write_tools(root, max_bytes=64)
        engine = DefaultToolPolicyEngine(self.Rules("allow"), approval_enabled=False)
        overwrite = self.context(write, args={"path": "a.txt", "content": "x", "overwrite": True})
        decision = engine.evaluate(overwrite, rule_key="workspace-write:write_workspace_file:.", persistable=True)
        self.assertEqual(PolicyAction.ASK, decision.action)
        self.assertFalse(decision.persistable)
        deletion = self.context(delete, args={"path": "a.txt", "recursive": False})
        decision = engine.evaluate(deletion, rule_key="workspace-write:delete_workspace_path:.", persistable=True)
        self.assertEqual(PolicyAction.ASK, decision.action)
        self.assertFalse(decision.persistable)

    def test_write_rule_identity_is_scoped_to_parent_path(self):
        root = REPOSITORY_ROOT / ".test_tmp" / f"identity-{uuid4().hex}"
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, True)
        write, *_rest = create_workspace_write_tools(root, max_bytes=64)
        context = self.context(write, args={"path": "src/package/file.py", "content": "x"})
        rule_key, persistable = ToolExecutionPipeline._rule_identity(context)
        self.assertEqual("workspace-write:write_workspace_file:src/package", rule_key)
        self.assertTrue(persistable)

class StagedCommandChangesTest(unittest.TestCase):
    def setUp(self):
        self.base = REPOSITORY_ROOT / ".test_tmp" / f"changes-{uuid4().hex}"
        self.root = self.base / "workspace"
        self.runtime = self.base / "runtime"
        self.root.mkdir(parents=True)
        (self.root / "sample.txt").write_text("before", encoding="utf-8")
        self.addCleanup(shutil.rmtree, self.base, True)

    def test_stage_does_not_mutate_workspace_and_apply_is_explicit(self):
        def fake_run(argv, **_kwargs):
            mount = next(value for value in argv if value.startswith("type=bind,source="))
            source = Path(mount.split(",target=", 1)[0].removeprefix("type=bind,source="))
            (source / "sample.txt").write_text("after", encoding="utf-8")
            (source / "new.bin").write_bytes(b"\x00\x01")
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        with patch.dict(os.environ, {"LEARN_AGENT_RUNTIME_DIR": str(self.runtime)}):
            stage, apply_changes, _discard = create_staged_command_tools(
                self.root, max_files=5, max_bytes=100
            )
            with patch("src.core.tools.command_changes.subprocess.run", side_effect=fake_run):
                result = stage.invoke({"command": "generate"})
            self.assertEqual("before", (self.root / "sample.txt").read_text(encoding="utf-8"))
            self.assertFalse((self.root / "new.bin").exists())
            change_set_id = result.split("Staged change set ", 1)[1].split(";", 1)[0]
            apply_changes.invoke({
                "change_set_id": change_set_id,
                "expected_changes": ["create:new.bin", "modify:sample.txt"],
            })
        self.assertEqual("after", (self.root / "sample.txt").read_text(encoding="utf-8"))
        self.assertEqual(b"\x00\x01", (self.root / "new.bin").read_bytes())


    def test_apply_rejects_tampered_staged_content(self):
        def fake_run(argv, **_kwargs):
            mount = next(value for value in argv if value.startswith("type=bind,source="))
            source = Path(mount.split(",target=", 1)[0].removeprefix("type=bind,source="))
            (source / "sample.txt").write_text("staged", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.dict(os.environ, {"LEARN_AGENT_RUNTIME_DIR": str(self.runtime)}):
            stage, apply_changes, _discard = create_staged_command_tools(
                self.root, max_files=5, max_bytes=100
            )
            with patch("src.core.tools.command_changes.subprocess.run", side_effect=fake_run):
                result = stage.invoke({"command": "change"})
            change_set_id = result.split("Staged change set ", 1)[1].split(";", 1)[0]
            staged = next(self.runtime.rglob(f"{change_set_id}/workspace/sample.txt"))
            staged.write_text("tampered", encoding="utf-8")
            with self.assertRaises(ValueError):
                apply_changes.invoke({
                    "change_set_id": change_set_id,
                    "expected_changes": ["modify:sample.txt"],
                })
        self.assertEqual("before", (self.root / "sample.txt").read_text(encoding="utf-8"))
    def test_apply_rejects_workspace_conflict(self):
        def fake_run(argv, **_kwargs):
            mount = next(value for value in argv if value.startswith("type=bind,source="))
            source = Path(mount.split(",target=", 1)[0].removeprefix("type=bind,source="))
            (source / "sample.txt").write_text("staged", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.dict(os.environ, {"LEARN_AGENT_RUNTIME_DIR": str(self.runtime)}):
            stage, apply_changes, _discard = create_staged_command_tools(
                self.root, max_files=5, max_bytes=100
            )
            with patch("src.core.tools.command_changes.subprocess.run", side_effect=fake_run):
                result = stage.invoke({"command": "change"})
            change_set_id = result.split("Staged change set ", 1)[1].split(";", 1)[0]
            (self.root / "sample.txt").write_text("user edit", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                apply_changes.invoke({
                    "change_set_id": change_set_id,
                    "expected_changes": ["modify:sample.txt"],
                })
        self.assertEqual("user edit", (self.root / "sample.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
