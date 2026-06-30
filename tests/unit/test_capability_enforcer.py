"""Focused tests for hard tool capability enforcement."""

import unittest
from pathlib import Path
import uuid

from src.core.tools.catalog import (
    ApprovalRequirement,
    NetworkMode,
    SandboxMode,
    ToolAudience,
    ToolCapability,
    ToolRisk,
    ToolSpec,
)
from src.core.tools.security.enforcement import CapabilityEnforcer
from src.core.tools.security.models import ToolCallContext


class _Tool:
    name = "sample"


class CapabilityEnforcerTest(unittest.TestCase):
    def _context(self, root: str, args: dict, spec: ToolSpec) -> ToolCallContext:
        return ToolCallContext(
            "sample",
            "call-1",
            args,
            "workspace",
            "session",
            "execution",
            "run",
            "parent",
            spec,
            root,
        )

    def _spec(self, **overrides) -> ToolSpec:
        values = {
            "name": "sample",
            "tool": _Tool(),
            "audiences": frozenset({ToolAudience.PARENT}),
            "risk": ToolRisk.READ_ONLY,
            "capabilities": frozenset({ToolCapability.FILE_READ}),
            "approval": ApprovalRequirement.NONE,
        }
        values.update(overrides)
        return ToolSpec(**values)

    def test_rejects_path_escape_for_workspace_file_capability(self):
        root = Path(".test_tmp") / "capability-enforcer" / uuid.uuid4().hex
        root.mkdir(parents=True, exist_ok=False)
        outside = root.parent / "outside.txt"
        context = self._context(str(root), {"path": "../outside.txt"}, self._spec())
        with self.assertRaises(ValueError):
            CapabilityEnforcer().validate(context)
        self.assertFalse(outside.exists())

    def test_network_policy_denies_network_capability(self):
        spec = self._spec(
            capabilities=frozenset({ToolCapability.NETWORK_ACCESS}),
            network=NetworkMode.ALLOW,
        )
        context = self._context(".", {}, spec)
        with self.assertRaises(PermissionError):
            CapabilityEnforcer(network_policy="deny").validate(context)

    def test_host_full_access_skips_workspace_path_check_only_when_declared(self):
        spec = self._spec(
            sandbox=SandboxMode.HOST_FULL_ACCESS,
            approval=ApprovalRequirement.ALWAYS,
        )
        context = self._context(".", {"path": "C:/outside.txt"}, spec)
        CapabilityEnforcer().validate(context)


if __name__ == "__main__":
    unittest.main()