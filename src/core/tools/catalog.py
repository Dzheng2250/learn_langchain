"""Structured registration and audience filtering for workspace tools."""

from dataclasses import dataclass
from enum import StrEnum


class ToolAudience(StrEnum):
    """Agent roles allowed to receive a registered tool."""
    PARENT = "parent"
    SUBAGENT = "subagent"


class ToolRisk(StrEnum):
    """Static capability risk used for policy and future approvals."""
    READ_ONLY = "read_only"
    CONTROLLED_EXECUTION = "controlled_execution"
    DELEGATION = "delegation"


@dataclass(frozen=True)
class ToolSpec:
    """Immutable tool registration metadata."""
    name: str
    tool: object
    audiences: frozenset[ToolAudience]
    risk: ToolRisk
    description: str = ""


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
        self._specs[spec.name] = spec

    def freeze(self) -> None:
        """Prevent capability changes after a WorkspaceRuntime is built."""
        self._frozen = True

    def specs(self) -> tuple[ToolSpec, ...]:
        """Return an immutable snapshot of all registered specifications."""
        return tuple(self._specs.values())

    def specs_for(self, audience: ToolAudience) -> tuple[ToolSpec, ...]:
        """Return specifications visible to one Agent audience."""
        return tuple(spec for spec in self._specs.values() if audience in spec.audiences)

    def tools_for(self, audience: ToolAudience) -> list:
        """Return executable tool objects visible to one Agent audience."""
        return [spec.tool for spec in self.specs_for(audience)]
