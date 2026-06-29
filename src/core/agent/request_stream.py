"""Foreground chat and resume request streaming service."""

from collections.abc import Iterator

from src.core.hooks import HookContext, HookPoint
from typing import Protocol

from src.core.agent.contracts import (
    DiagnosticTurnStreamer,
    ExecutionControl,
    ExecutionLifecycleController,
    LockedTurnStreamer,
    RuntimeGraphProvider,
)
from src.core.agent.responses import (
    archived_session_event,
    idle_resume_event,
    pending_execution_event,
)
from src.core.ports.session import AgentSessionStore
from src.core.llm.contracts import ModelConfiguration


class SessionLockProvider(Protocol):
    """Minimal lock registry capability required by request streaming."""

    def get(self, session_id):
        """Return a context manager that serializes one Session."""


class AgentRequestStreamService:
    """Resolve request identity and stream foreground Agent execution events.

    This service owns the per-request orchestration that is independent from
    the async worker wrapper. Keeping it outside ``AgentTurnService`` makes the
    boundary explicit: the worker service schedules synchronous work, while this
    service resolves workspace/session identity and drives the execution loop.
    """

    def __init__(
        self,
        *,
        session_store: AgentSessionStore,
        lock_registry: SessionLockProvider,
        model_configuration: ModelConfiguration,
        diagnostic_turn_service: DiagnosticTurnStreamer,
        execution_lifecycle: ExecutionLifecycleController,
        runtime_graph_resolver: RuntimeGraphProvider,
        turn_execution_loop: LockedTurnStreamer,
        hook_runtime=None,
    ) -> None:
        self.session_store = session_store
        self.lock_registry = lock_registry
        self.model_configuration = model_configuration
        self.diagnostic_turn_service = diagnostic_turn_service
        self.execution_lifecycle = execution_lifecycle
        self.runtime_graph_resolver = runtime_graph_resolver
        self.turn_execution_loop = turn_execution_loop
        self.hook_runtime = hook_runtime

    def stream_turn(
        self,
        workspace_root: str,
        session_name: str,
        user_input: str,
        *,
        run_id: str,
        control: ExecutionControl | None = None,
        goal_mode: bool = False,
    ) -> Iterator[dict]:
        """Resolve request identity, serialize the Session, and stream one turn."""
        normalized = user_input.strip()
        if not normalized:
            raise ValueError("message must not be empty")
        workspace = self.session_store.resolve_workspace(workspace_root)
        existing = self.session_store.find_session(workspace, session_name)
        if existing is not None and existing[1]:
            yield archived_session_event(existing[0], run_id)
            return
        session, new_session = self.session_store.resolve_session(
            workspace,
            session_name,
        )
        hooks = self._hooks(workspace.root)
        if new_session:
            hooks.require(HookContext(
                point=HookPoint.SESSION_START,
                subject="startup",
                workspace_id=str(workspace.workspace_id),
                session_id=str(session.session_id),
                run_id=run_id,
                workspace_root=str(workspace.root),
                payload={"source": "startup", "session_name": session.session_name},
            ))
        prompt_context = hooks.require(HookContext(
            point=HookPoint.USER_PROMPT_SUBMIT,
            workspace_id=str(workspace.workspace_id),
            session_id=str(session.session_id),
            run_id=run_id,
            workspace_root=str(workspace.root),
            payload={"prompt": normalized, "goal_mode": goal_mode},
        ))
        normalized = str(prompt_context.payload.get("prompt", normalized)).strip()
        if not normalized:
            raise ValueError("UserPromptSubmit hook produced an empty message")
        # The UUID lock is the consistency boundary for loading and saving one
        # Session. Different Session UUIDs may execute concurrently.
        with self.lock_registry.get(session.session_id):
            status = self.model_configuration.configuration_status()
            if not status.configured:
                yield from self.diagnostic_turn_service.stream_unconfigured_turn(
                    session,
                    run_id,
                    status.missing,
                )
                return
            start = self.execution_lifecycle.begin_turn(
                session,
                normalized,
                goal_mode=goal_mode,
            )
            if start.blocked_by_pending:
                yield pending_execution_event(session, run_id, start.pending)
                return
            execution = start.execution
            try:
                graph = self.runtime_graph_resolver.graph_for_turn(
                    workspace,
                    goal_mode=goal_mode,
                )
            except Exception as exc:
                self.execution_lifecycle.pause_runtime_creation_failed(execution, exc)
                raise
            yield from self.turn_execution_loop.stream_locked_turn(
                session,
                graph,
                normalized,
                run_id,
                execution=execution,
                control=control,
            )

    def stream_resume(
        self,
        workspace_root: str,
        session_name: str,
        *,
        run_id: str,
        instruction: str = "",
        control: ExecutionControl | None = None,
        resume_value: dict | None = None,
    ) -> Iterator[dict]:
        """Resume the Session's pending execution with a new bounded Grant."""
        workspace = self.session_store.resolve_workspace(workspace_root)
        existing = self.session_store.find_session(workspace, session_name)
        if existing is not None and existing[1]:
            yield archived_session_event(existing[0], run_id)
            return
        session, _ = self.session_store.resolve_session(workspace, session_name)
        hooks = self._hooks(workspace.root)
        hooks.require(HookContext(
            point=HookPoint.SESSION_START,
            subject="resume",
            workspace_id=str(workspace.workspace_id),
            session_id=str(session.session_id),
            run_id=run_id,
            workspace_root=str(workspace.root),
            payload={"source": "resume", "session_name": session.session_name},
        ))
        with self.lock_registry.get(session.session_id):
            if not self.execution_lifecycle.has_attached_execution(session):
                yield idle_resume_event(session, run_id)
                return
            pending = self.execution_lifecycle.resume(session)
            try:
                graph = self.runtime_graph_resolver.graph_for_resume(
                    workspace,
                    pending,
                    instruction=instruction,
                )
            except Exception as exc:
                self.execution_lifecycle.pause_resume_preparation_failed(pending, exc)
                raise
            yield from self.turn_execution_loop.stream_locked_turn(
                session,
                graph,
                pending.original_input,
                run_id,
                execution=pending,
                resume=True,
                resume_value=resume_value,
                control=control,
            )

    def _hooks(self, workspace_root):
        if self.hook_runtime is None:
            from src.core.hooks import NOOP_HOOK_DISPATCHER
            return NOOP_HOOK_DISPATCHER
        return self.hook_runtime.get(workspace_root)
