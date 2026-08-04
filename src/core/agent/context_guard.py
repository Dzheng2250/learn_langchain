"""Checkpoint-safe context compaction between parent Agent model calls."""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import AIMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages

from src.core.context.budget import ContextWindowPlanner, ModelTokenCounter
from src.core.context.compaction import ContextCompactionRequired
from src.core.context.summary_executor import ContextSummaryExecutor
from src.core.llm.usage import context_tokens, has_context_usage, message_usage
from src.core.llm.retry_context import emit_foreground_event
from src.core.telemetry import emit_event, record_error


class AgentGraphState(MessagesState):
    """Active model window plus an append-only full-fidelity current Turn."""

    turn_journal: Annotated[list, add_messages]
    working_summary: str
    compacted_journal_count: int
    compaction_generation: int
    context_input_tokens: int
    context_output_tokens: int
    context_tokens: int
    context_usage_available: bool
    llm_usage_generation: int


class InTurnContextGuard:
    """Compact closed tool cycles before the next parent model request."""

    def __init__(
        self,
        model_provider,
        *,
        system_message: SystemMessage,
        tools: list,
        counter=None,
        planner=None,
        executor=None,
    ) -> None:
        self.counter = counter or ModelTokenCounter()
        self.planner = planner or ContextWindowPlanner(counter=self.counter)
        self.executor = executor or ContextSummaryExecutor(model_provider=model_provider)
        self.system_message = system_message
        self.tool_tokens = self.counter.count_value(
            [
                {
                    "name": getattr(tool, "name", ""),
                    "description": getattr(tool, "description", ""),
                    "schema": (
                        tool.tool_call_schema.model_json_schema()
                        if getattr(tool, "tool_call_schema", None) is not None
                        else {}
                    ),
                }
                for tool in tools
            ]
        ).tokens

    def __call__(self, state: AgentGraphState) -> dict:
        working_summary = str(state.get("working_summary") or "")
        active = list(state.get("messages") or [])
        prefix = [self.system_message]
        if working_summary:
            prefix.append(_working_summary_message(working_summary))
        projected = self._projected_input_tokens(
            prefix=prefix,
            active=active,
            journal=list(state.get("turn_journal") or []),
        )
        empty_plan = self.planner.plan([], fixed_messages=[*prefix, *active])
        soft = empty_plan.budget.soft_input_limit
        hard = empty_plan.budget.hard_input_limit
        if projected <= soft:
            return {}

        journal = list(state.get("turn_journal") or [])
        start = max(1, int(state.get("compacted_journal_count") or 1))
        cycles = _closed_tool_cycles(journal, start)
        selected = []
        predicted = projected
        for cycle_start, cycle_end, cycle_messages in cycles:
            # Preserve the newest closed cycle at the soft boundary. At the hard
            # boundary it is eligible too, because proceeding would be unsafe.
            is_latest = cycle_end == cycles[-1][1]
            if is_latest and projected <= hard:
                break
            selected.append((cycle_start, cycle_end, cycle_messages))
            predicted -= self.counter.count_messages(cycle_messages).tokens
            if predicted <= soft:
                break

        if not selected:
            if projected > hard:
                raise ContextCompactionRequired(
                    "The active Turn exceeded its hard input limit, but no closed "
                    "tool cycle is available for safe compaction."
                )
            return {}

        source_messages = [
            message
            for _start, _end, cycle_messages in selected
            for message in cycle_messages
        ]
        try:
            summary_result = self.executor.summarize(
                working_summary,
                source_messages,
                source_groups=[cycle_messages for _start, _end, cycle_messages in selected],
            )
            summary, input_tokens, output_tokens = summary_result
        except Exception as exc:
            emit_foreground_event(
                "context_compaction_failed",
                {
                    "mode": "in_turn",
                    "error_type": type(exc).__name__,
                    "source_message_count": len(source_messages),
                },
            )
            record_error(
                "context_guard",
                "in_turn_compaction",
                exc,
                "Turn-local compaction failed; active messages were preserved.",
                {"projected_input_tokens": projected, "hard_input_limit": hard},
                event_type="in_turn_compaction_failed",
            )
            if projected > hard:
                raise ContextCompactionRequired(
                    "The active Turn reached its hard input limit and Turn-local "
                    "compaction failed. Resume after the summary provider recovers."
                ) from exc
            return {}

        removals = [
            RemoveMessage(id=message.id)
            for message in source_messages
            if getattr(message, "id", None)
        ]
        if len(removals) != len(source_messages):
            if projected > hard:
                raise ContextCompactionRequired(
                    "The active Turn contains messages without stable IDs and cannot "
                    "be compacted safely."
                )
            return {}
        source_tokens = self.counter.count_messages(source_messages).tokens
        previous_summary_tokens = (
            self.counter.count_messages(
                [_working_summary_message(working_summary)]
            ).tokens
            if working_summary
            else 0
        )
        new_summary_tokens = self.counter.count_messages(
            [_working_summary_message(summary)]
        ).tokens
        # Keep the same projection basis used before compaction. Recounting the
        # complete LangChain checkpoint here includes response metadata that is
        # not sent to the provider and can turn a successful compaction into a
        # false hard-limit failure.
        post_compaction_tokens = max(
            0,
            projected - source_tokens - previous_summary_tokens + new_summary_tokens,
        )
        if post_compaction_tokens >= projected:
            if projected > hard:
                raise ContextCompactionRequired(
                    "Turn-local compaction did not reduce the active model input "
                    "below its hard limit."
                )
            return {}
        if post_compaction_tokens > hard:
            raise ContextCompactionRequired(
                "Turn-local compaction completed, but the remaining active input "
                "still exceeds the hard limit."
            )
        generation = int(state.get("compaction_generation") or 0) + 1
        emit_event(
            "in_turn_context_compacted",
            "context_guard",
            "Closed tool cycles were compacted before the next model call.",
            {
                "generation": generation,
                "compacted_cycle_count": len(selected),
                "compacted_message_count": len(source_messages),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "projected_input_tokens": projected,
                "post_compaction_tokens": post_compaction_tokens,
            },
        )
        emit_foreground_event(
            "context_compaction_completed",
            {
                **getattr(summary_result, "event_data", {}),
                "mode": "in_turn",
                "generation": generation,
                "compacted_cycle_count": len(selected),
                "compacted_message_count": len(source_messages),
            },
        )
        return {
            "messages": removals,
            "working_summary": summary,
            "compacted_journal_count": selected[-1][1],
            "compaction_generation": generation,
        }

    def _projected_input_tokens(self, *, prefix: list, active: list, journal: list) -> int:
        """Prefer provider usage for the stable prefix of the current Turn.

        Serializing complete LangChain messages includes response metadata that is
        not sent back to the provider and can substantially overestimate a long
        checkpoint. Once the current Turn has one model response, its reported
        input usage is a better baseline; only that response and later tool results
        need to be estimated before the next request.
        """
        current_turn_ids = {
            getattr(message, "id", None)
            for message in journal
            if getattr(message, "id", None)
        }
        for index in range(len(active) - 1, -1, -1):
            message = active[index]
            if not isinstance(message, AIMessage):
                continue
            if getattr(message, "id", None) not in current_turn_ids:
                continue
            usage = message_usage(message)
            input_tokens = usage.get("input_tokens")
            if input_tokens is not None and has_context_usage(usage):
                delta = self.counter.count_messages(active[index + 1:]).tokens
                return context_tokens(usage) + delta
        return self.counter.count_messages([*prefix, *active]).tokens + self.tool_tokens


def _closed_tool_cycles(journal: list, start: int) -> list[tuple[int, int, list]]:
    """Return complete assistant-tool/result groups after a journal cursor."""
    cycles = []
    index = start
    while index < len(journal):
        assistant = journal[index]
        calls = getattr(assistant, "tool_calls", None) if isinstance(assistant, AIMessage) else None
        if not calls:
            index += 1
            continue
        expected = {str(call.get("id") or "") for call in calls}
        end = index + 1
        results = []
        while end < len(journal) and isinstance(journal[end], ToolMessage):
            results.append(journal[end])
            end += 1
        actual = {str(message.tool_call_id or "") for message in results}
        if expected and expected.issubset(actual):
            cycles.append((index, end, [assistant, *results]))
            index = end
            continue
        break
    return cycles


def _working_summary_message(summary: str) -> SystemMessage:
    """Build the exact synthetic message used for a Turn-local summary."""
    return SystemMessage(
        content=(
            "Current Turn working summary. Treat it as prior execution state, "
            f"not as a new user request:\n{summary}"
        )
    )


def latest_tool_results(state: AgentGraphState) -> list[ToolMessage]:
    """Select only results belonging to the latest assistant tool request."""
    messages = list(state.get("messages") or [])
    assistant_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], AIMessage)
            and getattr(messages[index], "tool_calls", None)
        ),
        None,
    )
    if assistant_index is None:
        return []
    expected = {
        str(call.get("id") or "")
        for call in messages[assistant_index].tool_calls
    }
    return [
        message
        for message in messages[assistant_index + 1:]
        if isinstance(message, ToolMessage)
        and str(message.tool_call_id or "") in expected
    ]
