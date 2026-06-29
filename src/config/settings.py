"""Non-secret runtime configuration.

Committed defaults live here. Deployment-specific values and secrets can be
overridden with environment variables or the Core user-level .env file.
"""

from src.config.env import env_bool, env_float, env_int, env_str


DEBUG_AGENT = env_bool("LEARN_AGENT_DEBUG", False)

# Per-Slice and per-Grant execution limits. A Slice is one bounded LangGraph
# invocation; a Grant is the automatic work allowed by one chat/resume request.
MAX_GRAPH_STEPS_PER_SLICE = env_int("LEARN_AGENT_MAX_GRAPH_STEPS_PER_SLICE", 20)
MAX_AUTO_SLICES_PER_GRANT = env_int("LEARN_AGENT_MAX_AUTO_SLICES_PER_GRANT", 3)
MAX_GRANT_WALL_SECONDS = env_float("LEARN_AGENT_MAX_GRANT_WALL_SECONDS", 600.0)
MAX_PARALLEL_TOOL_CALLS = env_int("LEARN_AGENT_MAX_PARALLEL_TOOL_CALLS", 4)
TOOL_APPROVAL_ENABLED = env_bool("LEARN_AGENT_TOOL_APPROVAL_ENABLED", True)
HOST_EXECUTION_ENABLED = env_bool("LEARN_AGENT_HOST_EXECUTION_ENABLED", False)
TOOL_HOOK_TIMEOUT_SECONDS = env_float("LEARN_AGENT_TOOL_HOOK_TIMEOUT_SECONDS", 2.0)
TOOL_DEFAULT_TIMEOUT_SECONDS = env_float("LEARN_AGENT_TOOL_DEFAULT_TIMEOUT_SECONDS", 60.0)
TOOL_NETWORK_POLICY = env_str("LEARN_AGENT_NETWORK_POLICY", "deny").strip().lower()
MAX_CONTROLLED_EXECUTIONS_PER_GRANT = env_int(
    "LEARN_AGENT_MAX_CONTROLLED_EXECUTIONS_PER_GRANT", 12
)
MAX_DELEGATIONS_PER_GRANT = env_int("LEARN_AGENT_MAX_DELEGATIONS_PER_GRANT", 6)
HARD_MAX_TOOL_CALLS_PER_GRANT = env_int("LEARN_AGENT_HARD_MAX_TOOL_CALLS_PER_GRANT", 100)
# Backward-compatible name while callers migrate to explicit Slice terminology.
MAX_GRAPH_STEPS = MAX_GRAPH_STEPS_PER_SLICE
BASH_PATH = "bash"

# Shared model configuration. The generic LEARN_AGENT names take precedence;
# LLM configuration is provider-neutral; the default provider is Anthropic.
MODEL = env_str("LEARN_AGENT_MODEL", "")
MODEL_CONTEXT_LIMIT = env_int("LEARN_AGENT_MODEL_CONTEXT_LIMIT", 128_000)
LLM_API_KEY = env_str("LEARN_AGENT_LLM_API_KEY", "")
LLM_BASE_URL = env_str("LEARN_AGENT_LLM_BASE_URL", "")
REASONING_DISPLAY = env_str("LEARN_AGENT_REASONING_DISPLAY", "collapsed").strip().lower()
REASONING_PREVIEW_LIMIT = env_int("LEARN_AGENT_REASONING_PREVIEW_LIMIT", 12000)

# Container command sandbox limits. The Workspace is copied into a temporary
# directory and mounted read-only; command output is truncated before LLM input.
DOCKER_IMAGE = "python:3.12-slim"
DOCKER_TIMEOUT_SECONDS = 10
DOCKER_MEMORY = "256m"
DOCKER_CPUS = "0.5"
DOCKER_OUTPUT_LIMIT = 4000
FILE_READ_CHUNK_LINES = 200
FILE_READ_OUTPUT_LIMIT = 8000
ENTIRE_FILE_MAX_LINES = 300

PARENT_FILE_READ_LINES = 80
PARENT_FILE_READ_OUTPUT_LIMIT = 5000

FILE_AGENT_CHUNK_LINES = 160
FILE_AGENT_MAX_CHUNKS = 30
FILE_AGENT_NOTES_LIMIT = 12000
LARGE_FILE_CHUNK_LINES = 220
LARGE_FILE_MAX_CHUNKS = 80
LARGE_FILE_MAP_WORKERS = 4
LARGE_FILE_SUMMARY_LIMIT = 16000

SUBAGENT_MAX_STEPS = 32
SUBAGENT_CONTEXT_MESSAGE_LIMIT = 8
SUBAGENT_RESULT_LIMIT = 6000

# Local skills are discovered only under the active Workspace.
SKILLS_DIR = "skills"
SKILL_FILE_NAME = "SKILL.md"
SKILL_READ_OUTPUT_LIMIT = 8000

# Short-term context keeps whole conversation Turns. A Turn may contain several
# LangChain messages, such as user, assistant and tool-result messages.
RECENT_TURN_LIMIT = 6
# Backward-compatible alias for older internal callers; new code should use
# RECENT_TURN_LIMIT so tool-heavy Turns are not sliced in half.
RECENT_MESSAGE_LIMIT = RECENT_TURN_LIMIT
# Compression starts when token count, Turn count, or character count crosses
# its trigger; old Turns are summarized while recent Turns remain verbatim.
# Token-based compression is the primary safety valve for very large Turns.
SUMMARY_TRIGGER_TOKEN_LIMIT = env_int("LEARN_AGENT_SUMMARY_TRIGGER_TOKEN_LIMIT", 80_000)
SUMMARY_TRIGGER_TURN_LIMIT = 20
# Backward-compatible alias for older internal callers; new code should use
# SUMMARY_TRIGGER_TURN_LIMIT.
SUMMARY_TRIGGER_MESSAGE_LIMIT = SUMMARY_TRIGGER_TURN_LIMIT
SUMMARY_TRIGGER_CHAR_LIMIT = 24000
SESSION_SUMMARY_MAX_CHARS = 8000
SUMMARY_SOURCE_CHAR_LIMIT = 12000

MEMORY_ENABLED = True
# Storage backend selectors. The first implementation keeps SQLite as the only
# production adapter, while these names reserve the dependency-inversion seam.
CONVERSATION_HISTORY_BACKEND = env_str("LEARN_AGENT_CONVERSATION_HISTORY_BACKEND", "sqlite")
MEMORY_BACKEND = env_str("LEARN_AGENT_MEMORY_BACKEND", "sqlite")
TASK_BACKEND = env_str("LEARN_AGENT_TASK_BACKEND", "sqlite")
CHECKPOINT_BACKEND = env_str("LEARN_AGENT_CHECKPOINT_BACKEND", "sqlite")
# Optional future PostgreSQL business projection. Disabled means local SQLite
# remains authoritative without accumulating an unconsumed projection outbox.
POSTGRES_PROJECTION_ENABLED = env_bool("LEARN_AGENT_POSTGRES_PROJECTION_ENABLED", False)
# LEARN_AGENT_DATABASE_URL overrides the split host/port/name/user/password
# settings. All Session, memory, and observation data share this PostgreSQL.
MEMORY_DB_URL = env_str("LEARN_AGENT_DATABASE_URL", "")
MEMORY_DB_HOST = env_str("LEARN_AGENT_DB_HOST", "127.0.0.1")
MEMORY_DB_PORT = env_int("LEARN_AGENT_DB_PORT", 5432)
MEMORY_DB_NAME = env_str("LEARN_AGENT_DB_NAME", "learn_agent")
MEMORY_DB_USER = env_str("LEARN_AGENT_DB_USER", "postgres")
MEMORY_DB_PASSWORD = env_str("LEARN_AGENT_DB_PASSWORD", "postgres")
DEFAULT_SESSION_ID = "default"
MEMORY_RETRIEVAL_LIMIT = 6
# Bootstrap memories are injected only on the first real Turn of a Session.
MEMORY_BOOTSTRAP_LIMIT = 4
MEMORY_CONTEXT_CHAR_LIMIT = 6000
MEMORY_EXTRACTION_ENABLED = True
MEMORY_EXTRACTION_INTERVAL_TURNS = 5
MEMORY_EXTRACTION_MIN_CHARS = 1200
MEMORY_EXTRACTION_HINT_KEYWORDS = [
    "记住",
    "保存",
    "偏好",
    "约定",
    "规则",
    "以后",
    "长期",
    "remember",
    "preference",
    "always",
]
MEMORY_MIN_IMPORTANCE = 3
MEMORY_EXTRACT_SOURCE_CHAR_LIMIT = 12000

AGENT_EVENTS_ENABLED = True
# Event sinks observe business behavior and must never determine its result.
AGENT_EVENTS_SQLITE_ENABLED = env_bool("LEARN_AGENT_EVENTS_SQLITE_ENABLED", True)
AGENT_EVENTS_SQLITE_PATH = env_str("LEARN_AGENT_EVENTS_SQLITE_PATH", "")
AGENT_EVENTS_SQLITE_RETENTION_DAYS = env_int(
    "LEARN_AGENT_EVENTS_SQLITE_RETENTION_DAYS",
    30,
)
AGENT_EVENTS_POSTGRES_ENABLED = env_bool("LEARN_AGENT_EVENTS_POSTGRES_ENABLED", False)
AGENT_EVENTS_CONSOLE_ENABLED = False
AGENT_EVENTS_FILE_ENABLED = env_bool("LEARN_AGENT_EVENTS_FILE_ENABLED", True)
AGENT_EVENTS_FILE_PATH = env_str("LEARN_AGENT_EVENTS_FILE_PATH", "")
AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT = env_int(
    "LEARN_AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT",
    1000,
)
AGENT_EVENTS_ASYNC_WRITE = env_bool("LEARN_AGENT_EVENTS_ASYNC_WRITE", True)
# PostgreSQL event writes are queued and flushed by size or elapsed interval.
AGENT_EVENTS_BATCH_SIZE = env_int("LEARN_AGENT_EVENTS_BATCH_SIZE", 50)
AGENT_EVENTS_FLUSH_INTERVAL_SECONDS = env_float(
    "LEARN_AGENT_EVENTS_FLUSH_INTERVAL_SECONDS",
    1.0,
)
AGENT_EVENTS_QUEUE_MAX_SIZE = env_int("LEARN_AGENT_EVENTS_QUEUE_MAX_SIZE", 1000)

# Cross-layer daemon trace. Trace is best-effort diagnostic data and never a
# source of truth for Session state, recovery, billing, or compliance.
TRACE_ENABLED = env_bool("LEARN_AGENT_TRACE_ENABLED", True)
TRACE_DIR = env_str("LEARN_AGENT_TRACE_DIR", "")
TRACE_RETENTION_DAYS = env_int("LEARN_AGENT_TRACE_RETENTION_DAYS", 14)
TRACE_BATCH_SIZE = env_int("LEARN_AGENT_TRACE_BATCH_SIZE", 100)
TRACE_FLUSH_INTERVAL_SECONDS = env_float("LEARN_AGENT_TRACE_FLUSH_INTERVAL_SECONDS", 0.5)
TRACE_QUEUE_MAX_SIZE = env_int("LEARN_AGENT_TRACE_QUEUE_MAX_SIZE", 5000)
TRACE_DATA_PREVIEW_LIMIT = env_int("LEARN_AGENT_TRACE_DATA_PREVIEW_LIMIT", 500)

CORE_HOST = env_str("LEARN_AGENT_CORE_HOST", "127.0.0.1")
CORE_PORT = env_int("LEARN_AGENT_CORE_PORT", 18765)
# Maximum size of one JSON-RPC NDJSON frame, not the complete conversation.
CORE_MAX_MESSAGE_BYTES = 1_048_576
CORE_SHUTDOWN_TIMEOUT_SECONDS = env_float("LEARN_AGENT_CORE_SHUTDOWN_TIMEOUT_SECONDS", 10)
CORE_CONNECT_TIMEOUT_SECONDS = env_float("LEARN_AGENT_CORE_CONNECT_TIMEOUT_SECONDS", 3)
CORE_DAEMON_STARTUP_TIMEOUT_SECONDS = env_float("LEARN_AGENT_DAEMON_STARTUP_TIMEOUT_SECONDS", 15)
CORE_DAEMON_STOP_TIMEOUT_SECONDS = env_float("LEARN_AGENT_DAEMON_STOP_TIMEOUT_SECONDS", 15)
# Maximum number of different Session Turns executing concurrently. The same
# Session remains serialized by its UUID lock regardless of this value.
CORE_AGENT_WORKERS = env_int("LEARN_AGENT_CORE_AGENT_WORKERS", 4)
CORE_RUNTIME_DIR = ""
PG_DUMP_PATH = env_str("LEARN_AGENT_PG_DUMP_PATH", "")
POSTGRES_DOCKER_CONTAINER = env_str("LEARN_AGENT_DB_CONTAINER", "learn-agent-postgres")
