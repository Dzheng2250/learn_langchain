"""Built-in handler adapters for external lifecycle hooks."""

import json
import subprocess
from dataclasses import dataclass

from src.core.hooks.models import HookAction, HookContext, HookDecision


@dataclass(frozen=True)
class CommandHook:
    """Run one explicitly configured argv command without a shell."""

    command: tuple[str, ...]
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("Command hook argv must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("Command hook timeout must be greater than zero")

    def handle(self, context: HookContext) -> HookDecision:
        request = {
            "point": context.point.value,
            "subject": context.subject,
            "workspace_id": context.workspace_id,
            "session_id": context.session_id,
            "execution_id": context.execution_id,
            "run_id": context.run_id,
            "payload": dict(context.payload),
        }
        completed = subprocess.run(
            self.command,
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            cwd=context.workspace_root or None,
            timeout=self.timeout_seconds,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Hook command exited with status {completed.returncode}")
        raw = completed.stdout.strip()
        if not raw:
            return HookDecision()
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise ValueError("Hook command response must be a JSON object")
        return HookDecision(
            action=HookAction(response.get("action", HookAction.CONTINUE.value)),
            payload=response.get("payload"),
            reason=str(response.get("reason", "")),
        )
