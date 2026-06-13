"""Typed skill manifest summaries and full local skill documents."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillManifest:
    """Small skill index entry used for discovery and selection."""

    directory: str
    name: str
    description: str


@dataclass(frozen=True)
class SkillDocument:
    """Full skill document loaded after a skill has been selected."""

    manifest: SkillManifest
    relative_path: str
    content: str
