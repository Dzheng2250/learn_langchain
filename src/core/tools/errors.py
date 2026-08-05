"""Tool-domain control errors shared by implementations and execution policy."""


class ToolSideEffectUncertain(RuntimeError):
    """A tool may have produced only part of its declared side effects."""


__all__ = ["ToolSideEffectUncertain"]
