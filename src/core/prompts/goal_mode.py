"""Turn-local lifecycle policy for goal mode prompts."""

from langchain_core.messages import HumanMessage


GOAL_PROMPT_MARKER = "goal_mode_prompt"
GOAL_REVIEW_MARKER = "goal_completion_review"

GOAL_MODE_INSTRUCTION = """\
<goal-mode>
Treat this request as a goal that may require sustained, multi-step work.
Assess its complexity before acting. For complex work, use task_plan when it
would improve reliability, keep task statuses current with task_update, and
inspect the latest plan when progress is uncertain. Do not create a plan for a
simple request. Before finishing, verify that the user's goal and important
validation work are complete; continue working when material tasks remain.
</goal-mode>"""

GOAL_COMPLETION_REVIEW = """\
<goal-completion-review>
The current private task plan still contains unfinished work. Re-check the
latest task list, continue the remaining work when possible, update task
statuses accurately, and only conclude after the goal is complete or you have
clearly explained a genuine blocker.
</goal-completion-review>"""


def inject_goal_mode_prompt(user_prompt: str) -> str:
    """Attach goal policy to one user turn without changing the system prefix."""
    return f"{user_prompt}\n\n{GOAL_MODE_INSTRUCTION}"


def replace_current_user_prompt(messages: list, prompt: str) -> list:
    """Replace the current HumanMessage with a marked model-only prompt."""
    updated = list(messages)
    for index in range(len(updated) - 1, -1, -1):
        message = updated[index]
        if isinstance(message, HumanMessage):
            kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
            kwargs["learn_agent_internal_prompt"] = GOAL_PROMPT_MARKER
            updated[index] = message.model_copy(
                update={"content": prompt, "additional_kwargs": kwargs}
            )
            return updated
    raise ValueError("Prepared turn does not contain a current HumanMessage")


def completion_review_message() -> HumanMessage:
    """Build one synthetic continuation message for an unfinished Goal plan."""
    return HumanMessage(
        content=GOAL_COMPLETION_REVIEW,
        additional_kwargs={"learn_agent_internal_prompt": GOAL_REVIEW_MARKER},
    )


def contains_completion_review(messages: list) -> bool:
    """Return whether checkpoint state already contains the Goal review marker."""
    return any(
        (getattr(message, "additional_kwargs", {}) or {}).get(
            "learn_agent_internal_prompt"
        ) == GOAL_REVIEW_MARKER
        for message in messages
    )


def sanitize_goal_messages(messages: list, original_input: str) -> list:
    """Remove model-only lifecycle prompts before durable history is committed."""
    sanitized = []
    for message in messages:
        marker = (getattr(message, "additional_kwargs", {}) or {}).get(
            "learn_agent_internal_prompt"
        )
        if marker == GOAL_REVIEW_MARKER:
            continue
        if marker == GOAL_PROMPT_MARKER and isinstance(message, HumanMessage):
            kwargs = dict(message.additional_kwargs)
            kwargs.pop("learn_agent_internal_prompt", None)
            message = message.model_copy(
                update={"content": original_input, "additional_kwargs": kwargs}
            )
        sanitized.append(message)
    return sanitized
