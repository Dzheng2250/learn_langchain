"""Terminal rendering for Agent stream events."""

from src.cli.errors import CliError


def render_agent_event(params: dict) -> None:
    """Render one ``agent.event`` notification without changing business state."""
    event = params.get("event")
    data = params.get("data", {})
    if event == "token":
        print(data.get("content", ""), end="", flush=True)
    elif event == "step":
        step_type = data.get("type", "step")
        if step_type in {"tool_call_start", "tool_call_result"}:
            print(f"\n[{step_type}: {data.get('tool') or ''}]", flush=True)
    elif event == "error":
        print(f"\nError: {data.get('message', 'Agent turn failed.')}", flush=True)


def render_cli_error(error: CliError) -> None:
    """Render one expected CLI failure without a traceback."""
    print(f"Error: {error.message}")
    if error.hint:
        print(f"Hint: {error.hint}")
