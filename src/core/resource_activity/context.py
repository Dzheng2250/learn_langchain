"""Execution-local binding used by resource-aware tools and executors."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from .models import ResourceObservation
from src.core.telemetry import emit_event, record_error

_binding: ContextVar[tuple[object, object, list[str]] | None] = ContextVar("resource_activity_binding", default=None)

@contextmanager
def bind_resource_activity(recorder, context):
    captured: list[str] = []
    token = _binding.set((recorder, context, captured) if recorder is not None else None)
    try:
        yield captured
    finally:
        _binding.reset(token)

def current_resource_context():
    binding = _binding.get()
    return binding[1] if binding is not None else None

def record_resource_activity(observation: ResourceObservation) -> str | None:
    binding = _binding.get()
    if binding is None:
        return None
    recorder, context, captured = binding
    if not observation.event_key:
        observation = replace(
            observation,
            event_key=(
                f"{getattr(context, 'tool_call_id', '')}:{len(captured)}:"
                f"{observation.operation.value}:{observation.change_state.value}:"
                f"{observation.resource_uri}"
            ),
        )
    try:
        activity_id = recorder.record(context, observation)
    except Exception as exc:
        record_error("resource_activity", "record", exc, "Resource activity recording failed.",
                     {"tool": getattr(context, "tool_name", ""), "operation": observation.operation.value})
        return None
    if activity_id and activity_id not in captured:
        captured.append(activity_id)
        emit_event("resource_activity_recorded", "resource_activity", "Resource activity recorded.", {
            "activity_id": activity_id, "tool": getattr(context, "tool_name", ""),
            "operation": observation.operation.value, "observation_mode": observation.observation_mode.value,
            "change_state": observation.change_state.value,
        })
    return activity_id

def lookup_resource_evidence(recorder, context, *, source: str) -> dict:
    """Return hook-safe evidence while making ledger failures observable."""
    if recorder is None or not hasattr(recorder, "evidence_for"):
        return {"status": "not_applicable", "activity_id": None}
    try:
        return recorder.evidence_for(context)
    except Exception as exc:
        record_error(
            source,
            "resource_evidence",
            exc,
            "Resource evidence lookup failed.",
            {
                "tool": getattr(context, "tool_name", ""),
                "execution_id": str(getattr(context, "execution_id", "") or ""),
            },
        )
        return {
            "status": "unavailable",
            "reason": "ledger_lookup_failed",
            "activity_id": None,
        }
