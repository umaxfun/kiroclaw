# Domain Entities — Unit 2: Session Persistence

## SessionRecord

Represents a stored mapping between a Telegram thread and a Kiro session.

```
SessionRecord
  user_id: int                      # Telegram user ID
  thread_id: int                    # Telegram forum topic (message_thread_id)
  session_id: str                   # Kiro CLI session ID (from session/new response)
  workspace_path: str               # Absolute path to thread workspace dir
  model: str                        # Selected model, default "auto"
```

## SQLite Schema

Database file: `./tg-acp.db` (relative to bot working directory, per FR-05).

```sql
CREATE TABLE IF NOT EXISTS sessions (
    user_id       INTEGER NOT NULL,
    thread_id     INTEGER NOT NULL,
    session_id    TEXT    NOT NULL,
    workspace_path TEXT   NOT NULL,
    model         TEXT    NOT NULL DEFAULT 'auto',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    PRIMARY KEY (user_id, thread_id)
);
```

### Column Details

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| user_id | INTEGER | NOT NULL, PK | Telegram user ID |
| thread_id | INTEGER | NOT NULL, PK | Telegram message_thread_id |
| session_id | TEXT | NOT NULL | Kiro session UUID from session/new |
| workspace_path | TEXT | NOT NULL | Absolute path: `{base}/{user_id}/{thread_id}/` |
| model | TEXT | NOT NULL, DEFAULT 'auto' | Model selection per FR-09 |
| created_at | TEXT | NOT NULL | ISO 8601 timestamp of first session creation |
| updated_at | TEXT | NOT NULL | ISO 8601 timestamp of last upsert |

### Design Decisions

- **Composite primary key** `(user_id, thread_id)` — one session per user per thread, matching FR-05 mapping
- **No auto-increment ID** — the natural key is sufficient and avoids an extra index
- **TEXT for timestamps** — SQLite has no native datetime; ISO 8601 strings sort correctly and are human-readable
- **model column in sessions table** — per FR-09, model selection is per-thread, stored alongside the session mapping (not a separate table)
- **No foreign keys** — single table, no referential integrity needed
- **No WAL mode** — single-writer bot process, default journal mode is fine for PoC

## Workspace Directory Structure

Per FR-06, each thread gets its own directory:

```
{WORKSPACE_BASE_PATH}/
  {user_id}/
    {thread_id}/                    # cwd for session/new
```

- `WORKSPACE_BASE_PATH` comes from Config (default `./workspaces/`)
- Directories created on-demand when first message arrives in a thread
- Path construction: `Path(config.workspace_base_path) / str(user_id) / str(thread_id)`
- The workspace_path stored in SQLite is the resolved absolute path
