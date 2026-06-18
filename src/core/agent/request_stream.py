"""Foreground chat and resume request streaming service."""

from collections.abc import Callable, Iterator
from typing import Protocol

from src.core.agent.contracts import ExecutionControl
from src.core.agent.loop import TurnExecutionLoop
from src.core.agent.responses import (
    archived_session_event,
    idle_resume_event,
    pending_execution_event,
)
from src.core.agent.runtime_graph import RuntimeGraphResolver
from src.core.diagnostics import DiagnosticTurnService
from src.core.execution import ExecutionLifecycleService
from src.core.llm.provider import ModelConfiguration
from src.core.workspace.models import SessionContext
from src.core.workspace.contracts import WorkspaceIdentityRepository


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
        workspace_repository: WorkspaceIdentityRepository,
        lock_registry: SessionLockProvider,
        model_configuration: ModelConfiguration,
        diagnostic_turn_service: DiagnosticTurnService,
        execution_lifecycle: ExecutionLifecycleService,
        runtime_graph_resolver: RuntimeGraphResolver,
        turn_execution_loop: TurnExecutionLoop,
        execution_repository=None,
    ) -> None:
        self.workspace_repository = workspace_repository
        self.lock_registry = lock_registry
        self.model_configuration = model_configuration
        self.diagnostic_turn_service = diagnostic_turn_service
        self.execution_lifecycle = execution_lifecycle
        self.runtime_graph_resolver = runtime_graph_resolver
        self.turn_execution_loop = turn_execution_loop
        self.execution_repository = execution_repository

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
        workspace = self.workspace_repository.resolve(workspace_root)
        existing = self._get_existing_session_by_name(workspace, session_name)
        if existing is not None and existing[1]:
            yield archived_session_event(existing[0], run_id)
            return
        session, _new_session = self.workspace_repository.resolve_session(
            workspace,
            session_name,
        )
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
    ) -> Iterator[dict]:
        """Resume the Session's pending execution with a new bounded Grant."""
        if self.execution_repository is None:
            raise RuntimeError("Resumable execution is not configured.")
        workspace = self.workspace_repository.resolve(workspace_root)
        existing = self._get_existing_session_by_name(workspace, session_name)
        if existing is not None and existing[1]:
            yield archived_session_event(existing[0], run_id)
            return
        session, _ = self.workspace_repository.resolve_session(workspace, session_name)
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
                control=control,
            )

    def _get_existing_session_by_name(
        self,
        workspace,
        session_name: str,
    ) -> tuple[SessionContext, bool] | None:
        """Return an existing Session when the repository supports lookup-only access."""
        getter: Callable | None = getattr(
            self.workspace_repository,
            "get_session_by_name",
            None,
        )
        if getter is None:
            return None
        return getter(workspace, session_name, include_archived=True)
