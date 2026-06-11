"""User-facing CLI error hierarchy."""


class CliError(RuntimeError):
    """Expected CLI failure with a stable user-facing message."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigurationError(CliError):
    exit_code = 2


class CoreClientError(CliError):
    pass


class CoreUnavailableError(CoreClientError):
    pass


class CoreConnectionInterruptedError(CoreClientError):
    pass


class CoreProtocolError(CoreClientError):
    pass


class CoreAuthenticationError(CoreClientError):
    pass


class CoreRequestError(CoreClientError):
    pass


class DaemonLifecycleError(CliError):
    pass


class CliRenderError(CliError):
    pass
