# Unit of Work Dependencies

## Dependency Matrix

| Unit | Depends On | New Components | Extends |
|------|-----------|----------------|---------|
| 1: Foundation + ACP Echo | (none) | C7, C8, C1 | — |
| 2: Session Persistence | Unit 1 | C3 | main.py |
| 3: Telegram Bot + Streaming | Unit 1, Unit 2 | C4, C6 (partial) | main.py (rewrite) |
| 4: File Handling + Commands | Unit 3 | C5 | C4, C6 |
| 5: Process Pool + Cancel | Unit 4 | C2 | C6 |
| 6: Release Prep | Unit 5 | — | C7, C6 |

## Build Sequence

```
Unit 1: Foundation + ACP Echo
  |
  v
Unit 2: Session Persistence
  |
  v
Unit 3: Telegram Bot + Streaming
  |
  v
Unit 4: File Handling + Commands
  |
  v
Unit 5: Process Pool + Cancel
  |
  v
Unit 6: Release Prep
```

Strictly linear — each unit builds on the previous. No parallel units.

## Component Build Timeline

Shows when each component is first created and when it's extended:

```
         Unit 1    Unit 2    Unit 3    Unit 4    Unit 5    Unit 6
C7       CREATE                                            EXTEND
C8       CREATE
C1       CREATE
C3                 CREATE
C4                           CREATE    EXTEND
C6                           CREATE    EXTEND    EXTEND    EXTEND
C5                                     CREATE
C2                                               CREATE
```

## Integration Points Between Units

### Unit 1 → Unit 2
- main.py gains SessionStore dependency
- ACP Client flow gains session/load branch (in addition to session/new)
- Workspace directory creation before session/new

### Unit 2 → Unit 3
- Entry point rewritten from CLI script to aiogram bot
- Stdout printing replaced with StreamWriter (sendMessageDraft)
- Session lookup keyed by Telegram user_id + thread_id instead of hardcoded values
- ACP Client managed as a single long-lived instance (no pool yet)

### Unit 3 → Unit 4
- StreamWriter.finalize() extended to parse `<send_file>` tags
- Bot Handlers gains file/document/audio message handler
- Bot Handlers gains /model command handler
- FileHandler added for download/upload/path validation

### Unit 4 → Unit 5
- Direct ACP Client usage in Bot Handlers replaced with ProcessPool.acquire()/release()
- Bot Handlers gains in-flight tracking per thread and cancel logic
- ProcessPool manages ACP Client lifecycle internally

### Unit 5 → Unit 6
- C7 Config gains `allowed_telegram_ids` field parsed from `.env`
- C6 Bot Handlers gains allowlist middleware — first check before any processing
- C6 `/start` handler gains restricted-access variant for unauthorized users
- README.md created at workspace root

## Risk Notes

- **Unit 3 is the biggest jump**: CLI script → long-running bot. Entry point is rewritten. This is the riskiest transition.
- **Unit 5 changes concurrency model**: Single process → pool. Bot Handlers orchestration logic changes significantly. Thorough testing of race conditions needed.
- **Units 1-2 are throwaway entry points**: The CLI-based main.py from Units 1-2 is replaced in Unit 3. The components (C7, C8, C1, C3) carry forward unchanged.
- **Unit 6 is low-risk**: Small config addition + early-return guard in handlers. No architectural changes.
