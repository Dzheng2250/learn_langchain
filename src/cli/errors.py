"""User-facing CLI error hierarchy."""


class CliError(RuntimeError):
    """Expected CLI failure with a stable user-facing message."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigurationError(CliError):
    """Invalid local configuration detected before command dispatch."""
    exit_code = 2


class CoreClientError(CliError):
    """Base class for expected CLI-to-Core communication failures."""
    pass


class CoreUnavailableError(CoreClientError):
    """Core cannot be reached or its runtime credentials are unavailable."""
    pass


class CoreConnectionInterruptedError(CoreClientError):
    """An established Core connection ended before the final response."""
    pass


class CoreProtocolError(CoreClientError):
    """Core returned malformed or incompatible JSON-RPC data."""
    pass


class CoreAuthenticationError(CoreClientError):
    """Core rejected the user-level runtime authentication token."""
    pass


class CoreRequestError(CoreClientError):
    """Core accepted transport data but rejected the requested operation."""
    pass


class DaemonLifecycleError(CliError):
    """Starting or stopping the Core daemon did not complete as expected."""
    pass


class CliRenderError(CliError):
    """A streamed event could not be rendered to the terminal."""
    pass
