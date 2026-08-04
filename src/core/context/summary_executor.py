"""Token-budgeted LLM execution for context compression."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from dataclasses import dataclass

from src.config.settings import (
    CONTEXT_SAFETY_MARGIN_TOKENS,
    CONTEXT_SUMMARY_MAP_MAX_TOKENS,
    CONTEXT_SUMMARY_MAP_WORKERS,
    CONTEXT_SUMMARY_MAX_TOKENS,
    MODEL_CONTEXT_LIMIT,
)
from src.core.common.content import message_content_text
from src.core.common.debug import debug_print
from src.core.context.budget import ModelTokenCounter
from src.core.context.messages import format_messages_for_summary
from src.core.llm.completion import ensure_complete_response
from src.core.llm.contracts import LlmPurpose, ModelProvider
from src.core.llm.retry_context import emit_foreground_event
from src.core.llm.usage import message_usage
from src.core.prompts import build_context_summary_messages
from src.core.telemetry import emit_event, event_span


@dataclass(frozen=True)
class _SourceUnit:
    text: str
    source_keys: tuple[str, ...]
    children: tuple["_SourceUnit", ...] = ()


@dataclass(frozen=True)
class _SummaryCallResult:
    text: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


@dataclass(frozen=True)
class SummaryExecutionResult:
    """Completed summary plus safe aggregate diagnostics."""

    summary: str
    input_tokens: int
    output_tokens: int
    event_data: dict

    def __iter__(self):
        yield self.summary
        yield self.input_tokens
        yield self.output_tokens


class ContextSummaryExecutor:
    """Summarize once when possible, otherwise use bounded map/reduce."""

    def __init__(
        self,
        *,
        model_provider: ModelProvider,
        counter: ModelTokenCounter | None = None,
        model_context_limit: int = MODEL_CONTEXT_LIMIT,
        safety_margin_tokens: int = CONTEXT_SAFETY_MARGIN_TOKENS,
        summary_max_tokens: int = CONTEXT_SUMMARY_MAX_TOKENS,
        map_max_tokens: int = CONTEXT_SUMMARY_MAP_MAX_TOKENS,
        map_workers: int = CONTEXT_SUMMARY_MAP_WORKERS,
    ) -> None:
        if summary_max_tokens <= 0 or map_max_tokens <= 0:
            raise ValueError("Context summary token budgets must be greater than zero")
        if map_max_tokens > summary_max_tokens:
            raise ValueError("Map output budget cannot exceed final summary output budget")
        if map_workers <= 0:
            raise ValueError("Context summary map workers must be greater than zero")
        self.model_provider = model_provider
        self.counter = counter or ModelTokenCounter()
        self.model_context_limit = int(model_context_limit)
        self.safety_margin_tokens = int(safety_margin_tokens)
        self.summary_max_tokens = int(summary_max_tokens)
        self.map_max_tokens = int(map_max_tokens)
        self.map_workers = int(map_workers)
        self.final_input_limit = self._input_limit(self.summary_max_tokens)
        self.map_input_limit = self._input_limit(self.map_max_tokens)

    def summarize(
        self,
        previous_summary: str,
        messages: list,
        memory_context: str = "",
        *,
        source_groups: list[list] | None = None,
    ) -> SummaryExecutionResult:
        """Return ``(summary, input_tokens, output_tokens)`` for every source message."""
        units = self._source_units(messages, source_groups)
        full_source = self._join_units(units)
        expected_keys = {key for unit in units for key in unit.source_keys}
        estimated_source_tokens = self.counter.count_value(full_source).tokens
        single_prompt = self._prompt(
            source=full_source,
            previous_summary=previous_summary,
            memory_context=memory_context,
            max_tokens=self.summary_max_tokens,
            phase="final",
        )
        mode = (
            "single"
            if self.counter.count_messages(single_prompt).tokens <= self.final_input_limit
            else "map_reduce"
        )
        self._notify(
            "context_compaction_started",
            {
                "mode": mode,
                "estimated_source_tokens": estimated_source_tokens,
                "source_message_count": len(messages),
            },
        )

        if mode == "single":
            result = self._invoke(
                source=full_source,
                previous_summary=previous_summary,
                memory_context=memory_context,
                max_tokens=self.summary_max_tokens,
                phase="final",
                stage="single",
                index=1,
                count=1,
            )
            results = [result]
            summary = result.text
            map_group_count = 0
            reduce_levels = 0
            covered_keys = expected_keys
        else:
            batches = self._partition_units(
                units,
                previous_summary=previous_summary,
                memory_context=memory_context,
                input_limit=self.map_input_limit,
                max_tokens=self.map_max_tokens,
                phase="map",
            )
            covered_keys = {
                key for batch in batches for unit in batch for key in unit.source_keys
            }
            if covered_keys != expected_keys:
                raise RuntimeError("Context summary source coverage is incomplete")
            map_results = self._invoke_parallel(
                batches,
                previous_summary=previous_summary,
                memory_context=memory_context,
                stage="map",
            )
            results = list(map_results)
            map_group_count = len(map_results)
            summary, reduce_results, reduce_levels = self._reduce(
                previous_summary=previous_summary,
                memory_context=memory_context,
                summaries=[result.text for result in map_results],
            )
            results.extend(reduce_results)

        input_tokens = sum(result.input_tokens for result in results)
        output_tokens = sum(result.output_tokens for result in results)
        cache_creation = sum(result.cache_creation_input_tokens for result in results)
        cache_read = sum(result.cache_read_input_tokens for result in results)
        debug_print("CONTEXT SUMMARY UPDATED", summary)
        payload = {
            "mode": mode,
            "summary_chars": len(summary),
            "compressed_messages": len(messages),
            "source_tokens_estimated": estimated_source_tokens,
            "source_coverage_count": len(covered_keys),
            "map_group_count": map_group_count,
            "reduce_levels": reduce_levels,
            "llm_call_count": len(results),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
        }
        emit_event(
            "context_summarized",
            "agent_context",
            "Context summary updated.",
            payload,
        )
        return SummaryExecutionResult(summary, input_tokens, output_tokens, payload)

    def _reduce(
        self,
        *,
        previous_summary: str,
        memory_context: str,
        summaries: list[str],
    ) -> tuple[str, list[_SummaryCallResult], int]:
        results: list[_SummaryCallResult] = []
        current = list(summaries)
        level = 0
        while True:
            level += 1
            source = self._numbered_summaries(current)
            final_prompt = self._prompt(
                source=source,
                previous_summary=previous_summary,
                memory_context=memory_context,
                max_tokens=self.summary_max_tokens,
                phase="final",
            )
            if self.counter.count_messages(final_prompt).tokens <= self.final_input_limit:
                result = self._invoke(
                    source=source,
                    previous_summary=previous_summary,
                    memory_context=memory_context,
                    max_tokens=self.summary_max_tokens,
                    phase="final",
                    stage="reduce",
                    index=1,
                    count=1,
                    level=level,
                )
                results.append(result)
                return result.text, results, level
            if level > 8:
                raise RuntimeError("Context summary reduce depth exceeded its safety limit")
            units = [
                _SourceUnit(f"Intermediate summary {index}:\n{text}", (str(index),))
                for index, text in enumerate(current, start=1)
            ]
            batches = self._partition_units(
                units,
                previous_summary=previous_summary,
                memory_context=memory_context,
                input_limit=self.map_input_limit,
                max_tokens=self.map_max_tokens,
                phase="map",
            )
            reduced = self._invoke_parallel(
                batches,
                previous_summary=previous_summary,
                memory_context=memory_context,
                stage="reduce_map",
                level=level,
            )
            results.extend(reduced)
            next_values = [result.text for result in reduced]
            if self.counter.count_value(next_values).tokens >= self.counter.count_value(current).tokens:
                raise RuntimeError("Context summary reduce stage did not reduce token volume")
            current = next_values

    def _invoke_parallel(
        self,
        batches: list[list[_SourceUnit]],
        *,
        previous_summary: str,
        memory_context: str,
        stage: str,
        level: int = 0,
    ) -> list[_SummaryCallResult]:
        completed = 0
        ordered: list[_SummaryCallResult | None] = [None] * len(batches)

        def invoke(index: int, batch: list[_SourceUnit]) -> _SummaryCallResult:
            return self._invoke(
                source=self._join_units(batch),
                previous_summary=previous_summary,
                memory_context=memory_context,
                max_tokens=self.map_max_tokens,
                phase="map",
                stage=stage,
                index=index + 1,
                count=len(batches),
                level=level,
            )

        with ThreadPoolExecutor(max_workers=min(self.map_workers, len(batches))) as pool:
            futures = {
                pool.submit(copy_context().run, invoke, index, batch): index
                for index, batch in enumerate(batches)
            }
            try:
                for future in as_completed(futures):
                    index = futures[future]
                    ordered[index] = future.result()
                    completed += 1
                    usage = [result for result in ordered if result is not None]
                    self._notify(
                        "context_compaction_progress",
                        {
                            "mode": "map_reduce",
                            "stage": stage,
                            "level": level,
                            "completed_groups": completed,
                            "group_count": len(batches),
                            "input_tokens": sum(item.input_tokens for item in usage),
                            "output_tokens": sum(item.output_tokens for item in usage),
                            "cache_creation_input_tokens": sum(
                                item.cache_creation_input_tokens for item in usage
                            ),
                            "cache_read_input_tokens": sum(
                                item.cache_read_input_tokens for item in usage
                            ),
                        },
                    )
            except Exception:
                for future in futures:
                    future.cancel()
                raise
        return [result for result in ordered if result is not None]

    def _invoke(
        self,
        *,
        source: str,
        previous_summary: str,
        memory_context: str,
        max_tokens: int,
        phase: str,
        stage: str,
        index: int,
        count: int,
        level: int = 0,
    ) -> _SummaryCallResult:
        prompt = self._prompt(
            source=source,
            previous_summary=previous_summary,
            memory_context=memory_context,
            max_tokens=max_tokens,
            phase=phase,
        )
        with event_span(
            "context_summary_llm",
            "agent_context",
            payload={
                "stage": stage,
                "level": level,
                "group_index": index,
                "group_count": count,
                "source_tokens_estimated": self.counter.count_value(source).tokens,
            },
        ):
            response = self._create_summary_llm(max_tokens=max_tokens).invoke(prompt)
        ensure_complete_response(
            response,
            output_budget_setting=(
                "LEARN_AGENT_CONTEXT_SUMMARY_MAP_MAX_TOKENS"
                if phase == "map"
                else "LEARN_AGENT_CONTEXT_SUMMARY_MAX_TOKENS"
            ),
        )
        text = message_content_text(response).strip()
        if not text:
            raise RuntimeError(f"Context summary model returned empty output during {stage}")
        usage = message_usage(response)
        return _SummaryCallResult(
            text=text,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            cache_read_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
        )

    def _partition_units(
        self,
        units: list[_SourceUnit],
        *,
        previous_summary: str,
        memory_context: str,
        input_limit: int,
        max_tokens: int,
        phase: str,
    ) -> list[list[_SourceUnit]]:
        expanded: list[_SourceUnit] = []
        for unit in units:
            if self._fits(
                [unit], previous_summary, memory_context, input_limit, max_tokens, phase
            ):
                expanded.append(unit)
            else:
                expanded.extend(
                    self._split_oversized_unit(
                        unit,
                        previous_summary=previous_summary,
                        memory_context=memory_context,
                        input_limit=input_limit,
                        max_tokens=max_tokens,
                        phase=phase,
                    )
                )
        batches: list[list[_SourceUnit]] = []
        current: list[_SourceUnit] = []
        for unit in expanded:
            candidate = [*current, unit]
            if current and not self._fits(
                candidate, previous_summary, memory_context, input_limit, max_tokens, phase
            ):
                batches.append(current)
                current = [unit]
            else:
                current = candidate
        if current:
            batches.append(current)
        return batches

    def _split_oversized_unit(
        self,
        unit: _SourceUnit,
        *,
        previous_summary: str,
        memory_context: str,
        input_limit: int,
        max_tokens: int,
        phase: str,
    ) -> list[_SourceUnit]:
        if unit.children:
            expanded: list[_SourceUnit] = []
            for child in unit.children:
                if self._fits(
                    [child], previous_summary, memory_context, input_limit, max_tokens, phase
                ):
                    expanded.append(child)
                else:
                    expanded.extend(
                        self._split_oversized_unit(
                            child,
                            previous_summary=previous_summary,
                            memory_context=memory_context,
                            input_limit=input_limit,
                            max_tokens=max_tokens,
                            phase=phase,
                        )
                    )
            return expanded
        remaining = unit.text
        parts: list[str] = []
        while remaining:
            low, high, best = 1, len(remaining), 0
            while low <= high:
                middle = (low + high) // 2
                probe = _SourceUnit(remaining[:middle], unit.source_keys)
                if self._fits(
                    [probe], previous_summary, memory_context, input_limit, max_tokens, phase
                ):
                    best = middle
                    low = middle + 1
                else:
                    high = middle - 1
            if best <= 0:
                raise RuntimeError("Context summary fixed prompt exceeds its input budget")
            parts.append(remaining[:best])
            remaining = remaining[best:]
        total = len(parts)
        return [
            _SourceUnit(
                f"Source segment {index}/{total}:\n{text}",
                unit.source_keys,
            )
            for index, text in enumerate(parts, start=1)
        ]

    def _fits(
        self,
        units: list[_SourceUnit],
        previous_summary: str,
        memory_context: str,
        input_limit: int,
        max_tokens: int,
        phase: str,
    ) -> bool:
        prompt = self._prompt(
            source=self._join_units(units),
            previous_summary=previous_summary,
            memory_context=memory_context,
            max_tokens=max_tokens,
            phase=phase,
        )
        return self.counter.count_messages(prompt).tokens <= input_limit

    def _source_units(
        self,
        messages: list,
        source_groups: list[list] | None,
    ) -> list[_SourceUnit]:
        groups = source_groups or [[message] for message in messages]
        units = []
        ordinal = 0
        for group in groups:
            keys = []
            children = []
            for message in group:
                message_id = getattr(message, "id", None)
                key = str(message_id) if message_id else f"ordinal:{ordinal}"
                keys.append(key)
                children.append(
                    _SourceUnit(format_messages_for_summary([message]), (key,))
                )
                ordinal += 1
            units.append(
                _SourceUnit(
                    format_messages_for_summary(group),
                    tuple(keys),
                    tuple(children),
                )
            )
        return units or [_SourceUnit("", ())]

    @staticmethod
    def _join_units(units: list[_SourceUnit]) -> str:
        return "\n\n".join(unit.text for unit in units)

    @staticmethod
    def _numbered_summaries(summaries: list[str]) -> str:
        return "\n\n".join(
            f"Intermediate summary {index}:\n{summary}"
            for index, summary in enumerate(summaries, start=1)
        )

    @staticmethod
    def _prompt(**kwargs) -> list:
        if "max_tokens" in kwargs:
            kwargs["summary_max_tokens"] = kwargs.pop("max_tokens")
        return build_context_summary_messages(**kwargs)

    def _create_summary_llm(self, *, max_tokens: int | None = None):
        return self.model_provider.create_chat_model(
            LlmPurpose.CONTEXT_SUMMARY,
            temperature=0,
            streaming=False,
            max_tokens=max_tokens or self.summary_max_tokens,
        )

    def _input_limit(self, output_tokens: int) -> int:
        value = self.model_context_limit - int(output_tokens) - self.safety_margin_tokens
        if value <= 0:
            raise ValueError("Context summary output and safety budgets exhaust the model window")
        return value

    @staticmethod
    def _notify(event: str, data: dict) -> None:
        emit_foreground_event(event, data)
