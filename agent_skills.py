import os
from dataclasses import dataclass

from agent_config import SKILL_FILE_NAME, SKILL_READ_OUTPUT_LIMIT, SKILLS_DIR


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


class LocalSkillStore:
    """Access local skills stored as skills/<skill_name>/SKILL.md."""

    def __init__(
        self,
        workspace_dir: str,
        skills_dir: str = SKILLS_DIR,
        skill_file_name: str = SKILL_FILE_NAME,
        output_limit: int = SKILL_READ_OUTPUT_LIMIT,
    ) -> None:
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.skills_dir = skills_dir
        self.skill_file_name = skill_file_name
        self.output_limit = output_limit
        self.skills_root = os.path.abspath(os.path.join(self.workspace_dir, self.skills_dir))

    def list_skill_names(self) -> list[str]:
        """Return skill directory names that contain a skill file."""
        if not os.path.isdir(self.skills_root):
            return []

        skill_names = []
        for name in sorted(os.listdir(self.skills_root)):
            skill_file = os.path.join(self.skills_root, name, self.skill_file_name)
            if os.path.isfile(skill_file):
                skill_names.append(name)

        return skill_names

    def list_manifests(self) -> list[SkillManifest]:
        """Return skill metadata from each SKILL.md."""
        manifests = []
        for directory in self.list_skill_names():
            skill_file = os.path.join(self.skills_root, directory, self.skill_file_name)
            with open(skill_file, "r", encoding="utf-8", errors="replace") as file:
                content = file.read()

            metadata = self._parse_skill_metadata(content)
            manifests.append(
                SkillManifest(
                    directory=directory,
                    name=metadata.get("name") or directory,
                    description=metadata.get("description") or "(no description)",
                )
            )

        return manifests

    def format_skill_list(self) -> str:
        """Return a tool-friendly summary of available skills."""
        if not os.path.isdir(self.skills_root):
            return f"No skills directory found: {self.skills_dir}"

        manifests = self.list_manifests()
        if not manifests:
            return f"No available skills found under {self.skills_dir}."

        lines = ["Available skills:"]
        for manifest in manifests:
            lines.append(
                f"- directory: {manifest.directory}\n"
                f"  name: {manifest.name}\n"
                f"  description: {manifest.description}"
            )

        return "\n".join(lines)

    def load_document(self, skill_name: str) -> SkillDocument:
        """Load one full skill document by directory or manifest name."""
        skill_file = self._resolve_skill_file(skill_name)
        directory = os.path.basename(os.path.dirname(skill_file))

        with open(skill_file, "r", encoding="utf-8", errors="replace") as file:
            content = file.read()

        metadata = self._parse_skill_metadata(content)
        manifest = SkillManifest(
            directory=directory,
            name=metadata.get("name") or directory,
            description=metadata.get("description") or "(no description)",
        )

        if len(content) > self.output_limit:
            content = content[:self.output_limit] + "\n... Skill content truncated ..."

        relative_path = os.path.relpath(skill_file, self.workspace_dir).replace("\\", "/")
        return SkillDocument(manifest=manifest, relative_path=relative_path, content=content)

    def read_skill(self, skill_name: str) -> str:
        """Read and format one skill document for tool output."""
        document = self.load_document(skill_name)
        return (
            f"Skill: {document.manifest.name}\n"
            f"Directory: {document.manifest.directory}\n"
            f"Description: {document.manifest.description}\n"
            f"File: {document.relative_path}\n\n"
            f"{document.content}"
        )

    def _resolve_skill_file(self, skill_name: str) -> str:
        """Resolve a skill name without allowing path escapes."""
        normalized = skill_name.strip().replace("\\", "/").strip("/")

        if not normalized or "/" in normalized or normalized == ".." or ".." in normalized.split("/"):
            raise ValueError("skill_name must be one directory name under the skills directory.")

        candidate = os.path.abspath(
            os.path.join(self.skills_root, normalized, self.skill_file_name)
        )

        if candidate.startswith(self.skills_root + os.sep) and os.path.isfile(candidate):
            return candidate

        matched_directory = self._find_directory_by_skill_name(normalized)
        if matched_directory:
            return os.path.abspath(
                os.path.join(self.skills_root, matched_directory, self.skill_file_name)
            )

        raise ValueError(f"Skill not found: {skill_name}")

    def _find_directory_by_skill_name(self, skill_name: str) -> str | None:
        """Find a skill directory by the name field in SKILL.md."""
        target = skill_name.casefold()
        for manifest in self.list_manifests():
            if manifest.name.casefold() == target:
                return manifest.directory

        return None

    def _parse_skill_metadata(self, content: str) -> dict[str, str]:
        """Parse name/description metadata from a skill file."""
        metadata = self._parse_frontmatter_metadata(content)
        if metadata:
            return metadata

        return self._parse_plain_metadata(content)

    def _parse_frontmatter_metadata(self, content: str) -> dict[str, str]:
        """Parse a small YAML-like frontmatter block without external dependencies."""
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}

        end_index = None
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_index = index
                break

        if end_index is None:
            return {}

        return self._parse_key_value_lines(lines[1:end_index])

    def _parse_plain_metadata(self, content: str) -> dict[str, str]:
        """Parse top-level name/description lines from the beginning of a skill file."""
        lines = content.splitlines()[:80]
        return self._parse_key_value_lines(lines)

    def _parse_key_value_lines(self, lines: list[str]) -> dict[str, str]:
        """Parse simple key: value and indented block values for name/description."""
        metadata: dict[str, str] = {}
        index = 0

        while index < len(lines):
            line = lines[index]
            stripped = line.strip()

            if not stripped or stripped.startswith("#") or ":" not in stripped:
                index += 1
                continue

            key, value = stripped.split(":", 1)
            key = key.strip().casefold()
            value = value.strip().strip("\"'")

            if key not in {"name", "description"}:
                index += 1
                continue

            if value in {"|", ">"}:
                block_lines = []
                index += 1
                while index < len(lines):
                    block_line = lines[index]
                    if block_line.strip() and not block_line.startswith((" ", "\t")):
                        break
                    block_lines.append(block_line.strip())
                    index += 1
                metadata[key] = " ".join(part for part in block_lines if part).strip()
                continue

            metadata[key] = value
            index += 1

        return metadata
