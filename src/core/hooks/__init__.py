"""System-level Agent lifecycle Hook API."""

from src.core.hooks.config import build_hook_dispatcher
from src.core.hooks.dispatcher import HookDispatcher, HookRejected, NOOP_HOOK_DISPATCHER
from src.core.hooks.handlers import CommandHook
from src.core.hooks.models import (
    HookAction, HookContext, HookDecision, HookFailureMode, HookPoint, HookSpec,
)
from src.core.hooks.registry import HookRegistry
from src.core.hooks.runtime import HookRuntimeRegistry

__all__ = [
    "CommandHook", "HookAction", "HookContext", "HookDecision",
    "HookDispatcher", "HookFailureMode", "HookPoint", "HookRegistry",
    "HookRejected", "HookRuntimeRegistry", "HookSpec", "NOOP_HOOK_DISPATCHER",
    "build_hook_dispatcher",
]
