import unittest

from src.core.database.queries import load_sql_file, split_sql_statements


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
            load_sql_file("../memory/store.py")

    def test_split_sql_statements_preserves_semicolons_inside_literals_and_comments(self) -> None:
        statements = split_sql_statements(
            """
            SELECT 'hello;world';
            -- comment containing ;
            SELECT 2;
            """
        )

        self.assertEqual(2, len(statements))
        self.assertIn("'hello;world'", statements[0])
        self.assertIn("SELECT 2", statements[1])

    def test_split_sql_statements_preserves_dollar_quoted_function_body(self) -> None:
        statements = split_sql_statements(
            """
            CREATE FUNCTION demo() RETURNS void AS $$
            BEGIN
                PERFORM 1;
                PERFORM 2;
            END;
            $$ LANGUAGE plpgsql;
            SELECT 3;
            """
        )

        self.assertEqual(2, len(statements))
        self.assertIn("PERFORM 2;", statements[0])
        self.assertEqual("SELECT 3", statements[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
