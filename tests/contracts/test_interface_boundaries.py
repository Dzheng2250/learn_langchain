import ast
import inspect
import unittest
from pathlib import Path

from src.core.agent.loop import TurnExecutionLoop
from src.core.agent.loop_errors import TurnLoopErrorHandler
from src.core.agent.loop_pause import TurnLoopPauseHandler
from src.core.agent.request_stream import AgentRequestStreamService
from src.core.agent.service import AgentTurnService
from src.core.agent.service_lifecycle import AgentServiceLifecycle
from src.core.agent.slices import SliceExecutionService
from src.core.context.loader import ConversationContextLoader
from src.core.diagnostics import DiagnosticTurnService
from src.core.execution import ExecutionLifecycleService
from src.core.finalization import TurnFinalizer
from src.core.session import SessionLifecycleService
from tests.support.paths import REPOSITORY_ROOT


APPLICATION_PATHS = (
    REPOSITORY_ROOT / "src" / "core" / "agent",
    REPOSITORY_ROOT / "src" / "core" / "execution",
    REPOSITORY_ROOT / "src" / "core" / "finalization",
    REPOSITORY_ROOT / "src" / "core" / "handlers",
    REPOSITORY_ROOT / "src" / "core" / "session",
)


class InterfaceBoundaryTest(unittest.TestCase):
    def test_application_layer_does_not_import_sqlite_or_adapters(self):
        violations = []
        for root in APPLICATION_PATHS:
            for path in root.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    else:
                        continue
                    for name in names:
                        if name == "sqlite3" or name.startswith("src.core.adapters"):
                            violations.append(f"{path.relative_to(REPOSITORY_ROOT)} imports {name}")

        self.assertEqual([], violations)

    def test_agent_turn_service_is_a_composed_facade(self):
        parameters = set(inspect.signature(AgentTurnService).parameters)

        self.assertEqual(
            {"async_turn_runner", "request_stream_service", "service_lifecycle"},
            parameters,
        )

    def test_turn_execution_loop_requires_preassembled_collaborators(self):
        parameters = inspect.signature(TurnExecutionLoop).parameters

        for name in ("observer", "error_handler", "pause_handler", "config"):
            self.assertIn(name, parameters)
            self.assertIs(parameters[name].default, inspect.Parameter.empty)
        self.assertNotIn("max_auto_slices", parameters)
        self.assertNotIn("provider_failure_service", parameters)

    def test_foreground_context_and_finalization_do_not_accept_state_facade(self):
        loader_parameters = inspect.signature(ConversationContextLoader).parameters
        self.assertIn("session_store", loader_parameters)
        self.assertIn("memory_store", loader_parameters)

        loop_parameters = inspect.signature(TurnExecutionLoop).parameters
        self.assertNotIn("state_store_factory", loop_parameters)

        finalize_parameters = inspect.signature(TurnFinalizer.finalize).parameters
        self.assertNotIn("store", finalize_parameters)

        lifecycle_parameters = inspect.signature(AgentServiceLifecycle).parameters
        self.assertIn("state_initializer", lifecycle_parameters)
        self.assertNotIn("state_store_factory", lifecycle_parameters)

    def test_runtime_graph_resolver_depends_on_workspace_runtime_port(self):
        path = REPOSITORY_ROOT / "src" / "core" / "agent" / "runtime_graph.py"
        source = path.read_text(encoding="utf-8-sig")
        self.assertIn("WorkspaceRuntimeProvider", source)
        self.assertNotIn("src.core.workspace.runtime", source)

    def test_request_stream_depends_on_behavior_contracts(self):
        parameters = inspect.signature(AgentRequestStreamService).parameters
        self.assertIn("runtime_graph_resolver", parameters)
        self.assertIn("turn_execution_loop", parameters)

        path = REPOSITORY_ROOT / "src" / "core" / "agent" / "request_stream.py"
        source = path.read_text(encoding="utf-8")
        forbidden = (
            "DiagnosticTurnService",
            "ExecutionLifecycleService",
            "RuntimeGraphResolver",
            "TurnExecutionLoop",
        )
        self.assertFalse(any(name in source for name in forbidden))

        diagnostic_parameters = inspect.signature(DiagnosticTurnService).parameters
        self.assertIn("session_store", diagnostic_parameters)
        self.assertNotIn("state_store_factory", diagnostic_parameters)

    def test_agent_execution_components_depend_on_named_ports(self):
        expected = {
            ExecutionLifecycleService: "execution_store",
            SliceExecutionService: "execution_store",
            TurnLoopErrorHandler: "execution_store",
            TurnLoopPauseHandler: "execution_store",
        }
        for component, port_name in expected.items():
            with self.subTest(component=component.__name__):
                parameters = inspect.signature(component).parameters
                self.assertIn(port_name, parameters)
                self.assertNotIn("execution_repository", parameters)

    def test_session_lifecycle_service_accepts_ports_not_sqlite_factories(self):
        parameters = inspect.signature(SessionLifecycleService).parameters

        self.assertIn("lifecycle_store", parameters)
        self.assertNotIn("workspace_repository", parameters)
        self.assertNotIn("state_store_factory", parameters)

    def test_business_modules_import_llm_contracts_not_provider_implementation(self):
        violations = []
        core_root = REPOSITORY_ROOT / "src" / "core"
        allowed = {
            core_root / "container.py",
            core_root / "llm" / "__init__.py",
            core_root / "llm" / "provider.py",
        }
        for path in core_root.rglob("*.py"):
            if path in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "src.core.llm.provider":
                    violations.append(str(path.relative_to(REPOSITORY_ROOT)))

        self.assertEqual([], violations)

    def test_concrete_model_provider_is_created_only_in_composition_root(self):
        violations = []
        core_root = REPOSITORY_ROOT / "src" / "core"
        allowed = {
            core_root / "container.py",
            core_root / "llm" / "provider.py",
        }
        for path in core_root.rglob("*.py"):
            if path in allowed:
                continue
            if "OpenAICompatibleProvider(" in path.read_text(encoding="utf-8"):
                violations.append(str(path.relative_to(REPOSITORY_ROOT)))

        self.assertEqual([], violations)

    def test_agent_layer_does_not_construct_concrete_model_provider(self):
        violations = []
        for path in (REPOSITORY_ROOT / "src" / "core" / "agent").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.module != "src.core.llm.provider":
                    continue
                if any(alias.name == "OpenAICompatibleProvider" for alias in node.names):
                    violations.append(str(path.relative_to(REPOSITORY_ROOT)))

        self.assertEqual([], violations)

    def test_application_layer_does_not_import_dependency_injector(self):
        violations = []
        for root in APPLICATION_PATHS:
            for path in root.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    else:
                        continue
                    for name in names:
                        if name == "dependency_injector" or name.startswith(
                            "dependency_injector."
                        ):
                            violations.append(
                                f"{path.relative_to(REPOSITORY_ROOT)} imports {name}"
                            )

        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
