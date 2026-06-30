"""Regression tests for lifecycle hook failure and replacement semantics."""

import time
import unittest

from src.core.hooks import (
    HookAction,
    HookContext,
    HookDecision,
    HookDispatcher,
    HookFailureMode,
    HookPoint,
    HookRegistry,
    HookSpec,
)


class HookRegressionTest(unittest.TestCase):
    def test_replace_merges_payload_fields_for_following_hooks(self):
        seen = []

        class ReplaceArgs:
            def handle(self, _context):
                return HookDecision(
                    HookAction.REPLACE,
                    {"args": {"path": "safe.txt"}},
                )

        class Inspect:
            def handle(self, context):
                seen.append(dict(context.payload))
                return HookDecision()

        registry = HookRegistry()
        registry.register(HookSpec("replace", HookPoint.PERMISSION_REQUEST, ReplaceArgs(), priority=1))
        registry.register(HookSpec("inspect", HookPoint.PERMISSION_REQUEST, Inspect(), priority=2))
        registry.freeze()

        updated, _decision = HookDispatcher(registry).dispatch(HookContext(
            HookPoint.PERMISSION_REQUEST,
            payload={
                "args": {"path": "raw.txt"},
                "reason": "approval",
                "capabilities": ["file_read"],
            },
        ))

        self.assertEqual("approval", updated.payload["reason"])
        self.assertEqual(["file_read"], updated.payload["capabilities"])
        self.assertEqual("safe.txt", updated.payload["args"]["path"])
        self.assertEqual("approval", seen[0]["reason"])

    def test_closed_timeout_rejects_slow_hook(self):
        class SlowHook:
            def handle(self, _context):
                time.sleep(0.02)
                return HookDecision()

        registry = HookRegistry()
        registry.register(HookSpec(
            "slow",
            HookPoint.PRE_TOOL_USE,
            SlowHook(),
            failure_mode=HookFailureMode.CLOSED,
            timeout_seconds=0.001,
        ))
        registry.freeze()

        _context, decision = HookDispatcher(registry).dispatch(HookContext(HookPoint.PRE_TOOL_USE))
        self.assertEqual(HookAction.REJECT, decision.action)

    def test_open_failure_returns_visible_warning(self):
        class BrokenHook:
            def handle(self, _context):
                raise RuntimeError("boom")

        registry = HookRegistry()
        registry.register(HookSpec(
            "broken",
            HookPoint.PRE_TOOL_USE,
            BrokenHook(),
            failure_mode=HookFailureMode.OPEN,
        ))
        registry.freeze()

        _context, decision = HookDispatcher(registry).dispatch(HookContext(HookPoint.PRE_TOOL_USE))
        self.assertEqual(HookAction.WARN, decision.action)
        self.assertIn("failed open", decision.reason)


if __name__ == "__main__":
    unittest.main()