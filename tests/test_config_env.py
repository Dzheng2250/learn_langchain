import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.config.env import env_bool, env_float, env_int, env_str


class EnvironmentValueTest(unittest.TestCase):
    def test_returns_defaults_when_values_are_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("fallback", env_str("DEMO_TEXT", "fallback"))
            self.assertEqual(7, env_int("DEMO_INT", 7))
            self.assertEqual(1.5, env_float("DEMO_FLOAT", 1.5))
            self.assertTrue(env_bool("DEMO_BOOL", True))

    def test_parses_typed_environment_values(self):
        with patch.dict(
            os.environ,
            {
                "DEMO_TEXT": "configured",
                "DEMO_INT": "12",
                "DEMO_FLOAT": "2.5",
                "DEMO_BOOL": "off",
            },
            clear=True,
        ):
            self.assertEqual("configured", env_str("DEMO_TEXT", "fallback"))
            self.assertEqual(12, env_int("DEMO_INT", 7))
            self.assertEqual(2.5, env_float("DEMO_FLOAT", 1.5))
            self.assertFalse(env_bool("DEMO_BOOL", True))

    def test_parses_short_boolean_values(self):
        with patch.dict(os.environ, {"DEMO_BOOL": "y"}, clear=True):
            self.assertTrue(env_bool("DEMO_BOOL", False))
        with patch.dict(os.environ, {"DEMO_BOOL": "n"}, clear=True):
            self.assertFalse(env_bool("DEMO_BOOL", True))

    def test_rejects_invalid_typed_environment_values(self):
        with patch.dict(os.environ, {"DEMO_INT": "many", "DEMO_BOOL": "maybe"}, clear=True):
            with self.assertRaisesRegex(ValueError, "DEMO_INT"):
                env_int("DEMO_INT", 1)
            with self.assertRaisesRegex(ValueError, "DEMO_BOOL"):
                env_bool("DEMO_BOOL", False)

    def test_core_entry_module_loads_user_env_before_settings(self):
        self._assert_entry_loads_user_env("src.core.main", "MEMORY_DB_NAME", "LEARN_AGENT_DB_NAME")

    def test_cli_entry_module_loads_user_env_before_settings(self):
        self._assert_entry_loads_user_env("src.cli.main", "CORE_PORT", "LEARN_AGENT_CORE_PORT", "19876")

    def test_generic_llm_variables_take_precedence_over_legacy_aliases(self):
        root = Path(__file__).resolve().parents[1]
        process_env = dict(os.environ)
        process_env.update(
            {
                "LEARN_AGENT_LLM_API_KEY": "generic-key",
                "LEARN_AGENT_LLM_BASE_URL": "https://generic.example/v1",
                "ALIYUN_API_KEY": "legacy-key",
                "ALIYUN_BASE_URL": "https://legacy.example/v1",
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from src.config.settings import LLM_API_KEY, LLM_BASE_URL; "
                    "print(LLM_API_KEY); print(LLM_BASE_URL)"
                ),
            ],
            cwd=root,
            env=process_env,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            ["generic-key", "https://generic.example/v1"],
            result.stdout.strip().splitlines(),
        )

    def test_legacy_llm_variables_remain_compatible(self):
        root = Path(__file__).resolve().parents[1]
        process_env = dict(os.environ)
        process_env.pop("LEARN_AGENT_LLM_API_KEY", None)
        process_env.pop("LEARN_AGENT_LLM_BASE_URL", None)
        process_env.update(
            {
                "ALIYUN_API_KEY": "legacy-key",
                "ALIYUN_BASE_URL": "https://legacy.example/v1",
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from src.config.settings import LLM_API_KEY, LLM_BASE_URL; "
                    "print(LLM_API_KEY); print(LLM_BASE_URL)"
                ),
            ],
            cwd=root,
            env=process_env,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            ["legacy-key", "https://legacy.example/v1"],
            result.stdout.strip().splitlines(),
        )

    def _assert_entry_loads_user_env(
        self,
        module: str,
        setting: str,
        environment_name: str,
        value: str = "loaded_before_settings",
    ):
        root = Path(__file__).resolve().parents[1]
        directory = root / ".test_tmp" / f"core-env-{uuid4().hex}"
        directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, directory, True)
        env_path = directory / ".env"
        env_path.write_text(f"{environment_name}={value}\n", encoding="utf-8")
        process_env = dict(os.environ)
        process_env["LEARN_AGENT_ENV_FILE"] = str(env_path)
        process_env.pop(environment_name, None)

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    f"import {module}; "
                    f"from src.config.settings import {setting}; "
                    f"print({setting})"
                ),
            ],
            cwd=root,
            env=process_env,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(value, result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
