"""Safe persistent-rule identities for Bash commands."""

import json


def command_rule_key(command: str) -> tuple[str, bool]:
    """Normalize one simple argv command; compound shell is not persistable."""
    try:
        import bashlex
        parts = bashlex.parse(command)
    except Exception:
        # Parser support is deliberately narrower than Bash. Any parser
        # failure must fail closed to an exact, non-persistable identity
        # instead of aborting the ToolNode (for example, quoted heredocs).
        return f"command-exact:{command.strip()}", False
    if len(parts) != 1 or parts[0].kind != "command":
        return f"command-exact:{command.strip()}", False
    words = []
    for node in parts[0].parts:
        if node.kind != "word" or getattr(node, "parts", None):
            return f"command-exact:{command.strip()}", False
        words.append(node.word)
    if not words:
        return f"command-exact:{command.strip()}", False
    key = json.dumps(words, ensure_ascii=False, separators=(",", ":"))
    return f"command-argv:{key}", True


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
