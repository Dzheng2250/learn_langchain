from dataclasses import dataclass, field


@dataclass
class AgentContextState:
    """Compact conversation state kept outside LangGraph message history."""

    summary: str = ""
    recent_messages: list = field(default_factory=list)
