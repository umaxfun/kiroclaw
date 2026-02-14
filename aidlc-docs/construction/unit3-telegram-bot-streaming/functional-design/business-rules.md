# Business Rules — Unit 3: Telegram Bot with Streaming

## BR-11: Stream Writer Rules

| # | Rule | Rationale |
|---|------|-----------|
| 1 | draft_id is a random positive int, constant for the lifetime of one StreamWriter | Telegram uses draft_id to identify which draft to update |
| 2 | sendMessageDraft throttled to at most 1 call per 100ms | Avoid Telegram rate limits; chunks arrive faster than needed for visual updates |
| 3 | Sliding window size = 4000 chars | Below Telegram's 4096 limit with margin for the "…\n" prefix |
| 4 | Sliding window prefixed with "…\n" when buffer exceeds window | Signals truncation to the user |
| 5 | On finalize, check buffer non-empty first, then send draft "…", then sendMessage; draft clears automatically | Avoids orphaned "…" draft when buffer is empty. Telegram clears the draft after sendMessage delivers |
| 6 | Message split: prefer newline break within last 200 chars of 4096 boundary | Clean visual breaks in multi-message responses |
| 7 | Message split: hard break at 4096 if no newline found | Guarantee no message exceeds Telegram limit |
| 8 | If cancelled, write_chunk and finalize are no-ops | Prevent stale data from reaching Telegram after cancel |
| 9 | cancel() only sets the flag — no draft "…" sent | Without a subsequent sendMessage, a "…" draft would persist indefinitely. The partial draft is replaced by the next response's draft |
| 10 | finalize() returns empty list in Unit 3 | File path extraction deferred to Unit 4 |
| 11 | sendMessageDraft errors are logged but swallowed | Draft is cosmetic; final sendMessage is what matters. Rate limits or transient errors must not crash the prompt flow |
| 12 | If buffer is empty on finalize, skip sendMessage | Agent may produce only tool calls with no text output |

## BR-12: Bot Handler Rules

| # | Rule | Rationale |
|---|------|-----------|
| 1 | Ignore messages where message.text is None or message.from_user is None | Non-text messages and channel posts have no user/text to process |
| 2 | Ignore messages without message_thread_id | Bot operates exclusively in forum topic mode (FR-01) |
| 3 | New thread: create workspace dir before session/new | kiro-cli needs a valid cwd |
| 4 | Existing thread: session/load before prompting | Resume session continuity (FR-05) |
| 5 | asyncio.Lock gates access to the single ACP Client (use `async with`) | Prevents handler interleaving; guarantees lock release even on exception |
| 6 | /start handler responds in the same thread | Standard Telegram bot behavior |
| 7 | Error during prompt: send error message to user in thread, log exception | User should know something went wrong; error details go to logs not to user |
| 8 | If client is dead (process crashed), respawn and re-initialize before use | Crash recovery within the handler; user retries transparently |
| 9 | If session/load fails, create a new session and update the store | Handles deleted/corrupted session files in ~/.kiro/sessions/cli/. User loses history but isn't stuck |
| 10 | Graceful shutdown: kill ACP Client + close SessionStore on SIGINT/SIGTERM | Prevents orphaned kiro-cli-chat processes holding session lock files |

## BR-13: sendMessageDraft API Rules

| # | Rule | Rationale |
|---|------|-----------|
| 1 | sendMessageDraft requires Bot API 9.3+ | Feature not available in older API versions |
| 2 | draft_id must be a positive non-zero integer | Telegram API requirement |
| 3 | text must be 1-4096 chars (empty string is rejected) | API constraint; use "…" as minimum content |
| 4 | sendMessageDraft does not create a permanent message | Draft is ephemeral; sendMessage creates the permanent record |
| 5 | Draft is automatically cleared after sendMessage delivers | No explicit "clear draft" API needed |
| 6 | sendMessageDraft only works in private chats with forum topics enabled | Bot operates in private chat mode per FR-01 |

## Test Strategy

### Unit Tests (no network, no kiro-cli)

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | StreamWriter sliding window — short text | Buffer returned as-is when under 4000 chars |
| 2 | StreamWriter sliding window — long text | Tail 4000 chars returned with "…\n" prefix |
| 3 | StreamWriter message split — under limit | Single segment, no split |
| 4 | StreamWriter message split — over limit, newline break | Split at last newline before 4096 |
| 5 | StreamWriter message split — over limit, hard break | Split at exactly 4096 when no newline |
| 6 | StreamWriter cancel — write_chunk after cancel is no-op | Buffer unchanged after cancel |
| 7 | StreamWriter finalize after cancel returns empty | No messages sent |
| 8 | StreamWriter finalize with empty buffer — no sendMessage | Handles agent-only-tool-calls case |
| 9 | StreamWriter write_chunk — sendMessageDraft error swallowed | Draft failure doesn't crash flow |
| 10 | StreamWriter finalize with empty buffer — no draft "…" sent | No orphaned draft |

### Integration Tests (real kiro-cli + real Telegram test bot)

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | Send text in forum topic, verify draft updates appear | sendMessageDraft streaming works |
| 2 | Send text, verify final sendMessage appears | Finalize flow works |
| 3 | Send two messages in same thread, verify session/load on second | Session continuity via bot |
| 4 | Trigger long response (>4096 chars), verify multi-message split | Split logic works end-to-end |
| 5 | /start command, verify welcome message | Command handler works |
