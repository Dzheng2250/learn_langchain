import ast
import unittest
from pathlib import Path

from tests.support.paths import REPOSITORY_ROOT


APPLICATION_PATHS = (
    REPOSITORY_ROOT / "src" / "core" / "agent",
    REPOSITORY_ROOT / "src" / "core" / "finalization",
    REPOSITORY_ROOT / "src" / "core" / "handlers",
)


class InterfaceBoundaryTest(unittest.TestCase):
    def test_application_layer_does_not_import_sqlite_or_adapters(self):
        violations = []
        for root in APPLICATION_PATHS:
            for path in root.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    else:
                        continue
                    for name in names:
                        if name == "sqlite3" or name.startswith("src.core.adapters"):
                            violations.append(f"{path.relative_to(REPOSITORY_ROOT)} imports {name}")

        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
