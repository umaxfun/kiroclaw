# Business Logic Model — Unit 2: Session Persistence

## C3 Session Store

```
SessionStore.__init__(db_path: str):
  1. Store db_path
  2. Open SQLite connection (sqlite3.connect)
  3. Enable row_factory = sqlite3.Row for dict-like access
  4. Execute CREATE TABLE IF NOT EXISTS sessions (schema from domain-entities.md)
  5. Connection stays open for the lifetime of the store

SessionStore.get_session(user_id: int, thread_id: int) -> SessionRecord | None:
  1. SELECT * FROM sessions WHERE user_id = ? AND thread_id = ?
  2. If row found: return SessionRecord from row data
  3. If no row: return None

SessionStore.upsert_session(user_id: int, thread_id: int, session_id: str, workspace_path: str) -> None:
  1. Get current ISO 8601 timestamp
  2. INSERT OR REPLACE INTO sessions
     (user_id, thread_id, session_id, workspace_path, model, created_at, updated_at)
     VALUES (?, ?, ?, ?, 'auto', ?, ?)
  3. Note: INSERT OR REPLACE resets model to 'auto' on conflict — this is acceptable
     because upsert only happens on session/new (first message in thread).
     If the user had set a model via /model, a new session means a fresh start.
  4. Commit

SessionStore.set_model(user_id: int, thread_id: int, model: str) -> None:
  1. UPDATE sessions SET model = ?, updated_at = ? WHERE user_id = ? AND thread_id = ?
  2. Commit
  3. Note: no-op if row doesn't exist (user hasn't sent a message yet).
     This is fine — /model before first message is a no-op.

SessionStore.get_model(user_id: int, thread_id: int) -> str:
  1. SELECT model FROM sessions WHERE user_id = ? AND thread_id = ?
  2. If row found: return model value
  3. If no row: return "auto" (default)

SessionStore.close() -> None:
  1. Close SQLite connection
```

## Workspace Directory Creation

Workspace directory creation is NOT part of SessionStore — it's orchestration logic that lives in the caller (main.py in Unit 2, Bot Handlers in Unit 3+).

```
create_workspace_dir(config: Config, user_id: int, thread_id: int) -> str:
  1. Construct path: Path(config.workspace_base_path) / str(user_id) / str(thread_id)
  2. Resolve to absolute path
  3. mkdir(parents=True, exist_ok=True)
  4. Return absolute path as string
```

This is a utility function, not a method on any component. It's called before `session/new` to ensure the `cwd` directory exists.

## Updated main.py Flow (Unit 2 — CLI Version)

```
main.py (Unit 2):
  1. Config.load()
  2. Config.validate_kiro_cli()
  3. WorkspaceProvisioner(config).provision()
  4. SessionStore(db_path="./tg-acp.db")
  5. Parse --user-id and --thread-id from CLI args (argparse, defaults: 1 and 1)
  6. Construct workspace_path = create_workspace_dir(config, user_id, thread_id)
  7. client = await ACPClient.spawn(config.kiro_agent_name, config.log_level)
  8. await client.initialize()
  9. session_record = store.get_session(user_id, thread_id)
  10. If session_record is not None:
      a. await client.session_load(session_record.session_id)
      b. session_id = session_record.session_id
      c. is_new_session = False
      d. Print "Loaded existing session {session_id}"
  11. Else:
      a. session_id = await client.session_new(cwd=workspace_path)
      b. store.upsert_session(user_id, thread_id, session_id, workspace_path)
      c. is_new_session = True
      d. Print "Created new session {session_id}"
  12. Select message based on session state:
      - If is_new_session: "Remember this number: 1234. Just confirm you memorized it."
      - If loaded session: "What number did I ask you to remember?"
  13. Stream response to stdout (same as Unit 1)
  14. await client.kill()
  15. store.close()
```

Key changes from Unit 1:
- Steps 4-6: SessionStore and workspace dir creation added
- Steps 9-11: Session lookup with load/new branching replaces unconditional session/new
- Step 12: Hardcoded 2-run demo — no user input prompt. Run once to memorize, run again to recall.
- Step 15: Store cleanup added

This is still a throwaway CLI entry point — replaced by aiogram bot in Unit 3.
