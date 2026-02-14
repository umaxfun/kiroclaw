# Domain Entities — Unit 3: Telegram Bot with Streaming

## StreamWriter State

```python
@dataclass
class StreamWriter:
    """Accumulates streaming chunks and delivers them to Telegram."""
    bot: Bot                    # aiogram Bot instance
    chat_id: int                # Telegram chat ID (private chat with bot)
    thread_id: int              # message_thread_id (forum topic)
    draft_id: int               # consistent draft_id for sendMessageDraft updates
    _buffer: str = ""           # accumulated full response text
    _last_draft_time: float = 0 # monotonic timestamp of last sendMessageDraft call
    _cancelled: bool = False    # True if cancel() was called
```

## Draft Lifecycle

```
[Created]  →  write_chunk()  →  [Drafting]  →  finalize()  →  [Finalized]
                                     |
                                  cancel()  →  [Cancelled]
```

- `draft_id`: a random positive integer, generated once per StreamWriter instance. Stays constant across all `sendMessageDraft` calls for one response.
- `sendMessageDraft` is called with the tail of the buffer (sliding window) as chunks arrive.
- On finalize: send draft with "…" to signal completion, then `sendMessage` with the full response. The draft is automatically cleared by Telegram after `sendMessage` delivers.
- On cancel: leave the partial draft visible (no "…" replacement — without a subsequent sendMessage, a "…" draft would persist indefinitely). The draft will be replaced by the next response's draft in the same thread.

## Message Split Model

When the full response exceeds 4096 characters, it is split into sequential messages:

```
Full response (e.g., 10000 chars)
    |
    +---> Message 1: chars [0..4095]      → sendMessage
    +---> Message 2: chars [4096..8191]   → sendMessage
    +---> Message 3: chars [8192..9999]   → sendMessage
```

Split rules:
- Max 4096 chars per message (Telegram limit)
- Split on last newline before the 4096 boundary when possible (clean break)
- If no newline in the last 200 chars, split at exactly 4096 (hard break)
- Each segment sent as a separate `sendMessage` call with `message_thread_id`
- If buffer is empty (agent produced no text), skip sendMessage entirely

## Bot Handler State

Bot Handlers (C6) in Unit 3 are stateless aiogram router handlers. Per-request state:
- Session lookup result from SessionStore
- ACP Client instance (single process, managed directly — no pool until Unit 5)
- StreamWriter instance (created per response)

No persistent handler state beyond what SessionStore provides.

## Concurrency Model (Unit 3)

A single ACP Client is shared across all requests. This is sequential — one prompt
at a time. An `asyncio.Lock` gates access to the client (via `async with`) so handlers
don't interleave and the lock is always released, even on exceptions.

This is safe because each message targets a different (user_id, thread_id) pair, which
maps to a different session_id. The single client handles session/load → prompt → turn_end
for one session, then moves to the next. No session lock conflicts occur because each
request uses its own session_id, and the previous session is released before the next
session/load.

If the kiro-cli process dies mid-stream, the handler detects it (client.is_alive() check),
respawns the client, and re-initializes before the next request. The current request
gets an error message; the next request works normally.

Unit 5 introduces the ProcessPool for true concurrency.
