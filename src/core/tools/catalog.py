"""Structured registration and audience filtering for workspace tools."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property


class ToolAudience(StrEnum):
    """Agent roles allowed to receive a registered tool."""
    PARENT = "parent"
    SUBAGENT = "subagent"


class ToolRisk(StrEnum):
    """Static capability risk used for policy and future approvals."""
    READ_ONLY = "read_only"
    INTERNAL_STATE = "internal_state"
    CONTROLLED_EXECUTION = "controlled_execution"
    DELEGATION = "delegation"


class ToolCapability(StrEnum):
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    COMMAND_EXECUTION = "command_execution"
    NETWORK_ACCESS = "network_access"
    INTERNAL_STATE = "internal_state"
    DELEGATION = "delegation"


class ApprovalRequirement(StrEnum):
    NONE = "none"
    POLICY = "policy"
    ALWAYS = "always"
    FORBIDDEN = "forbidden"


class SandboxMode(StrEnum):
    ISOLATED_READ_ONLY = "isolated_read_only"
    WORKSPACE_WRITE = "workspace_write"
    HOST_FULL_ACCESS = "host_full_access"


class NetworkMode(StrEnum):
    DENY = "deny"
    ALLOWLIST = "allowlist"
    ALLOW = "allow"


@dataclass(frozen=True)
class ToolSpec:
    """Immutable tool registration metadata."""
    name: str
    tool: object | None
    audiences: frozenset[ToolAudience]
    risk: ToolRisk
    description: str = ""
    factory: Callable[[], object] | None = None
    capabilities: frozenset[ToolCapability] = frozenset()
    approval: ApprovalRequirement = ApprovalRequirement.NONE
    sandbox: SandboxMode = SandboxMode.ISOLATED_READ_ONLY
    network: NetworkMode = NetworkMode.DENY
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if (self.tool is None) == (self.factory is None):
            raise ValueError("ToolSpec requires exactly one of tool or factory")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("Tool timeout must be greater than zero")
        if self.network == NetworkMode.DENY and ToolCapability.NETWORK_ACCESS in self.capabilities:
            raise ValueError("Network-capable tools must declare a non-deny network mode")
        if self.sandbox == SandboxMode.HOST_FULL_ACCESS and self.approval != ApprovalRequirement.ALWAYS:
            raise ValueError("Host full-access tools must always require approval")

    def resolve_tool(self) -> object:
        return self._resolved_tool

    @cached_property
    def _resolved_tool(self) -> object:
        """Create a factory-backed tool once for this immutable registration."""
        if self.tool is not None:
            return self.tool
        assert self.factory is not None
        return self.factory()


class ToolRegistry:
    """Register tool metadata once and derive audience-specific tool views."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._frozen = False

    def register(self, spec: ToolSpec) -> None:
        """Register one unique tool before the registry is frozen."""
        if self._frozen:
            raise RuntimeError("Tool registry is frozen")
        if spec.name in self._specs:
            raise ValueError(f"Tool already registered: {spec.name}")
        actual_name = getattr(spec.resolve_tool(), "name", spec.name)
        if actual_name != spec.name:
            raise ValueError(f"ToolSpec name {spec.name!r} does not match tool name {actual_name!r}")
        self._specs[spec.name] = spec

    def freeze(self) -> None:
        """Prevent capability changes after a WorkspaceRuntime is built."""
        self._frozen = True

    def specs(self) -> tuple[ToolSpec, ...]:
        """Return an immutable snapshot of all registered specifications."""
        return tuple(self._specs[name] for name in sorted(self._specs))

    def specs_for(self, audience: ToolAudience) -> tuple[ToolSpec, ...]:
        """Return specifications visible to one Agent audience."""
        return tuple(spec for spec in self.specs() if audience in spec.audiences)

    def tools_for(self, audience: ToolAudience) -> list:
        """Return executable tool objects visible to one Agent audience."""
        return [spec.resolve_tool() for spec in self.specs_for(audience)]
