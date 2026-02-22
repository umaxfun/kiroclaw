# Unit 6: Release Prep — Domain Entities

## Config Extension

### New Field: `allowed_telegram_ids`

| Field | Type | Source | Default | Required |
|-------|------|--------|---------|----------|
| `allowed_telegram_ids` | `frozenset[int]` | `ALLOWED_TELEGRAM_IDS` env var | `frozenset()` (empty = nobody allowed) | No (optional, fail-closed) |

### Parsing Rules
- Raw value: comma-separated string of integers (e.g., `"123456,789012,345678"`)
- Whitespace around commas and values is stripped
- Empty string or unset → `frozenset()` → nobody allowed (fail-closed)
- Non-integer values → `ValueError` at startup (fail fast)
- Duplicates are naturally deduplicated by `frozenset`
- Startup logs a warning if the allowlist is empty: `"ALLOWED_TELEGRAM_IDS is empty — all users will be denied"`

### Runtime Lookup
- O(1) membership check directly on `frozenset`
- Exposed via a method: `Config.is_user_allowed(user_id: int) -> bool` — returns `user_id in self.allowed_telegram_ids`
- No derived field needed — `frozenset` is the canonical store and is compatible with `frozen=True` dataclass


---

## SessionStore Extension

### New Method: `delete_session`

| Method | Params | Returns | Description |
|--------|--------|---------|-------------|
| `delete_session` | `user_id: int, thread_id: int` | `None` | Delete session record from SQLite. Used for stale lock recovery (BR-07). |
