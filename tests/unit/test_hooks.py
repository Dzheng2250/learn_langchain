"""Tests for the system-level Agent lifecycle Hook runtime."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.hooks import (
    CommandHook, HookAction, HookContext, HookDecision, HookDispatcher,
    HookFailureMode, HookPoint, HookRegistry, HookSpec, build_hook_dispatcher,
)


class _Handler:
    def __init__(self, action=HookAction.CONTINUE, payload=None, calls=None):
        self.action = action
        self.payload = payload
        self.calls = calls

    def handle(self, context):
        if self.calls is not None:
            self.calls.append(context.subject)
        return HookDecision(self.action, self.payload)


class HookRuntimeTest(unittest.TestCase):
    def test_exposes_all_ten_agent_lifecycle_points(self):
        self.assertEqual(10, len(HookPoint))
        self.assertEqual({
            "SessionStart", "UserPromptSubmit", "PreToolUse",
            "PermissionRequest", "PostToolUse", "PreCompact", "PostCompact",
            "SubagentStart", "SubagentStop", "Stop",
        }, {point.value for point in HookPoint})

    def test_registry_orders_and_matches_hooks(self):
        calls = []
        registry = HookRegistry()
        registry.register(HookSpec(
            "later", HookPoint.PRE_TOOL_USE, _Handler(calls=calls),
            matcher="read_.*", priority=20,
        ))
        registry.register(HookSpec(
            "earlier", HookPoint.PRE_TOOL_USE, _Handler(calls=calls),
            matcher="read_file", priority=10,
        ))
        registry.freeze()
        HookDispatcher(registry).dispatch(HookContext(
            HookPoint.PRE_TOOL_USE, subject="read_file",
        ))
        self.assertEqual(["read_file", "read_file"], calls)
        self.assertEqual(["earlier", "later"], [
            spec.hook_id for spec in registry.matching(HookPoint.PRE_TOOL_USE, "read_file")
        ])

    def test_replacement_flows_to_next_hook(self):
        seen = []

        class Inspect:
            def handle(self, context):
                seen.append(context.payload["prompt"])
                return HookDecision()

        registry = HookRegistry()
        registry.register(HookSpec(
            "replace", HookPoint.USER_PROMPT_SUBMIT,
            _Handler(HookAction.REPLACE, {"prompt": "safe"}), priority=1,
        ))
        registry.register(HookSpec(
            "inspect", HookPoint.USER_PROMPT_SUBMIT, Inspect(), priority=2,
        ))
        registry.freeze()
        updated, _decision = HookDispatcher(registry).dispatch(HookContext(
            HookPoint.USER_PROMPT_SUBMIT, payload={"prompt": "raw"},
        ))
        self.assertEqual("safe", updated.payload["prompt"])
        self.assertEqual(["safe"], seen)

    def test_invalid_action_fails_closed_when_configured(self):
        registry = HookRegistry()
        registry.register(HookSpec(
            "invalid", HookPoint.POST_COMPACT,
            _Handler(HookAction.REJECT), failure_mode=HookFailureMode.CLOSED,
        ))
        registry.freeze()
        _context, decision = HookDispatcher(registry).dispatch(
            HookContext(HookPoint.POST_COMPACT)
        )
        self.assertEqual(HookAction.REJECT, decision.action)

    def test_command_hook_uses_json_stdin_and_stdout_without_shell(self):
        script = (
            "import json,sys; data=json.load(sys.stdin); "
            "print(json.dumps({'action':'replace','payload':{'prompt':data['payload']['prompt'].upper()}}))"
        )
        decision = CommandHook((sys.executable, "-c", script)).handle(HookContext(
            HookPoint.USER_PROMPT_SUBMIT, payload={"prompt": "hello"},
        ))
        self.assertEqual(HookAction.REPLACE, decision.action)
        self.assertEqual("HELLO", decision.payload["prompt"])

    def test_json_config_builds_command_hook(self):
        path = Path("configured-hooks.json").resolve()
        document = json.dumps({"hooks": {"Stop": [{"hooks": [{
                "id": "stop-check", "type": "command",
                "command": [sys.executable, "-c", "print('{}')"],
            }]}]}})
        with patch.object(Path, "is_file", return_value=True), patch.object(
            Path, "read_text", return_value=document,
        ):
            dispatcher = build_hook_dispatcher([path])
            _context, decision = dispatcher.dispatch(HookContext(HookPoint.STOP))
        self.assertEqual(HookAction.CONTINUE, decision.action)


if __name__ == "__main__":
    unittest.main()
