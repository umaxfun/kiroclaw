# Business Rules — Unit 2: Session Persistence

## BR-09: Session Store

| Rule | Description |
|------|-------------|
| BR-09.1 | Schema created on init (CREATE TABLE IF NOT EXISTS) — safe to call multiple times |
| BR-09.2 | Primary key is (user_id, thread_id) — one session per user per thread |
| BR-09.3 | get_session returns None for unknown (user_id, thread_id) — never raises |
| BR-09.4 | upsert_session uses INSERT OR REPLACE — overwrites existing row on conflict |
| BR-09.5 | upsert_session resets model to 'auto' on replace — new session = fresh model |
| BR-09.6 | get_model returns "auto" when no row exists (default) |
| BR-09.7 | set_model is a no-op when no row exists (UPDATE WHERE with no match) |
| BR-09.8 | All writes are committed immediately (autocommit per operation) |
| BR-09.9 | Timestamps are ISO 8601 strings (UTC) |
| BR-09.10 | Connection opened on init, closed explicitly via close() |

## BR-10: Workspace Directory

| Rule | Description |
|------|-------------|
| BR-10.1 | Path pattern: `{WORKSPACE_BASE_PATH}/{user_id}/{thread_id}/` |
| BR-10.2 | user_id and thread_id are converted to strings for path construction |
| BR-10.3 | Directories created with `mkdir(parents=True, exist_ok=True)` — idempotent |
| BR-10.4 | Workspace path stored in SQLite is the resolved absolute path |
| BR-10.5 | Directory creation happens before session/new (cwd must exist) |
| BR-10.6 | Directory creation is NOT part of SessionStore — it's orchestration logic |

## Test Strategy — Unit 2

### Integration Tests (real kiro-cli + real SQLite)

| Test | What it verifies |
|------|-----------------|
| test_session_continuity | Run 1: session/new, store mapping. Run 2: session/load with stored ID. Verify second run resumes session. |
| test_session_new_creates_workspace | First message creates workspace dir at expected path |
| test_session_load_uses_existing_workspace | Second message reuses existing workspace dir |

### Unit Tests (real SQLite, no kiro-cli)

| Test | What it verifies |
|------|-----------------|
| test_store_schema_creation | SessionStore init creates table, second init is idempotent |
| test_store_upsert_and_get | upsert a session, get it back, verify all fields |
| test_store_get_nonexistent | get_session for unknown key returns None |
| test_store_upsert_replaces | upsert twice for same key, second overwrites first |
| test_store_set_model | set_model updates model field |
| test_store_get_model_default | get_model for unknown key returns "auto" |
| test_store_get_model_after_set | set_model then get_model returns the set value |
| test_store_upsert_resets_model | set_model to "claude-sonnet-4", then upsert (new session) resets to "auto" |
| test_workspace_dir_creation | create_workspace_dir creates nested dirs, returns absolute path |
| test_workspace_dir_idempotent | create_workspace_dir called twice, no error |
