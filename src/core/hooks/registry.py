"""Deterministic registration and matching for lifecycle hooks."""

import re

from src.core.hooks.models import HookPoint, HookSpec


class HookRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, HookSpec] = {}
        self._frozen = False

    def register(self, spec: HookSpec) -> None:
        if self._frozen:
            raise RuntimeError("Hook registry is frozen")
        if not spec.hook_id.strip():
            raise ValueError("Hook ID must not be empty")
        if spec.hook_id in self._specs:
            raise ValueError(f"Hook already registered: {spec.hook_id}")
        if spec.matcher not in {"", "*"}:
            re.compile(spec.matcher)
        if not callable(getattr(spec.handler, "handle", None)):
            raise TypeError("Hook handler must define handle(context)")
        self._specs[spec.hook_id] = spec

    def freeze(self) -> None:
        self._frozen = True

    def matching(self, point: HookPoint, subject: str = "") -> tuple[HookSpec, ...]:
        matches = []
        for spec in self._specs.values():
            if spec.point != point:
                continue
            if spec.matcher not in {"", "*"} and re.search(spec.matcher, subject) is None:
                continue
            matches.append(spec)
        return tuple(sorted(matches, key=lambda item: (item.priority, item.hook_id)))

    def specs(self) -> tuple[HookSpec, ...]:
        return tuple(self._specs.values())
