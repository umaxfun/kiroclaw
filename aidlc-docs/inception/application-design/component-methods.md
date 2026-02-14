# Component Methods

Method signatures for each component. Detailed business rules will be defined in Functional Design per unit.

---

## C1: ACP Client

```python
class ACPClient:
    async def spawn(agent_name: str) -> ACPClient
        # Spawn kiro-cli acp subprocess with --agent flag
        # Return initialized client

    async def initialize() -> dict
        # Send initialize JSON-RPC, return agent capabilities

    async def session_new(cwd: str) -> str
        # Create new session, return session_id

    async def session_load(session_id: str) -> None
        # Load existing session

    async def session_prompt(session_id: str, content: list[dict]) -> AsyncIterator[dict]
        # Send prompt, yield session/update notifications until turn_end

    async def session_cancel(session_id: str) -> None
        # Cancel in-flight prompt

    async def session_set_model(session_id: str, model: str) -> None
        # Set model for session

    def is_alive() -> bool
        # Check if subprocess is still running

    async def kill() -> None
        # Terminate subprocess
```

---

## C2: Process Pool

```python
class ProcessPool:
    async def start(agent_name: str) -> None
        # Initialize pool with 1 warm process, store agent_name for spawning

    async def acquire() -> ACPClient
        # Get a free process (or queue if at capacity), return ready client

    async def release(client: ACPClient) -> None
        # Return process to pool, start idle timer

    async def shutdown() -> None
        # Kill all processes, drain queue

    # Internal:
    async def _spawn_and_init() -> ACPClient
        # Spawns ACPClient with agent_name
    def _start_idle_timer(client: ACPClient) -> None
    def _cancel_idle_timer(client: ACPClient) -> None
    async def _process_queue() -> None
```

---

## C3: Session Store

```python
class SessionStore:
    def __init__(db_path: str) -> None
        # Open/create SQLite database, ensure schema

    def get_session(user_id: int, thread_id: int) -> SessionRecord | None
        # Lookup session by telegram user+thread

    def upsert_session(user_id: int, thread_id: int, session_id: str, workspace_path: str) -> None
        # Create or update session mapping

    def set_model(user_id: int, thread_id: int, model: str) -> None
        # Update model selection for a thread

    def get_model(user_id: int, thread_id: int) -> str
        # Get model for thread, default "auto"
```

---

## C4: Stream Writer

```python
class StreamWriter:
    def __init__(bot: Bot, chat_id: int, thread_id: int, draft_id: int) -> None

    async def write_chunk(text: str) -> None
        # Append to buffer, call sendMessageDraft with sliding window (plain text, no parse_mode)

    async def finalize() -> list[tuple[str, str]]
        # Convert buffer from Markdown to Telegram HTML (chatgpt-md-converter)
        # Parse/strip <send_file> tags BEFORE conversion, collect (path, description) tuples
        # Split HTML with tag-aware splitter:
        #   - Inline tags (<b>, <i>, <code>, <u>, <s>, <a>): backtrack before opening tag
        #   - Block tags (<pre>, <blockquote>): close at split, reopen at next segment
        # Send via sendMessage with parse_mode=HTML
        # If HTML conversion fails, fall back to plain text; if Telegram rejects a segment, retry as plain text
        # Returns list of (file_path, description) tuples found in <send_file> tags

    def cancel() -> None
        # Discard buffer, stop writing
```

---

## C5: File Handler

```python
class FileHandler:
    async def download_to_workspace(message: Message, workspace_path: str) -> str
        # Download file from Telegram message to workspace, return local path

    async def send_file(bot: Bot, chat_id: int, thread_id: int, file_path: str, caption: str | None = None) -> None
        # Send file from workspace to Telegram thread, with optional caption

    def validate_path(file_path: str, workspace_path: str) -> bool
        # Ensure file_path is within workspace boundary
```

---

## C6: Bot Handlers

```python
# aiogram router handlers — not a class, registered on Router

async def cmd_start(message: Message) -> None
    # Send welcome message

async def cmd_model(message: Message) -> None
    # Parse argument: no arg = list models, with arg = set model

async def handle_message(message: Message) -> None
    # Main orchestration:
    # 1. Cancel in-flight prompt for this thread if any
    # 2. Get session from SessionStore (may be None for new threads)
    # 3. If new thread: create workspace dir ./workspaces/{uid}/{tid}/
    # 4. If file attached, download via FileHandler
    # 5. Acquire process from ProcessPool
    # 6. Load session (existing) or create new session with cwd
    # 7. If new session: upsert session mapping in SessionStore
    # 8. Send prompt, stream via StreamWriter
    # 9. On finalize, send any <send_file> files via FileHandler
    # 10. Release process back to pool
```

---

## C7: Config

```python
class Config:
    bot_token: str
    workspace_base_path: str        # default: "./workspaces/"
    max_processes: int              # default: 5
    idle_timeout_seconds: int       # default: 30
    kiro_agent_name: str            # REQUIRED — name of the custom global agent (no default, must be set in .env)
    log_level: str                  # default: "INFO" — controls stderr capture and bot event logging
    kiro_config_path: str           # default: "./kiro-config/" — path to template directory

    @classmethod
    def load() -> Config
        # Load from .env, validate, fail fast on missing required values

    def validate_kiro_cli() -> None
        # Validate all startup prerequisites:
        # - kiro-cli is on PATH
        # - KIRO_AGENT_NAME is set
        # - kiro-config/ template directory exists with agent config template
        # - WORKSPACE_BASE_PATH is writable
```

---

## C8: Workspace Provisioner

```python
class WorkspaceProvisioner:
    def __init__(config: Config) -> None
        # Store config reference for agent name, template path

    def provision() -> None
        # Sync ~/.kiro/ with kiro-config/ template using prefix-based matching:
        # 1. SAFETY CHECKS (abort if any fail):
        #    - KIRO_AGENT_NAME is non-empty, >= 3 chars
        #    - KIRO_AGENT_NAME matches ^[a-zA-Z0-9_-]+$ (no wildcards, dots, slashes)
        #    - kiro-config/ template contains at least the agent JSON file
        #    - Count files matching prefix in each target dir — abort if > 20 total
        # 2. For each of agents/, steering/, skills/ in ~/.kiro/:
        #    - Delete all files/dirs matching {KIRO_AGENT_NAME}* prefix
        #    - Copy matching files from kiro-config/ template
        # 3. Agent config ~/.kiro/agents/{agent_name}.json is REQUIRED
        # Files outside the bot's prefix are never touched.

    def provision_thread_override(thread_workspace_path: str, agent_config: dict) -> None
        # Create .kiro/agents/{agent_name}.json in a specific thread directory
        # For per-thread custom steering (rare, on-demand)

    def _sync_prefix(src_dir: str, dst_dir: str, prefix: str) -> None
        # Delete all entries matching prefix* in dst_dir, copy from src_dir
```
