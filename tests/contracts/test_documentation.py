import re
import unittest
from pathlib import Path

from tests.support.paths import REPOSITORY_ROOT


class DocumentationStructureTest(unittest.TestCase):
    """Keep public contracts discoverable after documentation changes."""

    def setUp(self):
        self.root = REPOSITORY_ROOT

    def test_documentation_categories_and_api_contracts_exist(self):
        required = (
            "docs/README.md",
            "docs/api/ipc-protocol.md",
            "docs/api/rpc-reference.md",
            "docs/api/streaming-events.md",
            "docs/api/error-reference.md",
            "docs/api/tui-client-guide.md",
            "docs/architecture/core-architecture.md",
            "docs/decisions/cli-core-json-rpc.md",
            "docs/reference/configuration-reference.md",
            "docs/quality/testing-guide.md",
            "tests/README.md",
        )
        missing = [path for path in required if not (self.root / path).is_file()]
        self.assertEqual([], missing)

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
        handler_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (self.root / "src" / "core" / "handlers").glob("*.py")
        )
        methods = set(re.findall(r'router\.register\("([^"]+)"', handler_source))
        reference = (self.root / "docs" / "api" / "rpc-reference.md").read_text(
            encoding="utf-8"
        )
        undocumented = sorted(method for method in methods if f"`{method}`" not in reference)
        self.assertEqual([], undocumented)

    def test_repository_root_markdown_links_resolve(self):
        markdown_files = [
            self.root / "README.md",
            *(self.root / "docs").rglob("*.md"),
            self.root / "src" / "core" / "README.md",
        ]
        missing = []
        for source in markdown_files:
            text = source.read_text(encoding="utf-8")
            for target in re.findall(r"\]\((/[^)#]+)(?:#[^)]+)?\)", text):
                path = self.root / target.lstrip("/")
                if not path.exists():
                    missing.append(f"{source.relative_to(self.root)} -> {target}")
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
