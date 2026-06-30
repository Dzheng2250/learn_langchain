"""Interfaces implemented by lifecycle hook handlers."""

from typing import Protocol

from src.core.hooks.models import HookContext, HookDecision


class HookHandler(Protocol):
    def handle(self, context: HookContext) -> HookDecision: ...
