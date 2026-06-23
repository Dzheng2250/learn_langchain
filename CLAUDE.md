# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture Overview

A local coding agent with a dual-process architecture:

- **`learn-agent` (CLI)**: Frontend process — command parsing, workspace discovery, daemon lifecycle, user I/O and streaming display.
- **`learn-agent-core` (Core daemon)**: Backend process — LangGraph agent loop, tools, LLM, session/memory/execution state, maintenance tasks.
- **Communication**: Local TCP + NDJSON + JSON-RPC, authenticated via ephemeral token.

### State Storage

| Store | Role | Authoritative |
|---|---|---|
| `state.db` (SQLite) | Workspace, Session, messages, memory, executions, maintenance jobs | ✅ Yes |
| `checkpoints.db` (SQLite) | LangGraph checkpoint threads for resumable slices | No |
| `artifacts/` | Deduplicated large tool content | No |
| `telemetry/` | Domain observation events (tool, memory, context events) | No |
| `traces/` | Cross-layer diagnostic timeline (JSONL) | No |
| PostgreSQL | Optional migration source and event sink | No |

### Agent Execution Model

```
Run:        One chat or resume request (one run_id)
Execution:  A task that can span multiple Runs (one execution_id)
Slice:      One budget-limited LangGraph step batch
```

- **AgentTurnService** orchestrates load → execute → commit for one turn.
- **LangGraph** drives the agent ↔ tools loop via `StateGraph(MessagesState)`.
- **TurnCoordinator** manages budget slicing and finalization.
- **TurnFinalizer + CompletedTurnCommitter** atomically commit messages, context, execution state, and maintenance jobs.
- **MaintenanceScheduler** runs background tasks (context summary, memory extraction, checkpoint cleanup).
- **WorkspaceRuntimeRegistry** caches per-workspace compiled graphs (normal + goal mode).

### Key Source Layout

```
src/
  config/        Shared CLI + Core configuration (.env, paths, settings)
  ipc/           JSON-RPC models and auth
  cli/           CLI entry, commands, daemon lifecycle, renderer
    commands/    One module per CLI subcommand (chat, start, stop, status, session, trace)
  core/
    app.py       Composition root — wires all services together
    main.py      Core daemon entry point (serve, migrate, init-user-config)
    agent/       AgentTurnService, LangGraph graph factory, budget, coordinator
    state/       SQLite schema, migrations, repositories (execution, workspace, checkpoint)
    workspace/   Workspace identity, runtime factory/cache
    tools/       Tool registry, implementations, ObservedToolNode wrapper
    llm/         ModelProvider (OpenAI-compatible), model configuration
    context/     Short-term context management
    memory/      Message archival and long-term memory extraction
    database/    Optional PostgreSQL connection and legacy migration
    telemetry/   Structured observation events, EventBus, sinks
    tracing/     Cross-layer diagnostic timeline (JSONL)
    bus/         JSON-RPC validation, auth, routing
    transport/   TCP + NDJSON socket server
    handlers/    JSON-RPC method handlers (AgentHandlers, CoreHandlers)
    tasks/       Goal-mode private task planning (task repository, service)
    finalization/ Completed turn commit + maintenance job enqueue
    maintenance/ Job queue, scheduler, recovery coordinator
    errors/      Provider error classification, handling policy
    prompts/     System prompt templates for parent/subagent
    hooks/       Legacy event import compatibility layer
    skills/      Skill manifest parsing and storage
tests/
  unit/          Single-component, pure logic, mock-based tests
  integration/   Multi-component tests using local SQLite, TCP, thread pools
  contracts/     Documentation and architecture drift detection
  optional/      Tests requiring explicit opt-in (e.g., PostgreSQL)
```

## Commands

### Development Install

```
pip install -e .              # editable install
```

### Python Environment

The project uses a Conda environment at `D:\app\anaconda\envs\agent_learn`:

```
D:/app/anaconda/envs/agent_learn/python.exe -m pip install -e .
```

### Running Tests

```shell
# Full suite
python -B -m unittest discover -s tests -v

# By category
python -B -m unittest discover -s tests/unit -t . -v
python -B -m unittest discover -s tests/integration -t . -v
python -B -m unittest discover -s tests/contracts -t . -v

# Single test module or method
python -B -m unittest tests.unit.test_tracing -v
python -B -m unittest tests.integration.test_core_bus.CoreServerIntegrationTest -v
```

### Running the Agent

```shell
learn-agent start                            # start Core daemon
learn-agent status                           # check daemon health
learn-agent chat                             # interactive chat
learn-agent chat --session default "..."     # single question
learn-agent chat --goal --session default "..."  # goal mode (multi-step task planning)
learn-agent stop                             # graceful stop
learn-agent stop --force                     # force-stop stuck daemon
learn-agent session list                     # list sessions
learn-agent session history --session default  # show session history
learn-agent trace                            # show recent trace
```

### Configuration

Set `.env` variables (see `.env.example`):

| Variable | Purpose |
|---|---|
| `LEARN_AGENT_LLM_API_KEY` | OpenAI-compatible API key |
| `LEARN_AGENT_LLM_BASE_URL` | API base URL |
| `LEARN_AGENT_MODEL` | Model name (required; no default) |
| `LEARN_AGENT_CORE_PORT` | Core daemon port (default: 18765) |

### CLI Daemon Lifecycle

- `start_daemon()` spawns Core as a detached process, waits for HTTP ping.
- `stop_daemon()` sends `core.shutdown` RPC, waits for process exit.
- `--force` uses SIGTERM → SIGKILL (Unix) to terminate a stuck daemon.

### First-time Setup

```shell
python -c "from shutil import copyfile; copyfile('.env.example', '.env')"
# Edit .env with your API key, then:
learn-agent-core init-user-config --from-env .env
learn-agent start
learn-agent status
learn-agent chat
```

## Key Design Decisions

- **Local-first**: SQLite `state.db` is the authoritative business state. PostgreSQL is optional for telemetry/legacy migration only.
- **At-least-once commit**: Messages, context, execution state, and maintenance jobs are committed in the same SQLite transaction before the user response is returned.
- **Resumable executions**: A long task is split into budget-limited slices. Paused executions can be recovered with `session resume`.
- **Goal mode**: When `--goal` is set, the parent agent receives private task planning tools (`task_plan`, `task_update`, `task_list`, `task_get`) and can decompose complex goals across multiple chat/resume cycles.
- **CLI does not touch state**: The CLI only renders events; all business logic runs in Core.
- **Tool call parameter sanitization**: Sensitive args (api_key, password, token, etc.) are redacted as `[REDACTED]` in CLI rendering. Depth-limited to 20 levels.