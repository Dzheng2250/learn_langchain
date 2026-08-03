"""Safe persistent-rule identities for Bash commands."""

import hashlib
import json


def command_rule_key(command: str) -> tuple[str, bool]:
    """Return a prefix identity for simple argv or an exact identity for compound shell."""
    normalized = command.strip()
    try:
        import bashlex
        parts = bashlex.parse(normalized)
    except Exception:
        # Parser support is deliberately narrower than Bash. Any parser
        # failure must fail closed to an exact, non-persistable identity
        # instead of aborting the ToolNode (for example, quoted heredocs).
        return _exact_command_key(normalized), False
    if len(parts) != 1 or parts[0].kind != "command":
        return _exact_command_key(normalized), True
    words = []
    for node in parts[0].parts:
        if node.kind != "word" or getattr(node, "parts", None):
            return _exact_command_key(normalized), True
        words.append(node.word)
    if not words:
        return _exact_command_key(normalized), False
    key = json.dumps(words, ensure_ascii=False, separators=(",", ":"))
    return f"command-argv:{key}", True


def _exact_command_key(command: str) -> str:
    """Build a content-free identity that only matches the same full command."""
    digest = hashlib.sha256(command.encode("utf-8")).hexdigest()
    return f"command-exact-sha256:{digest}"


def command_rule_argv(rule_key: str) -> tuple[str, ...] | None:
    """Decode a normalized command rule without accepting arbitrary JSON shapes."""
    if not rule_key.startswith("command-argv:"):
        return None
    try:
        value = json.loads(rule_key.removeprefix("command-argv:"))
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        return None
    return tuple(value)
