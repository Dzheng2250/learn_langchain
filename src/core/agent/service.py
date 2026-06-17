"""Workspace-aware application service for one complete Agent turn."""

import asyncio
from contextvars import copy_context
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, ThreadPoolExecutor
from functools import partial
from threading import Lock, RLock
from uuid import UUID, uuid4

from src.config.settings import (
    CORE_AGENT_WORKERS,
    MAX_AUTO_SLICES_PER_GRANT,
    MEMORY_ENABLED,
)
from src.core.agent.contracts import EventCallback, ExecutionControl
from src.core.agent.coordinator import TurnCoordinator
from src.core.agent.budget import ExecutionBudget, bind_execution_budget, reset_execution_budget
from src.core.agent.models import AgentRunContext, RunLimits, StopReason
from src.core.state.contracts import StateStore
from src.core.state.types import ExecutionStatus
from src.core.context.manager import AgentContextManager
from src.core.errors import ErrorAction, ProviderErrorHandler
from src.core.telemetry import (
    bind_context,
    bind_run_context,
    emit_event,
    record_error,
    reset_context,
)
from src.core.llm.provider import ModelConfiguration, OpenAICompatibleProvider
from src.core.streaming.events import stream_graph_events
from src.core.tasks.context import ToolExecutionContext
from src.core.workspace.models import SessionContext
from src.core.workspace.contracts import WorkspaceIdentityRepository
from src.core.workspace.runtime import WorkspaceRuntimeRegistry
from src.core.tracing import (
    TraceDirection,
    TraceLayer,
    bind_trace_context,
    record_trace,
    reset_trace_context,
)


def _trace_slice(events, slice_id):
    """Bind one Slice identity around graph streaming and LLM callbacks."""
    token = bind_trace_context(slice_id=slice_id)
    try:
        yield from events
    finally:
        reset_trace_context(token)


class SessionLockRegistry:
    """Create and retain one reentrant consistency lock per Session UUID."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[UUID, RLock] = {}

    def get(self, session_id: UUID) -> RLock:
        """Return the stable lock that serializes the given Session."""
        with self._guard:
            return self._locks.setdefault(session_id, RLock())


class AgentTurnService:
    """Run bounded synchronous Agent turns without blocking the Core event loop.

    A dedicated executor limits concurrent turns. Session UUID locks additionally
    serialize requests targeting the same Session while allowing different
    Sessions to run concurrently.
    """

    def __init__(
        self,
        *,
        workspace_repository: WorkspaceIdentityRepository,
        runtime_registry: WorkspaceRuntimeRegistry,
        state_store_factory: Callable[[], StateStore],
        context_manager: AgentContextManager | None = None,
        model_configuration: ModelConfiguration | None = None,
        memory_enabled: bool = MEMORY_ENABLED,
        lock_registry: SessionLockRegistry | None = None,
        run_limits: RunLimits | None = None,
        turn_executor: Executor | None = None,
        max_concurrent_turns: int = CORE_AGENT_WORKERS,
        execution_repository=None,
        checkpoint_manager=None,
        turn_finalizer=None,
        maintenance_repository=None,
        maintenance_scheduler=None,
        recovery_coordinator=None,
        turn_coordinator=None,
        max_auto_slices: int = MAX_AUTO_SLICES_PER_GRANT,
        provider_error_handler: ProviderErrorHandler | None = None,
    ) -> None:
        if max_concurrent_turns <= 0:
            raise ValueError("max_concurrent_turns must be greater than zero")
        self.workspace_repository = workspace_repository
        self.runtime_registry = runtime_registry
        self.state_store_factory = state_store_factory
        self.context_manager = context_manager or AgentContextManager()
        self.model_configuration = model_configuration or OpenAICompatibleProvider()
        self.memory_enabled = memory_enabled
        self.lock_registry = lock_registry or SessionLockRegistry()
        self.run_limits = run_limits or RunLimits()
        self._turn_slots = asyncio.Semaphore(max_concurrent_turns)
        self._turn_executor = turn_executor or ThreadPoolExecutor(
            max_workers=max_concurrent_turns,
            thread_name_prefix="agent-turn",
        )
        self._owns_turn_executor = turn_executor is None
        self.execution_repository = execution_repository
        self.checkpoint_manager = checkpoint_manager
        self.turn_finalizer = turn_finalizer
        self.maintenance_repository = maintenance_repository
        self.maintenance_scheduler = maintenance_scheduler
        self.recovery_coordinator = recovery_coordinator
        self.turn_coordinator = turn_coordinator or TurnCoordinator(
            self.context_manager,
            self.turn_finalizer,
            memory_enabled=self.memory_enabled,
        )
        self.max_auto_slices = max(1, int(max_auto_slices))
        self.provider_error_handler = provider_error_handler or ProviderErrorHandler()

    def initialize(self) -> None:
        """Initialize durable schema dependencies before accepting requests."""
        store = self.state_store_factory()
        try:
            store.initialize()
            if self.checkpoint_manager is not None:
                self.checkpoint_manager.initialize()
            if self.recovery_coordinator is not None:
                self.recovery_coordinator.reconcile()
            if self.maintenance_scheduler is not None:
                self.maintenance_scheduler.start()
        finally:
            store.close()

    async def run_turn(
        self,
        workspace_root: str,
        session_name: str,
        user_input: str,
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
        control: ExecutionControl | None = None,
        goal_mode: bool = False,
    ) -> dict:
        """Schedule one synchronous turn on the bounded Agent executor.

        ``on_event`` is invoked from the worker thread. RPC adapters must marshal
        socket writes back to their owning event loop.
        """
        loop = asyncio.get_running_loop()
        # The semaphore bounds submitted and running turns together, preventing
        # an unbounded queue from accumulating in ThreadPoolExecutor.
        await self._turn_slots.acquire()
        try:
            worker_context = copy_context()
            worker_future = self._turn_executor.submit(
                worker_context.run,
                partial(
                    self._run_turn_sync,
                    workspace_root,
                    session_name,
                    user_input,
                    on_event,
                    run_id=run_id,
                    control=control,
                    goal_mode=goal_mode,
                )
            )
        except Exception:
            self._turn_slots.release()
            raise

        def release_slot(_future) -> None:
            """Release concurrency capacity after the worker actually finishes."""
            try:
                loop.call_soon_threadsafe(self._turn_slots.release)
            except RuntimeError:
                # The process event loop is already closed; no later turn can use the slot.
                pass

        worker_future.add_done_callback(release_slot)
        return await asyncio.wrap_future(worker_future)

    async def resume_execution(
        self,
        workspace_root: str,
        session_name: str,
        instruction: str = "",
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
        control: ExecutionControl | None = None,
    ) -> dict:
        """Schedule a recoverable execution resume on the bounded executor."""
        loop = asyncio.get_running_loop()
        await self._turn_slots.acquire()
        try:
            worker_context = copy_context()
            worker_future = self._turn_executor.submit(
                worker_context.run,
                partial(
                    self._run_resume_sync,
                    workspace_root,
                    session_name,
                    instruction,
                    on_event,
                    run_id=run_id,
                    control=control,
                )
            )
        except Exception:
            self._turn_slots.release()
            raise

        def release_slot(_future) -> None:
            try:
                loop.call_soon_threadsafe(self._turn_slots.release)
            except RuntimeError:
                pass

        worker_future.add_done_callback(release_slot)
        return await asyncio.wrap_future(worker_future)

    def _run_turn_sync(
        self,
        workspace_root: str,
        session_name: str,
        user_input: str,
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
        control: ExecutionControl | None = None,
        goal_mode: bool = False,
    ) -> dict:
        """Consume one synchronous event stream and aggregate its final result."""
        result = {"status": "error", "run_id": run_id or uuid4().hex}
        for item in self.stream_turn(
            workspace_root,
            session_name,
            user_input,
            run_id=result["run_id"],
            control=control,
            goal_mode=goal_mode,
        ):
            if on_event:
                on_event(item)
            if item["event"] == "done":
                result["status"] = "ok"
                result.update(item["data"])
            elif item["event"] == "error":
                result["error"] = item["data"].get("message", "Agent turn failed.")
                result["stop_reason"] = item["data"].get(
                    "stop_reason",
                    StopReason.TURN_ERROR.value,
                )
                for field in (
                    "error_category",
                    "error_action",
                    "retryable",
                    "provider",
                    "provider_code",
                    "http_status",
                    "failure_source",
                    "failure_stage",
                    "failure_scope",
                    "user_action",
                ):
                    if field in item["data"]:
                        result[field] = item["data"][field]
        return result

    def _run_resume_sync(
        self,
        workspace_root: str,
        session_name: str,
        instruction: str = "",
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
        control: ExecutionControl | None = None,
    ) -> dict:
        """Consume one resumed execution stream and aggregate its final result."""
        result = {"status": "error", "run_id": run_id or uuid4().hex}
        for item in self.stream_resume(
            workspace_root,
            session_name,
            instruction=instruction,
            run_id=result["run_id"],
            control=control,
        ):
            if on_event:
                on_event(item)
            if item["event"] == "done":
                result["status"] = "ok"
                result.update(item["data"])
            elif item["event"] == "error":
                result["error"] = item["data"].get("message", "Agent resume failed.")
                result["stop_reason"] = item["data"].get("stop_reason", StopReason.TURN_ERROR.value)
                for field in (
                    "error_category",
                    "error_action",
                    "retryable",
                    "provider",
                    "provider_code",
                    "http_status",
                    "failure_source",
                    "failure_stage",
                    "failure_scope",
                    "user_action",
                ):
                    if field in item["data"]:
                        result[field] = item["data"][field]
        return result

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
            yield self._archived_session_event(existing[0], run_id)
            return
        session, _new_session = self.workspace_repository.resolve_session(workspace, session_name)
        # The UUID lock is the consistency boundary for loading and saving one
        # Session. Different Session UUIDs may execute concurrently.
        with self.lock_registry.get(session.session_id):
            status = self.model_configuration.configuration_status()
            if not status.configured:
                yield from self._stream_unconfigured_turn(session, run_id, status.missing)
                return
            execution = None
            if self.execution_repository is not None:
                pending = self.execution_repository.get_pending(session)
                if pending is not None:
                    yield self._pending_execution_event(session, run_id, pending)
                    return
                execution = self.execution_repository.begin(
                    session,
                    normalized,
                    goal_mode=goal_mode,
                )
                record_trace(
                    TraceDirection.INTERNAL,
                    TraceLayer.AGENT,
                    "agent.execution_attached",
                    execution_id=execution.execution_id,
                    data={"status": execution.status.value, "goal_mode": goal_mode},
                )
            try:
                runtime = self.runtime_registry.get(workspace)
                graph = runtime.goal_graph if goal_mode else runtime.graph
            except Exception as exc:
                if execution is not None and self.execution_repository is not None:
                    self.execution_repository.pause(
                        execution.execution_id,
                        ExecutionStatus.PAUSED_ERROR,
                        StopReason.TURN_ERROR.value,
                        f"Workspace runtime creation failed: {exc}",
                    )
                raise
            yield from self._stream_locked_turn(
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
            yield self._archived_session_event(existing[0], run_id)
            return
        session, _ = self.workspace_repository.resolve_session(workspace, session_name)
        with self.lock_registry.get(session.session_id):
            if self.execution_repository.get_attached(session) is None:
                yield {
                    "event": "done",
                    "data": {
                        "run_id": run_id,
                        "status": "idle",
                        "workspace_id": str(session.workspace.workspace_id),
                        "session_id": str(session.session_id),
                        "session_name": session.session_name,
                        "message": "Session has no pending execution to resume.",
                    },
                }
                return
            pending = self.execution_repository.resume(session)
            record_trace(
                TraceDirection.INTERNAL,
                TraceLayer.AGENT,
                "agent.execution_attached",
                execution_id=pending.execution_id,
                data={
                    "status": pending.status.value,
                    "resume": True,
                    "goal_mode": pending.goal_mode,
                },
            )
            try:
                runtime = self.runtime_registry.get(workspace)
                graph = runtime.goal_graph if pending.goal_mode else runtime.graph
                if instruction.strip():
                    from langchain_core.messages import HumanMessage

                    graph.update_state(
                        {"configurable": {"thread_id": pending.checkpoint_thread_id}},
                        {
                            "messages": [
                                HumanMessage(
                                    content=f"Additional resume instruction: {instruction.strip()}"
                                )
                            ]
                        },
                        as_node="agent",
                    )
            except Exception as exc:
                self.execution_repository.pause(
                    pending.execution_id,
                    ExecutionStatus.PAUSED_ERROR,
                    StopReason.TURN_ERROR.value,
                    f"Execution resume preparation failed: {exc}",
                )
                raise
            yield from self._stream_locked_turn(
                session,
                graph,
                pending.original_input,
                run_id,
                execution=pending,
                resume=True,
                control=control,
            )

    def _pending_execution_event(self, session: SessionContext, run_id: str, pending) -> dict:
        """Return a non-error event when a Session is blocked by recoverable work."""
        message = (
            "Session has a pending execution. Use 'learn-agent session resume --session "
            f"{session.session_name}' to continue, or 'learn-agent session discard --session "
            f"{session.session_name}' to discard it before starting a new chat."
        )
        return {
            "event": "done",
            "data": {
                "run_id": run_id,
                "status": "paused",
                "workspace_id": str(session.workspace.workspace_id),
                "session_id": str(session.session_id),
                "session_name": session.session_name,
                "execution_id": pending.execution_id,
                "stop_reason": pending.stop_reason or pending.status.value,
                "goal_mode": pending.goal_mode,
                "message": message,
            },
        }

    def _archived_session_event(self, session: SessionContext, run_id: str) -> dict:
        """Return a terminal event when a Session is intentionally unavailable."""
        return {
            "event": "done",
            "data": {
                "run_id": run_id,
                "status": "archived",
                "workspace_id": str(session.workspace.workspace_id),
                "session_id": str(session.session_id),
                "session_name": session.session_name,
                "message": (
                    "Session is archived. Use a different session name, or run "
                    "`learn-agent session delete --hard` before recreating it."
                ),
            },
        }

    def _get_existing_session_by_name(
        self,
        workspace,
        session_name: str,
    ) -> tuple[SessionContext, bool] | None:
        """Return an existing Session when the repository supports lookup-only access."""
        getter = getattr(self.workspace_repository, "get_session_by_name", None)
        if getter is None:
            return None
        return getter(workspace, session_name, include_archived=True)

    def session_status(self, workspace_root: str, session_name: str) -> dict:
        """Return compact pending-execution state without running the graph."""
        workspace = self.workspace_repository.resolve(workspace_root)
        existing = self._get_existing_session_by_name(workspace, session_name)
        if existing is not None and existing[1]:
            session = existing[0]
            return {
                "status": "archived",
                "workspace_id": str(workspace.workspace_id),
                "session_id": str(session.session_id),
                "session_name": session.session_name,
                "context_tokens": 0,
                "pending_execution": None,
                "execution_recoverable": False,
                "checkpoint_state": None,
                "maintenance": {
                    "pending": 0,
                    "running": 0,
                    "failed": 0,
                    "recent_failures": [],
                },
            }
        session, _ = self.workspace_repository.resolve_session(workspace, session_name)
        # Load context state to read current context_tokens
        store = self.state_store_factory()
        try:
            context_state, _ = store.load_session(session)
        except Exception:
            context_state = None
        finally:
            store.close()
        pending = self.execution_repository.get_attached(session) if self.execution_repository else None
        maintenance = (
            self.maintenance_repository.counts_for_session(
                str(workspace.workspace_id),
                str(session.session_id),
            )
            if self.maintenance_repository is not None
            else {"pending": 0, "running": 0, "failed": 0}
        )
        if self.maintenance_repository is not None:
            maintenance["recent_failures"] = (
                self.maintenance_repository.recent_failures_for_session(
                    str(workspace.workspace_id),
                    str(session.session_id),
                )
            )
        else:
            maintenance["recent_failures"] = []
        return {
            "workspace_id": str(workspace.workspace_id),
            "session_id": str(session.session_id),
            "session_name": session.session_name,
            "context_tokens": context_state.context_tokens if context_state else 0,
            "pending_execution": pending.__dict__ if pending else None,
            "execution_recoverable": pending.recoverable if pending else False,
            "checkpoint_state": pending.checkpoint_state if pending else None,
            "maintenance": maintenance,
        }

    def discard_pending(self, workspace_root: str, session_name: str) -> dict:
        """Discard the pending execution while retaining its audit rows."""
        if self.execution_repository is None:
            raise RuntimeError("Resumable execution is not configured.")
        workspace = self.workspace_repository.resolve(workspace_root)
        existing = self._get_existing_session_by_name(workspace, session_name)
        if existing is not None and existing[1]:
            session = existing[0]
            return {
                "status": "archived",
                "workspace_id": str(session.workspace.workspace_id),
                "session_id": str(session.session_id),
                "session_name": session.session_name,
                "message": "Session is archived and has no active execution to discard.",
            }
        session, _ = self.workspace_repository.resolve_session(workspace, session_name)
        with self.lock_registry.get(session.session_id):
            if self.execution_repository.get_attached(session) is None:
                return {
                    "status": "idle",
                    "workspace_id": str(session.workspace.workspace_id),
                    "session_id": str(session.session_id),
                    "session_name": session.session_name,
                    "message": "Session has no pending execution to discard.",
                }
            pending = self.execution_repository.discard(session)
            if self.maintenance_repository is not None:
                from src.core.maintenance.models import MaintenanceJobSpec
                from src.core.maintenance.types import (
                    MaintenanceJobType,
                    MaintenancePriority,
                )

                self.maintenance_repository.enqueue(
                    MaintenanceJobSpec(
                        MaintenanceJobType.CHECKPOINT_CLEANUP,
                        f"checkpoint_cleanup:{pending.execution_id}",
                        str(session.workspace.workspace_id),
                        str(session.session_id),
                        {"checkpoint_thread_id": pending.checkpoint_thread_id},
                        execution_id=pending.execution_id,
                        priority=MaintenancePriority.CHECKPOINT_CLEANUP,
                    )
                )
            if self.maintenance_scheduler is not None:
                self.maintenance_scheduler.wake()
        return {"status": "discarded", "execution_id": pending.execution_id}

    def delete_session(
        self,
        workspace_root: str,
        session_name: str,
        *,
        hard_delete: bool = False,
    ) -> dict:
        """Archive by default, or permanently delete one Workspace-local Session."""
        workspace = self.workspace_repository.resolve(workspace_root)
        found = self._get_existing_session_by_name(workspace, session_name)
        if found is None:
            return {
                "status": "not_found",
                "mode": "hard_delete" if hard_delete else "archive",
                "workspace_id": str(workspace.workspace_id),
                "session_name": session_name,
            }
        session, archived = found
        with self.lock_registry.get(session.session_id):
            if hard_delete:
                checkpoint_threads = self.workspace_repository.checkpoint_threads_for_session(
                    session
                )
                if self.checkpoint_manager is not None:
                    for thread_id in checkpoint_threads:
                        try:
                            self.checkpoint_manager.delete_thread(thread_id)
                        except Exception:
                            # Some executions may have no materialized
                            # checkpoint. State deletion is still the source of
                            # truth for removing this Session.
                            pass
                deleted = self.workspace_repository.delete_session(session)
                return {
                    "status": "deleted" if deleted else "not_found",
                    "mode": "hard_delete",
                    "workspace_id": str(workspace.workspace_id),
                    "session_id": str(session.session_id),
                    "session_name": session.session_name,
                    "checkpoint_threads_deleted": len(checkpoint_threads),
                }

            cleanup_enqueued = False
            if not archived and self.execution_repository is not None:
                pending = self.execution_repository.get_attached(session)
                if pending is not None:
                    self.execution_repository.discard(session)
                    cleanup_enqueued = self._enqueue_checkpoint_cleanup(session, pending)
            archived_now = (
                False if archived else self.workspace_repository.archive_session(session)
            )
            if cleanup_enqueued and self.maintenance_scheduler is not None:
                self.maintenance_scheduler.wake()
            return {
                "status": "archived" if archived_now else "already_archived",
                "mode": "archive",
                "workspace_id": str(workspace.workspace_id),
                "session_id": str(session.session_id),
                "session_name": session.session_name,
                "cleanup_enqueued": cleanup_enqueued,
            }

    def _enqueue_checkpoint_cleanup(self, session: SessionContext, pending) -> bool:
        """Queue checkpoint cleanup for a discarded pending execution."""
        if self.maintenance_repository is None:
            return False
        from src.core.maintenance.models import MaintenanceJobSpec
        from src.core.maintenance.types import (
            MaintenanceJobType,
            MaintenancePriority,
        )

        self.maintenance_repository.enqueue(
            MaintenanceJobSpec(
                MaintenanceJobType.CHECKPOINT_CLEANUP,
                f"checkpoint_cleanup:{pending.execution_id}",
                str(session.workspace.workspace_id),
                str(session.session_id),
                {"checkpoint_thread_id": pending.checkpoint_thread_id},
                execution_id=pending.execution_id,
                priority=MaintenancePriority.CHECKPOINT_CLEANUP,
            )
        )
        return True

    def close(self) -> None:
        """Stop foreground Turn workers, then stop durable maintenance safely."""
        if self._owns_turn_executor:
            self._turn_executor.shutdown(wait=True, cancel_futures=False)
        maintenance_stopped = (
            self.maintenance_scheduler.close()
            if self.maintenance_scheduler is not None
            else True
        )
        if self.checkpoint_manager is not None and maintenance_stopped:
            self.checkpoint_manager.close()

    def _stream_locked_turn(
        self,
        session: SessionContext,
        graph,
        user_input: str,
        run_id: str,
        *,
        execution=None,
        resume: bool = False,
        control: ExecutionControl | None = None,
    ) -> Iterator[dict]:
        """Run bounded Slices and persist either completion or recoverable pause."""
        store = self.state_store_factory()
        context_token = bind_context(
            workspace_id=session.workspace.workspace_id,
            session_id=session.session_id,
            run_id=run_id,
        )
        run_context_token = None
        execution_trace_token = None
        budget_token = None
        budget = None
        active_slice_id = None
        try:
            # Persisted turn_index identifies the last completed turn. The
            # current turn is assigned only after the Session lock is held.
            prepared = self.turn_coordinator.prepare(
                store=store,
                session=session,
                user_input=user_input,
                run_id=run_id,
                limits=self.run_limits,
            )
            state = prepared.state
            current_turn = prepared.turn_index
            run_context = prepared.run_context
            run_context_token = bind_run_context(run_context)
            if execution is not None:
                execution_trace_token = bind_trace_context(execution_id=execution.execution_id)
            record_trace(
                TraceDirection.INTERNAL,
                TraceLayer.AGENT,
                "agent.run_started",
                data={"turn_index": current_turn, "resume": resume},
            )
            emit_event(
                "turn_started",
                "agent_service",
                "Started workspace Agent turn.",
                {
                    "session_name": session.session_name,
                    "user_input_preview": user_input[:300],
                    "limits": {
                        "max_graph_steps": run_context.limits.max_graph_steps,
                        "max_tool_calls": run_context.limits.max_tool_calls,
                    },
                },
            )
            input_messages = prepared.input_messages
            checkpoint_thread_id = execution.checkpoint_thread_id if execution else None
            tool_context = ToolExecutionContext(
                workspace_id=str(session.workspace.workspace_id),
                session_id=str(session.session_id),
                execution_id=execution.execution_id if execution else None,
            )
            total_tool_calls = 0
            budget = ExecutionBudget()
            budget_token = bind_execution_budget(budget)
            exhausted_reason = StopReason.GRAPH_STEP_LIMIT.value
            for slice_number in range(1, self.max_auto_slices + 1):
                if budget.wall_time_exhausted():
                    exhausted_reason = StopReason.GRANT_WALL_TIME_LIMIT.value
                    break
                slice_id = None
                if execution is not None and self.execution_repository is not None:
                    slice_id = self.execution_repository.start_slice(
                        execution.execution_id,
                        execution.grant_index,
                        slice_number,
                    )
                    active_slice_id = slice_id
                record_trace(
                    TraceDirection.INTERNAL,
                    TraceLayer.AGENT,
                    "agent.slice_started",
                    slice_id=slice_id,
                    data={"slice_number": slice_number},
                )
                slice_input = None if resume or slice_number > 1 else input_messages
                paused_for_budget = False
                for item in _trace_slice(
                    stream_graph_events(
                        graph,
                        slice_input,
                        run_context,
                        checkpoint_thread_id=checkpoint_thread_id,
                        provider_error_handler=self.provider_error_handler,
                        tool_context=tool_context,
                    ),
                    slice_id,
                ):
                    total_tool_calls += int(item.get("data", {}).get("tool_call_count", 0)) if item["event"] in {"paused", "done"} else 0
                    if item["event"] == "paused":
                        paused_for_budget = True
                        exhausted_reason = item["data"].get(
                            "stop_reason",
                            StopReason.GRAPH_STEP_LIMIT.value,
                        )
                        if slice_id:
                            usage = budget.snapshot()
                            self.execution_repository.finish_slice(
                                slice_id,
                                execution.execution_id,
                                status=ExecutionStatus.PAUSED_BUDGET,
                                stop_reason=exhausted_reason,
                                graph_steps_used=int(item["data"].get("graph_steps_used", 0)),
                                usage=usage,
                            )
                            active_slice_id = None
                        record_trace(
                            TraceDirection.INTERNAL,
                            TraceLayer.AGENT,
                            "agent.slice_finished",
                            slice_id=slice_id,
                            data={"status": "paused", "stop_reason": exhausted_reason},
                        )
                        break
                    if item["event"] == "error":
                        record_trace(
                            TraceDirection.INTERNAL,
                            TraceLayer.AGENT,
                            "agent.slice_finished",
                            slice_id=slice_id,
                            data={
                                "status": "error",
                                "stop_reason": item["data"].get("stop_reason"),
                            },
                        )
                        if execution is not None and self.execution_repository is not None:
                            usage = budget.snapshot()
                            if slice_id:
                                self.execution_repository.finish_slice(
                                    slice_id,
                                    execution.execution_id,
                                    status=ExecutionStatus.PAUSED_ERROR,
                                    stop_reason=item["data"].get(
                                        "stop_reason",
                                        StopReason.TURN_ERROR.value,
                                    ),
                                    graph_steps_used=int(
                                        item["data"].get("graph_steps_used", 0)
                                    ),
                                    usage=usage,
                                )
                                active_slice_id = None
                            if item["data"].get("error_action") == ErrorAction.TERMINATE:
                                self._terminate_execution_after_error(
                                    session,
                                    execution,
                                    item["data"].get(
                                        "error_category",
                                        StopReason.TURN_ERROR.value,
                                    ),
                                )
                                yield from self._emit_terminal_provider_error(
                                    session,
                                    execution,
                                    run_id,
                                    item,
                                )
                                return
                            self.execution_repository.pause(
                                execution.execution_id,
                                ExecutionStatus.PAUSED_ERROR,
                                item["data"].get(
                                    "stop_reason",
                                    StopReason.TURN_ERROR.value,
                                ),
                                item["data"].get("message", ""),
                                usage=usage,
                            )
                        terminated = (
                            item["data"].get("error_action") == ErrorAction.TERMINATE
                        )
                        emit_event(
                            "turn_terminated" if terminated else "turn_paused",
                            "agent_service",
                            (
                                "Workspace Agent execution terminated after a "
                                "non-retryable error."
                                if terminated
                                else "Workspace Agent execution paused after an error."
                            ),
                            {
                                "stop_reason": item["data"].get(
                                    "stop_reason",
                                    StopReason.TURN_ERROR.value,
                                ),
                                "error_type": item["data"].get("type", "unknown"),
                                "error_category": item["data"].get(
                                    "error_category",
                                    "unknown",
                                ),
                            },
                            level="error",
                        )
                        record_trace(
                            TraceDirection.INTERNAL,
                            TraceLayer.AGENT,
                            "agent.run_failed",
                            data={"stop_reason": item["data"].get("stop_reason")},
                        )
                        yield item
                        return
                    if item["event"] != "done":
                        yield item
                        continue

                    final_messages = item["data"]["messages"]
                    finalization = self.turn_coordinator.finalize(
                        store=store,
                        session=session,
                        turn_index=current_turn,
                        previous_state=state,
                        final_messages=final_messages,
                        user_input=user_input,
                        execution=execution,
                        slice_id=slice_id,
                        graph_steps_used=int(item["data"].get("graph_steps_used", 0)),
                        usage=budget.snapshot(),
                    )
                    snapshot = budget.snapshot()
                    active_slice_id = None
                    record_trace(
                        TraceDirection.INTERNAL,
                        TraceLayer.AGENT,
                        "agent.slice_finished",
                        slice_id=slice_id,
                        data={"status": "completed"},
                    )
                    emit_event(
                        "turn_finished",
                        "agent_service",
                        "Finished workspace Agent turn.",
                        {
                            "stop_reason": StopReason.COMPLETED.value,
                            "tool_call_count": total_tool_calls,
                            "slice_count": slice_number,
                        },
                    )
                    record_trace(
                        TraceDirection.INTERNAL,
                        TraceLayer.AGENT,
                        "agent.run_finished",
                        data={"status": "ok", "slice_count": slice_number},
                    )
                    yield {
                        "event": "done",
                        "data": {
                            "run_id": run_id,
                            "status": "ok",
                            "workspace_id": str(session.workspace.workspace_id),
                            "session_id": str(session.session_id),
                            "session_name": session.session_name,
                            "execution_id": execution.execution_id if execution else None,
                            "stop_reason": StopReason.COMPLETED.value,
                            "tool_call_count": total_tool_calls,
                            "slices_used": slice_number,
                            "goal_mode": bool(getattr(execution, "goal_mode", False)),
                            "durability": "committed",
                            "maintenance_status": finalization.maintenance_status,
                            "memory_status": finalization.memory_status,
                            "memory_request_explicit": finalization.memory_request_explicit,
                            "context_tokens": snapshot.get("input_tokens", 0),
                        },
                    }
                    return
                if not paused_for_budget:
                    return
                if exhausted_reason == StopReason.BUDGET_LIMIT.value:
                    break
                if checkpoint_thread_id is None:
                    # Compatibility services without a checkpointer cannot
                    # safely continue from input=None. Production Core always
                    # provides a durable checkpoint thread.
                    break
                if control is not None and control.pause_after_slice.is_set():
                    exhausted_reason = StopReason.CLIENT_DISCONNECTED.value
                    break
                if budget.wall_time_exhausted():
                    exhausted_reason = StopReason.GRANT_WALL_TIME_LIMIT.value
                    break
                resume = True

            snapshot = budget.snapshot()
            summary = (
                f"Execution paused because {exhausted_reason}. "
                f"Used {slice_number} Slice(s), {snapshot['tool_calls']} tool call(s), "
                f"{snapshot['controlled_executions']} controlled execution(s), and "
                f"{snapshot['delegations']} delegation(s)."
            )
            if execution is not None and self.execution_repository is not None:
                self.execution_repository.pause(
                    execution.execution_id,
                    ExecutionStatus.PAUSED_CONFIRMATION
                    if exhausted_reason == StopReason.BUDGET_LIMIT.value
                    else ExecutionStatus.PAUSED_BUDGET,
                    exhausted_reason,
                    summary,
                    usage=snapshot,
                )
            emit_event(
                "turn_paused",
                "agent_service",
                summary,
                {"slice_count": slice_number, **snapshot},
            )
            record_trace(
                TraceDirection.INTERNAL,
                TraceLayer.AGENT,
                "agent.run_paused",
                data={"stop_reason": exhausted_reason, "slice_count": slice_number},
            )
            yield {
                "event": "done",
                "data": {
                    "run_id": run_id,
                    "status": "paused",
                    "workspace_id": str(session.workspace.workspace_id),
                    "session_id": str(session.session_id),
                    "session_name": session.session_name,
                    "execution_id": execution.execution_id if execution else None,
                    "stop_reason": exhausted_reason,
                    "tool_call_count": total_tool_calls,
                    "slices_used": slice_number,
                    "goal_mode": bool(getattr(execution, "goal_mode", False)),
                    "message": summary,
                },
            }
        except Exception as exc:
            if execution is not None and self.execution_repository is not None:
                try:
                    usage = budget.snapshot() if budget is not None else None
                    if active_slice_id is not None:
                        self.execution_repository.finish_slice(
                            active_slice_id,
                            execution.execution_id,
                            status=ExecutionStatus.PAUSED_ERROR,
                            stop_reason=StopReason.TURN_ERROR.value,
                            usage=usage,
                        )
                    self.execution_repository.pause(
                        execution.execution_id,
                        ExecutionStatus.PAUSED_ERROR,
                        StopReason.TURN_ERROR.value,
                        str(exc),
                        usage=usage,
                    )
                except Exception:
                    pass
            record_error("agent_service", "turn", exc, "Agent turn failed.", event_type="turn_failed")
            record_trace(
                TraceDirection.INTERNAL,
                TraceLayer.AGENT,
                "agent.run_failed",
                data={"error_type": type(exc).__name__},
            )
            yield {
                "event": "error",
                "data": {
                    "type": "turn_failed",
                    "stop_reason": StopReason.TURN_ERROR.value,
                    "message": str(exc),
                    "run_id": run_id,
                },
            }
        finally:
            if run_context_token is not None:
                reset_context(run_context_token)
            if execution_trace_token is not None:
                reset_trace_context(execution_trace_token)
            if budget_token is not None:
                reset_execution_budget(budget_token)
            reset_context(context_token)
            store.close()

    def _terminate_execution_after_error(self, session, execution, reason: str) -> None:
        """Release a Session after a deterministic, non-retryable provider error."""
        self.execution_repository.terminate(session, execution.execution_id, reason)
        if self.maintenance_repository is None:
            return
        from src.core.maintenance.models import MaintenanceJobSpec
        from src.core.maintenance.types import MaintenanceJobType, MaintenancePriority

        try:
            self.maintenance_repository.enqueue(
                MaintenanceJobSpec(
                    MaintenanceJobType.CHECKPOINT_CLEANUP,
                    f"checkpoint_cleanup:{execution.execution_id}",
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    {"checkpoint_thread_id": execution.checkpoint_thread_id},
                    execution_id=execution.execution_id,
                    priority=MaintenancePriority.CHECKPOINT_CLEANUP,
                )
            )
            if self.maintenance_scheduler is not None:
                self.maintenance_scheduler.wake()
        except Exception as exc:
            # The Session has already been safely released. A cleanup enqueue
            # failure must not reattach or pause a terminal execution.
            record_error(
                "agent_service",
                "terminal_checkpoint_cleanup",
                exc,
                "Terminal execution released, but checkpoint cleanup could not be queued.",
                {"execution_id": execution.execution_id},
            )

    def _emit_terminal_provider_error(
        self,
        session: SessionContext,
        execution,
        run_id: str,
        item: dict,
    ) -> Iterator[dict]:
        """Report a deterministic provider rejection after automatic rollback.

        Content filtering and invalid-request failures are not recoverable
        execution pauses. The failed input is not committed to Session history,
        the Session is released immediately, and the user can continue from the
        last successfully committed turn without calling resume or discard.
        """
        message = item["data"].get("message", "The model provider rejected this turn.")
        failure_stage = item["data"].get("failure_stage", "parent_graph")
        failure_scope = item["data"].get("failure_scope", "current_turn")
        failure_source = item["data"].get("failure_source", "agent_turn")
        user_action = item["data"].get("user_action", "revise_input_and_retry")
        recovery_note = self._user_failure_explanation(
            failure_source=failure_source,
            failure_scope=failure_scope,
            failure_stage=failure_stage,
            user_action=user_action,
        )
        yield {
            "event": "token",
            "data": {
                "content": message + recovery_note,
            },
        }
        yield {
            "event": "done",
            "data": {
                "run_id": run_id,
                "status": "terminated",
                "workspace_id": str(session.workspace.workspace_id),
                "session_id": str(session.session_id),
                "session_name": session.session_name,
                "execution_id": execution.execution_id if execution else None,
                "stop_reason": item["data"].get(
                    "stop_reason",
                    StopReason.TURN_ERROR.value,
                ),
                "message": message,
                "auto_recovered": True,
                "failed_turn_saved": False,
                "goal_mode": bool(getattr(execution, "goal_mode", False)),
                "tool_call_count": 0,
                "slices_used": 1,
                "error_category": item["data"].get("error_category"),
                "error_action": item["data"].get("error_action"),
                "retryable": item["data"].get("retryable"),
                "provider": item["data"].get("provider"),
                "provider_code": item["data"].get("provider_code"),
                "http_status": item["data"].get("http_status"),
                "failure_source": failure_source,
                "failure_stage": failure_stage,
                "failure_scope": failure_scope,
                "user_action": item["data"].get("user_action"),
            },
        }

    def _user_failure_explanation(
        self,
        *,
        failure_source: str,
        failure_scope: str,
        failure_stage: str,
        user_action: str,
    ) -> str:
        """Build a user-facing explanation from structured failure fields."""
        source_labels = {
            "agent_turn": "当前这轮前台对话",
            "maintenance": "后台维护任务",
            "tool": "工具调用",
            "telemetry": "事件记录/遥测",
        }
        stage_labels = {
            "parent_model_provider": "父 Agent 调用模型服务商",
            "parent_graph": "父 Agent 图执行阶段发生异常",
            "parent_model_or_graph": "父 Agent 模型或图执行阶段发生异常",
            "context_summary": "后台上下文摘要压缩调用模型失败",
            "memory_extraction": "后台长期记忆提取调用模型失败",
            "subagent": "子 Agent 执行阶段失败",
            "tool_execution": "工具执行阶段失败",
        }
        scope_labels = {
            "current_turn": "只影响当前这一轮请求",
            "background_job": "只影响后台派生任务，不影响当前对话结果",
            "execution": "影响当前可恢复 Execution",
        }
        action_labels = {
            "revise_input_and_retry": "请修改输入后继续对话",
            "resume_later": "稍后可以使用 session resume 恢复",
            "inspect_status": "请先查看 session status 再决定恢复或丢弃",
        }
        lines = [
            "",
            f"失败来源：{source_labels.get(failure_source, failure_source)}。",
            f"LLM 调用位置：{stage_labels.get(failure_stage, failure_stage)}。",
            f"影响范围：{scope_labels.get(failure_scope, failure_scope)}。",
            "处理结果：本轮失败输入没有保存，Session 已回到上一轮成功提交的状态。",
            f"下一步：{action_labels.get(user_action, user_action)}。",
        ]
        return "\n".join(lines)

    def _stream_unconfigured_turn(
        self,
        session: SessionContext,
        run_id: str,
        missing: tuple[str, ...],
    ) -> Iterator[dict]:
        """Validate infrastructure without mutating conversation state."""
        store = self.state_store_factory()
        context_token = bind_context(
            workspace_id=session.workspace.workspace_id,
            session_id=session.session_id,
            run_id=run_id,
        )
        run_context_token = None
        try:
            _state, turn_index = store.load_session(session)
            run_context = AgentRunContext(
                run_id=run_id,
                session=session,
                turn_index=turn_index,
                limits=self.run_limits,
            )
            run_context_token = bind_run_context(run_context)
            emit_event(
                "diagnostic_started",
                "agent_service",
                "Started infrastructure diagnostic without LLM configuration.",
                {"session_name": session.session_name, "mode": "diagnostic"},
            )
            emit_event(
                "llm_configuration_missing",
                "agent_service",
                "LLM configuration is missing; returning a diagnostic response.",
                {"missing": list(missing)},
                level="warning",
            )
            response = (
                "Core 基础服务运行正常：CLI 与 daemon 已成功通信，Workspace 和 Session 已解析，"
                "数据库可正常创建并读取会话。\n\n"
                "当前未配置模型 API 密钥，因此本次请求不会写入对话历史、递增 turn_index，"
                "也不会调用 LLM 或工具。请设置 `LEARN_AGENT_LLM_API_KEY`；使用 OpenAI 兼容服务时可同时设置 "
                "`LEARN_AGENT_LLM_BASE_URL`，然后重新初始化用户配置并重启 Core。"
            )
            yield {"event": "token", "data": {"content": response}}
            emit_event(
                "diagnostic_finished",
                "agent_service",
                "Finished infrastructure diagnostic without LLM configuration.",
                {
                    "stop_reason": StopReason.LLM_NOT_CONFIGURED.value,
                    "tool_call_count": 0,
                    "mode": "diagnostic",
                },
            )
            yield {
                "event": "done",
                "data": {
                    "run_id": run_id,
                    "status": "ok",
                    "workspace_id": str(session.workspace.workspace_id),
                    "session_id": str(session.session_id),
                    "session_name": session.session_name,
                    "stop_reason": StopReason.LLM_NOT_CONFIGURED.value,
                    "tool_call_count": 0,
                },
            }
        except Exception as exc:
            record_error(
                "agent_service",
                "diagnostic_turn",
                exc,
                "Diagnostic turn failed.",
                event_type="turn_failed",
            )
            yield {
                "event": "error",
                "data": {
                    "type": "diagnostic_turn_failed",
                    "stop_reason": StopReason.TURN_ERROR.value,
                    "message": str(exc),
                    "run_id": run_id,
                },
            }
        finally:
            if run_context_token is not None:
                reset_context(run_context_token)
            reset_context(context_token)
            store.close()
