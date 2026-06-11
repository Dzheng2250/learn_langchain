import shutil
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.cli.config import CliConfig
from src.cli.daemon import start_daemon, stop_daemon
from src.cli.errors import DaemonLifecycleError


class CliDaemonTest(unittest.TestCase):
    def test_start_daemon_uses_configured_startup_timeout(self):
        runtime = Path(".test_tmp") / f"daemon-{uuid4().hex}"
        runtime.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, runtime, True)
        config = replace(
            CliConfig.load(),
            runtime_dir=runtime.resolve(),
            daemon_startup_timeout_seconds=1,
        )
        with (
            patch("src.cli.daemon.daemon_status", return_value=None) as daemon_status,
            patch("src.cli.daemon.create_token"),
            patch("src.cli.daemon.subprocess.Popen"),
            patch("src.cli.daemon.time.monotonic", side_effect=[10, 12]),
            self.assertRaises(DaemonLifecycleError),
        ):
            start_daemon(config)

        self.assertEqual(1, daemon_status.call_count)

    def test_stop_daemon_uses_configured_stop_timeout(self):
        runtime = Path(".test_tmp") / f"daemon-{uuid4().hex}"
        runtime.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, runtime, True)
        config = replace(
            CliConfig.load(),
            runtime_dir=runtime.resolve(),
            daemon_stop_timeout_seconds=1,
        )
        with (
            patch("src.cli.daemon.CoreClient.request", return_value={"status": "shutting_down"}),
            patch("src.cli.daemon.daemon_status", return_value=None) as daemon_status,
            patch("src.cli.daemon.time.monotonic", side_effect=[10, 12]),
        ):
            result = stop_daemon(config)

        self.assertEqual("shutting_down", result["status"])
        self.assertEqual(1, daemon_status.call_count)


if __name__ == "__main__":
    unittest.main()
