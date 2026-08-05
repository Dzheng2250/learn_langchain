"""Workspace patch protocol, matching, and transaction tests."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from langchain_core.tools import tool

from src.core.tools.workspace_patch import (
    WorkspacePatchEngine,
    WorkspacePatchError,
    apply_file_patch,
    parse_workspace_patch,
)
from src.core.tools.catalog import (
    ApprovalRequirement,
    ToolAudience,
    ToolCapability,
    ToolEffect,
    ToolRisk,
    ToolSpec,
)
from src.core.tools.observed import conflicting_mutation_calls
from src.core.tools.errors import ToolSideEffectUncertain


def _patch(*body: str) -> str:
    return "\n".join(("*** Begin Patch", *body, "*** End Patch"))


class WorkspacePatchParserTest(unittest.TestCase):
    def test_multiple_hunks_use_one_original_snapshot(self):
        parsed = parse_workspace_patch(_patch(
            "*** Update File: sample.py",
            "@@ first",
            " first",
            "-old-a",
            "+new-a",
            "@@ second",
            " second",
            "-old-b",
            "+new-b",
        ))
        updated, additions, deletions = apply_file_patch(
            "first\nold-a\ninserted-space\nsecond\nold-b\n",
            parsed.files[0],
        )
        self.assertEqual(
            updated,
            "first\nnew-a\ninserted-space\nsecond\nnew-b\n",
        )
        self.assertEqual((additions, deletions), (2, 2))

    def test_preserves_crlf_and_final_newline(self):
        parsed = parse_workspace_patch(_patch(
            "*** Update File: sample.txt",
            "@@",
            " one",
            "+middle",
            " two",
        ))
        updated, _adds, _deletes = apply_file_patch(
            "one\r\ntwo\r\n", parsed.files[0]
        )
        self.assertEqual(updated, "one\r\nmiddle\r\ntwo\r\n")

        without_final_newline, _adds, _deletes = apply_file_patch(
            "one\ntwo", parsed.files[0]
        )
        self.assertEqual(without_final_newline, "one\nmiddle\ntwo")

    def test_rejects_duplicate_path_ambiguous_context_and_noop(self):
        with self.assertRaisesRegex(WorkspacePatchError, "duplicate"):
            parse_workspace_patch(_patch(
                "*** Update File: sample.py", "@@", "-a", "+b",
                "*** Update File: sample.py", "@@", "-b", "+c",
            ))
        ambiguous = parse_workspace_patch(_patch(
            "*** Update File: sample.py", "@@", "-same", "+changed",
        ))
        with self.assertRaisesRegex(WorkspacePatchError, "ambiguous"):
            apply_file_patch("same\nmiddle\nsame\n", ambiguous.files[0])
        with self.assertRaisesRegex(WorkspacePatchError, "no changes"):
            parse_workspace_patch(_patch(
                "*** Update File: sample.py", "@@", " context",
            ))

    def test_rejects_add_delete_and_move_protocols(self):
        for header in (
            "*** Add File: new.py",
            "*** Delete File: old.py",
            "*** Move File: old.py -> new.py",
        ):
            with self.subTest(header=header), self.assertRaises(WorkspacePatchError):
                parse_workspace_patch(_patch(header, "@@", "+value"))


class WorkspacePatchEngineTest(unittest.TestCase):
    def test_applies_multiple_files_and_rejects_target_aliases(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "first.txt").write_text("one\n", encoding="utf-8")
            (root / "second.txt").write_text("two\n", encoding="utf-8")
            result = WorkspacePatchEngine(root, max_bytes=1000).apply(_patch(
                "*** Update File: first.txt", "@@", "-one", "+ONE",
                "*** Update File: second.txt", "@@", "-two", "+TWO",
            ))
            self.assertEqual((result.files, result.hunks), (2, 2))
            self.assertEqual((root / "first.txt").read_text(encoding="utf-8"), "ONE\n")
            self.assertEqual((root / "second.txt").read_text(encoding="utf-8"), "TWO\n")

            with self.assertRaisesRegex(WorkspacePatchError, "same target"):
                WorkspacePatchEngine(root, max_bytes=1000).apply(_patch(
                    "*** Update File: first.txt", "@@", "-ONE", "+one",
                    "*** Update File: ./first.txt", "@@", "-ONE", "+uno",
                ))

    def test_prevalidates_every_file_before_writing(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("one\n", encoding="utf-8")
            second.write_text("two\n", encoding="utf-8")
            source = _patch(
                "*** Update File: first.txt", "@@", "-one", "+changed",
                "*** Update File: second.txt", "@@", "-missing", "+changed",
            )
            with self.assertRaises(WorkspacePatchError):
                WorkspacePatchEngine(root, max_bytes=1000).apply(source)
            self.assertEqual(first.read_text(encoding="utf-8"), "one\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "two\n")

    def test_rolls_back_prior_files_when_later_replace_fails(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "a.txt"
            second = root / "b.txt"
            first.write_text("a\n", encoding="utf-8")
            second.write_text("b\n", encoding="utf-8")
            calls = 0

            def fail_second(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected commit failure")
                os.replace(source, destination)

            source = _patch(
                "*** Update File: a.txt", "@@", "-a", "+A",
                "*** Update File: b.txt", "@@", "-b", "+B",
            )
            with self.assertRaisesRegex(OSError, "injected"):
                WorkspacePatchEngine(
                    root, max_bytes=1000, replace=fail_second
                ).apply(source)
            self.assertEqual(first.read_text(encoding="utf-8"), "a\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "b\n")

    def test_incomplete_rollback_is_an_uncertain_side_effect(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.txt").write_text("a\n", encoding="utf-8")
            (root / "b.txt").write_text("b\n", encoding="utf-8")
            calls = 0

            def fail_commit_and_rollback(source, destination):
                nonlocal calls
                calls += 1
                if calls >= 2:
                    raise OSError("injected uncertainty")
                os.replace(source, destination)

            with self.assertRaisesRegex(ToolSideEffectUncertain, "rollback"):
                WorkspacePatchEngine(
                    root, max_bytes=1000, replace=fail_commit_and_rollback
                ).apply(_patch(
                    "*** Update File: a.txt", "@@", "-a", "+A",
                    "*** Update File: b.txt", "@@", "-b", "+B",
                ))

    def test_detects_target_change_before_commit(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "sample.txt"
            target.write_text("old\n", encoding="utf-8")

            class RacingEngine(WorkspacePatchEngine):
                def _write_temporaries(self, items):
                    result = super()._write_temporaries(items)
                    target.write_text("external\n", encoding="utf-8")
                    return result

            with self.assertRaisesRegex(WorkspacePatchError, "changed before commit"):
                RacingEngine(root, max_bytes=1000).apply(_patch(
                    "*** Update File: sample.txt", "@@", "-old", "+new",
                ))
            self.assertEqual(target.read_text(encoding="utf-8"), "external\n")

    def test_rejects_non_utf8_sensitive_and_symlink_targets(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "binary.dat").write_bytes(b"\xff")
            with self.assertRaisesRegex(WorkspacePatchError, "UTF-8"):
                WorkspacePatchEngine(root, max_bytes=1000).apply(_patch(
                    "*** Update File: binary.dat", "@@", "-x", "+y",
                ))
            (root / ".env").write_text("secret\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "blocked"):
                WorkspacePatchEngine(root, max_bytes=1000).apply(_patch(
                    "*** Update File: .env", "@@", "-secret", "+public",
                ))


class WorkspacePatchBatchTest(unittest.TestCase):
    def test_same_path_mutations_are_rejected_before_execution(self):
        @tool
        def patch_tool(patch: str) -> str:
            """Patch files."""
            return patch

        @tool
        def write_tool(path: str, content: str) -> str:
            """Write files."""
            return content

        common = dict(
            audiences=frozenset({ToolAudience.PARENT}),
            risk=ToolRisk.CONTROLLED_EXECUTION,
            capabilities=frozenset({ToolCapability.FILE_WRITE}),
            approval=ApprovalRequirement.POLICY,
            effect=ToolEffect.WORKSPACE_MUTATION,
        )
        specs = {
            "patch_tool": ToolSpec(
                name="patch_tool",
                tool=patch_tool,
                resource_resolver=lambda args: parse_workspace_patch(
                    args["patch"]
                ).paths,
                **common,
            ),
            "write_tool": ToolSpec(name="write_tool", tool=write_tool, **common),
        }
        patch_source = _patch(
            "*** Update File: same.py", "@@", "-old", "+new",
        )
        calls = [
            {"id": "patch", "name": "patch_tool", "args": {"patch": patch_source}},
            {"id": "write", "name": "write_tool", "args": {"path": "same.py", "content": "x"}},
            {"id": "other", "name": "write_tool", "args": {"path": "other.py", "content": "x"}},
        ]
        self.assertEqual(
            conflicting_mutation_calls(calls, specs), {"patch", "write"}
        )


if __name__ == "__main__":
    unittest.main()
