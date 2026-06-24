"""Validation helpers for Agent-private task planning."""

from __future__ import annotations

import re

from src.config.tasks import TaskSettings
from src.core.tasks.models import TaskPlanItem


_TASK_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class TaskPlanValidator:
    """Validate task keys, field lengths, and dependency graphs."""

    def __init__(self, settings: TaskSettings) -> None:
        self.settings = settings

    def validate_plan_items(self, items: list[TaskPlanItem]) -> None:
        """Validate a batch of Agent-proposed task items before persistence."""
        self.validate_unique_keys(items)
        for item in items:
            self.validate_plan_item(item)

    def validate_plan_item(self, item: TaskPlanItem) -> None:
        """Validate one task item proposed by the parent Agent."""
        self.validate_task_key(item.task_key)
        self.limit_required(item.subject, self.settings.subject_max_chars, "subject")
        self.limit_required(
            item.description,
            self.settings.description_max_chars,
            "description",
        )
        self.limit_required(item.notes, self.settings.notes_max_chars, "notes")
        self.validate_dependency_keys(
            item.task_key,
            item.depends_on,
            set(item.depends_on) | {item.task_key},
        )

    def validate_unique_keys(self, items: list[TaskPlanItem]) -> None:
        """Reject duplicate semantic task keys in a single plan call."""
        keys = [item.task_key for item in items]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate task_key values: {duplicates}")

    def validate_task_key(self, task_key: str) -> None:
        """Validate the stable task key that is exposed to the LLM."""
        if not task_key or len(task_key) > self.settings.task_key_max_chars:
            raise ValueError(
                f"task_key must be 1-{self.settings.task_key_max_chars} characters"
            )
        if not _TASK_KEY_RE.match(task_key):
            raise ValueError(
                "task_key must start with a lowercase letter and contain only "
                "lowercase letters, digits, underscores, or hyphens"
            )

    def validate_dependency_keys(
        self,
        task_key: str,
        depends_on: tuple[str, ...] | list[str],
        available_keys: set[str],
    ) -> None:
        """Validate dependency keys for one task inside one Execution."""
        for dependency_key in depends_on:
            self.validate_task_key(dependency_key)
            if dependency_key == task_key:
                raise ValueError("task cannot depend on itself")
            if dependency_key not in available_keys:
                raise ValueError(f"unknown dependency for {task_key}: {dependency_key}")

    def assert_acyclic(self, edges: dict[str, set[str]]) -> None:
        """Reject task dependency cycles."""
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str, path: tuple[str, ...]) -> None:
            if node in visiting:
                raise ValueError("task dependency cycle detected: " + " -> ".join((*path, node)))
            if node in visited:
                return
            visiting.add(node)
            for dependency in edges.get(node, set()):
                visit(dependency, (*path, node))
            visiting.remove(node)
            visited.add(node)

        for key in tuple(edges):
            visit(key, ())

    def limit_required(self, value: str, limit: int, field_name: str) -> str:
        """Reject required string fields that exceed their configured limit."""
        if len(value) > limit:
            raise ValueError(f"{field_name} exceeds {limit} characters")
        return value

    def limit_optional(self, value: str | None, limit: int, field_name: str) -> str | None:
        """Reject optional string fields that exceed their configured limit."""
        if value is None:
            return None
        return self.limit_required(value, limit, field_name)
