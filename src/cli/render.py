"""Terminal rendering for Agent stream events."""

from dataclasses import dataclass

from src.cli.errors import CliError


@dataclass
class AgentEventRenderer:
    """Render one request stream while avoiding duplicate final messages.

    Some providers expose incremental ``token`` events, while others only
    produce the completed ``step.agent_message``. The renderer accepts both:
    the completed message is a fallback only when no tokens were displayed.
    """

    received_token: bool = False

    def render(self, params: dict) -> None:
        """Render one ``agent.event`` notification without changing business state."""
        event = params.get("event")
        data = params.get("data", {})
        if event == "token":
            content = data.get("content", "")
            if content:
                self.received_token = True
                print(content, end="", flush=True)
        elif event == "step":
            step_type = data.get("type", "step")
            if step_type == "agent_message" and not self.received_token:
                print(data.get("content", ""), end="", flush=True)
            elif step_type in {"tool_call_start", "tool_call_result"}:
                print(f"\n[{step_type}: {data.get('tool') or ''}]", flush=True)
        elif event == "error":
            print(f"\nError: {data.get('message', 'Agent turn failed.')}", flush=True)


def render_agent_event(params: dict) -> None:
    """Render a standalone event; request streams should use AgentEventRenderer."""
    AgentEventRenderer().render(params)


def render_cli_error(error: CliError) -> None:
    """Render one expected CLI failure without a traceback."""
    print(f"Error: {error.message}")
    if error.hint:
        print(f"Hint: {error.hint}")
