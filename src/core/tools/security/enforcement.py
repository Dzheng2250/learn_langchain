"""Hard capability boundaries that approvals cannot bypass."""

from pathlib import Path

from src.core.tools.catalog import NetworkMode, SandboxMode, ToolCapability
from src.core.workspace.resolver import resolve_workspace_path


class CapabilityEnforcer:
    PATH_KEYS = ("path", "file", "directory")

    def __init__(self, *, network_policy: str = "deny") -> None:
        self.network_policy = network_policy

    def validate(self, context) -> None:
        spec = context.spec
        if ToolCapability.NETWORK_ACCESS in spec.capabilities and spec.network == NetworkMode.DENY:
            raise PermissionError("Tool declares network access while network policy denies it.")
        if ToolCapability.NETWORK_ACCESS in spec.capabilities and self.network_policy == "deny":
            raise PermissionError("Core network policy denies tool network access.")
        if spec.sandbox == SandboxMode.HOST_FULL_ACCESS:
            return
        if not ({ToolCapability.FILE_READ, ToolCapability.FILE_WRITE} & spec.capabilities):
            return
        root = Path(context.workspace_root)
        for key in self.PATH_KEYS:
            value = context.args.get(key)
            if isinstance(value, str) and value:
                resolve_workspace_path(root, value)
