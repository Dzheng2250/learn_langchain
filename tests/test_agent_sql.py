import unittest

from agent_sql import load_sql_file, split_sql_statements


class AgentSqlTest(unittest.TestCase):
    def test_split_sql_statements_ignores_blank_parts(self) -> None:
        statements = split_sql_statements(
            """
            SELECT 1;

            SELECT 2;
            """
        )

        self.assertEqual(["SELECT 1", "SELECT 2"], statements)

    def test_load_sql_file_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            load_sql_file("../agent_memory.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
