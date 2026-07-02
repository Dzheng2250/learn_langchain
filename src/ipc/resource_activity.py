"""Shared defensive projections for resource activity wire payloads."""


def _safe_non_negative_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def resource_activity_display_stats(summary) -> dict[str, int]:
    """Normalize the stable summary fields consumed by terminal frontends."""
    summary = summary if isinstance(summary, dict) else {}
    reads = summary.get("reads") if isinstance(summary.get("reads"), dict) else {}
    changes = summary.get("changes") if isinstance(summary.get("changes"), dict) else {}
    evidence = summary.get("evidence") if isinstance(summary.get("evidence"), dict) else {}
    return {
        "resource_count": _safe_non_negative_int(reads.get("resource_count")),
        "returned_bytes": _safe_non_negative_int(reads.get("returned_bytes")),
        "changed_resource_count": _safe_non_negative_int(changes.get("changed_resource_count")),
        "warnings": sum(
            _safe_non_negative_int(evidence.get(key))
            for key in ("partial", "stale", "missing", "incomplete")
        ),
    }