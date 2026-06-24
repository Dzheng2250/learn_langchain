"""Provider failure recovery and user-facing terminal error events."""

from collections.abc import Iterator

from src.core.agent.models import StopReason
from src.core.telemetry import record_error
from src.core.workspace.models import SessionContext


class ProviderFailureService:
    """Handle non-retryable provider failures outside the Agent turn runner."""

    def __init__(
        self,
        *,
        execution_repository=None,
        maintenance_repository=None,
        maintenance_scheduler=None,
    ) -> None:
        self.execution_repository = execution_repository
        self.maintenance_repository = maintenance_repository
        self.maintenance_scheduler = maintenance_scheduler

    def terminate_execution_after_error(self, session, execution, reason: str) -> None:
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

    def emit_terminal_provider_error(
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

