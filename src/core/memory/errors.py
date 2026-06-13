"""Memory subsystem exceptions exposed to higher application layers."""


class MemoryUnavailableError(RuntimeError):
    """Raised when the configured memory backend cannot be used."""
