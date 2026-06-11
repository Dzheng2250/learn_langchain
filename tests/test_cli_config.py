import unittest
from unittest.mock import patch

from src.cli.config import CliConfig


class CliConfigTest(unittest.TestCase):
    def test_loads_cli_transport_settings(self):
        config = CliConfig.load()
        self.assertTrue(config.core_host)
        self.assertGreater(config.core_port, 0)
        self.assertGreater(config.connect_timeout_seconds, 0)
        self.assertGreater(config.daemon_startup_timeout_seconds, 0)
        self.assertGreater(config.daemon_stop_timeout_seconds, 0)
        self.assertTrue(config.default_session_id)

    def test_rejects_non_loopback_host(self):
        with patch("src.cli.config.settings.CORE_HOST", "0.0.0.0"):
            with self.assertRaises(ValueError):
                CliConfig.load()

    def test_rejects_invalid_port(self):
        with patch("src.cli.config.settings.CORE_PORT", 70000):
            with self.assertRaises(ValueError):
                CliConfig.load()

    def test_rejects_invalid_daemon_timeouts(self):
        with patch("src.cli.config.settings.CORE_DAEMON_STARTUP_TIMEOUT_SECONDS", 0):
            with self.assertRaises(ValueError):
                CliConfig.load()
        with patch("src.cli.config.settings.CORE_DAEMON_STOP_TIMEOUT_SECONDS", 0):
            with self.assertRaises(ValueError):
                CliConfig.load()


if __name__ == "__main__":
    unittest.main()
