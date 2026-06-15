import argparse
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.cli.commands import register_commands
from tests.support.paths import REPOSITORY_ROOT


def _registered_rpc_methods(root: Path) -> set[str]:
    handler_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src" / "core" / "handlers").glob("*.py")
    )
    return set(re.findall(r'router\.register\("([^"]+)"', handler_source))


def _documented_rpc_methods(reference: str) -> set[str]:
    return set(
        re.findall(
            r"^\| `((?:agent|core|session)\.[^`]+)` \|",
            reference,
            flags=re.MULTILINE,
        )
    )


def _registered_cli_commands() -> set[str]:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    register_commands(subparsers, SimpleNamespace(default_session_id="default"))
    return _leaf_argparse_commands(parser, "learn-agent")


def _leaf_argparse_commands(parser: argparse.ArgumentParser, prefix: str) -> set[str]:
    commands = set()
    nested = []
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            nested.extend(choices.items())
    if not nested:
        return {prefix}
    for name, command_parser in nested:
        commands.update(_leaf_argparse_commands(command_parser, f"{prefix} {name}"))
    return commands


def _registered_core_commands(root: Path) -> set[str]:
    source = (root / "src" / "core" / "main.py").read_text(encoding="utf-8")
    return {
        f"learn-agent-core {name}"
        for name in re.findall(r'subparsers\.add_parser\(\s*"([^"]+)"', source)
    }


def _markdown_anchor(heading: str) -> str:
    value = re.sub(r"`([^`]*)`", r"\1", heading.strip().lower())
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"[^\w\u4e00-\u9fff -]", "", value)
    return re.sub(r" +", "-", value)


def _document_anchors(path: Path) -> set[str]:
    anchors = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match:
            anchors.add(_markdown_anchor(match.group(1)))
    return anchors


class DocumentationStructureTest(unittest.TestCase):
    """Keep public contracts discoverable after documentation changes."""

    def setUp(self):
        self.root = REPOSITORY_ROOT

    def test_documentation_categories_and_api_contracts_exist(self):
        required = (
            "docs/README.md",
            "docs/product/project-overview.md",
            "docs/product/functional-requirements.md",
            "docs/product/roadmap-and-known-limitations.md",
            "docs/governance/documentation-management.md",
            "docs/governance/document-register.md",
            "docs/governance/document-template.md",
            "docs/governance/decision-record-template.md",
            "docs/architecture/system-overview.md",
            "docs/architecture/security-model.md",
            "docs/api/ipc-protocol.md",
            "docs/api/rpc-reference.md",
            "docs/api/streaming-events.md",
            "docs/api/error-reference.md",
            "docs/api/tui-client-guide.md",
            "docs/api/cli-reference.md",
            "docs/api/protocol-compatibility.md",
            "docs/api/extension-guide.md",
            "docs/architecture/core-architecture.md",
            "docs/decisions/cli-core-json-rpc.md",
            "docs/reference/configuration-reference.md",
            "docs/development/development-guide.md",
            "docs/development/change-management.md",
            "docs/development/release-process.md",
            "docs/operations/runbook.md",
            "docs/operations/backup-and-restore.md",
            "docs/operations/upgrade-and-rollback.md",
            "docs/quality/testing-guide.md",
            "CONTRIBUTING.md",
            "tests/README.md",
        )
        self.assertEqual(len(required), len(set(required)), "required docs contain duplicates")
        missing = [path for path in required if not (self.root / path).is_file()]
        self.assertEqual([], missing)

    def test_governed_documentation_categories_exist(self):
        expected = {
            "api",
            "architecture",
            "decisions",
            "development",
            "governance",
            "history",
            "operations",
            "product",
            "quality",
            "reference",
        }
        actual = {
            path.name
            for path in (self.root / "docs").iterdir()
            if path.is_dir()
        }
        self.assertTrue(expected.issubset(actual), expected - actual)

    def test_core_authoritative_documents_declare_current_status(self):
        documents = (
            "docs/README.md",
            "docs/product/project-overview.md",
            "docs/product/functional-requirements.md",
            "docs/product/roadmap-and-known-limitations.md",
            "docs/architecture/system-overview.md",
            "docs/architecture/security-model.md",
            "docs/api/rpc-reference.md",
            "docs/api/streaming-events.md",
            "docs/operations/runbook.md",
            "docs/operations/backup-and-restore.md",
            "docs/governance/documentation-management.md",
            "docs/governance/document-register.md",
        )
        missing_status = [
            path
            for path in documents
            if "文档状态：Current"
            not in (self.root / path).read_text(encoding="utf-8")
        ]
        self.assertEqual([], missing_status)

    def test_managed_documents_declare_a_status(self):
        managed_categories = (
            "api",
            "architecture",
            "decisions",
            "development",
            "governance",
            "operations",
            "product",
            "quality",
            "reference",
        )
        missing_status = []
        for category in managed_categories:
            for path in (self.root / "docs" / category).glob("*.md"):
                if "文档状态：" not in path.read_text(encoding="utf-8"):
                    missing_status.append(str(path.relative_to(self.root)))
        self.assertEqual([], sorted(missing_status))

    def test_test_suite_uses_documented_categories(self):
        allowed = {"unit", "integration", "contracts", "optional", "support", "fixtures"}
        tests_root = self.root / "tests"
        unexpected = sorted(
            path.name
            for path in tests_root.iterdir()
            if path.is_dir() and not path.name.startswith((".", "__")) and path.name not in allowed
        )
        root_tests = sorted(path.name for path in tests_root.glob("test_*.py"))

        self.assertEqual([], unexpected)
        self.assertEqual([], root_tests)

    def test_registered_rpc_methods_are_in_the_rpc_reference(self):
        methods = _registered_rpc_methods(self.root)
        reference = (self.root / "docs" / "api" / "rpc-reference.md").read_text(
            encoding="utf-8"
        )
        documented = _documented_rpc_methods(reference)
        self.assertEqual(methods, documented)

    def test_registered_cli_commands_are_in_the_cli_reference(self):
        reference = (self.root / "docs" / "api" / "cli-reference.md").read_text(
            encoding="utf-8"
        )
        commands = _registered_cli_commands() | _registered_core_commands(self.root)
        undocumented = sorted(command for command in commands if f"`{command}`" not in reference)
        self.assertEqual([], undocumented)

    def test_repository_root_markdown_links_resolve(self):
        markdown_files = [
            self.root / "README.md",
            *(self.root / "docs").rglob("*.md"),
            self.root / "src" / "core" / "README.md",
        ]
        missing = []
        invalid_anchors = []
        for source in markdown_files:
            text = source.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                target_path, _, anchor = target.partition("#")
                if not target_path:
                    path = source
                elif target_path.startswith("/"):
                    path = self.root / target_path.lstrip("/")
                else:
                    path = (source.parent / target_path).resolve()
                    try:
                        path.relative_to(self.root)
                    except ValueError:
                        missing.append(f"{source.relative_to(self.root)} -> {target}")
                        continue
                if not path.exists():
                    missing.append(f"{source.relative_to(self.root)} -> {target}")
                    continue
                if anchor and path.suffix.lower() == ".md":
                    if anchor not in _document_anchors(path):
                        invalid_anchors.append(
                            f"{source.relative_to(self.root)} -> {target}"
                        )
        self.assertEqual([], missing)
        self.assertEqual([], invalid_anchors)


if __name__ == "__main__":
    unittest.main()
