"""Transport-independent request context exposed to RPC handlers."""

from typing import Any, Protocol


class RequestContext(Protocol):
    """Capabilities available to a request handler."""

    request_id: str | int | None

    async def send_notification(self, value: Any) -> None:
        """Send one server-side notification associated with the request."""

    def request_close(self) -> None:
        """Request that the transport close after writing the final response."""
