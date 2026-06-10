class SkillMetadataParser:
    """Parse name and description metadata from local skill documents."""

    def parse(self, content: str) -> dict[str, str]:
        metadata = self._parse_frontmatter(content)
        return metadata or self._parse_plain(content)

    def _parse_frontmatter(self, content: str) -> dict[str, str]:
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}

        end_index = next(
            (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        if end_index is None:
            return {}
        return self._parse_key_value_lines(lines[1:end_index])

    def _parse_plain(self, content: str) -> dict[str, str]:
        return self._parse_key_value_lines(content.splitlines()[:80])

    def _parse_key_value_lines(self, lines: list[str]) -> dict[str, str]:
        metadata: dict[str, str] = {}
        index = 0
        while index < len(lines):
            stripped = lines[index].strip()
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
