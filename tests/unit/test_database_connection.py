import unittest
from unittest.mock import patch

from src.core.database import connection


class DatabaseConnectionConfigTest(unittest.TestCase):
    def test_builds_conninfo_from_split_settings(self):
        with (
            patch.object(connection, "MEMORY_DB_URL", ""),
            patch.object(connection, "MEMORY_DB_HOST", "db.local"),
            patch.object(connection, "MEMORY_DB_PORT", 5433),
            patch.object(connection, "MEMORY_DB_NAME", "agent"),
            patch.object(connection, "MEMORY_DB_USER", "agent_user"),
            patch.object(connection, "MEMORY_DB_PASSWORD", "secret"),
        ):
            kwargs = connection.connection_kwargs()
            conninfo = connection.connection_info()

        self.assertEqual("db.local", kwargs["host"])
        self.assertIn("host=db.local", conninfo)
        self.assertIn("dbname=agent", conninfo)

    def test_database_url_takes_precedence(self):
        url = "postgresql://url_user:url_password@db.example:5434/url_db"
        with patch.object(connection, "MEMORY_DB_URL", url):
            kwargs = connection.connection_kwargs()
            conninfo = connection.connection_info()

        self.assertEqual("db.example", kwargs["host"])
        self.assertEqual("5434", kwargs["port"])
        self.assertEqual("url_db", kwargs["dbname"])
        self.assertIn("user=url_user", conninfo)


if __name__ == "__main__":
    unittest.main()
