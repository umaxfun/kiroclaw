# Business Logic Model — Unit 3: Telegram Bot with Streaming

## C4: StreamWriter

### write_chunk(text: str)

```
1. If _cancelled: return immediately (discard chunk)
2. Append text to _buffer
3. Compute draft_text = sliding_window(_buffer)
4. If time since _last_draft_time < DRAFT_THROTTLE_S: skip sendMessageDraft (throttle)
5. Try: bot.send_message_draft(chat_id, thread_id, draft_id, draft_text)
   Except TelegramRetryAfter: set _last_draft_time = now + retry_after (back off)
   Except other: log warning, continue (draft errors are non-fatal)
6. Update _last_draft_time
```

### sliding_window(buffer: str) -> str

```
WINDOW_SIZE = 4000  # chars — leave margin below Telegram's 4096 limit

If len(buffer) <= WINDOW_SIZE:
    return buffer
Else:
    return "…\n" + buffer[-WINDOW_SIZE:]
```

The "…\n" prefix signals to the user that earlier content is truncated in the draft. The full response is delivered on finalize.

### finalize() -> list[str]

```
1. If _cancelled: return []
2. If _buffer is empty: return [] (nothing to send, no draft to clear)
3. Send draft "…" to signal completion (best-effort, swallow errors)
4. Try: convert _buffer from Markdown to Telegram HTML using chatgpt-md-converter (telegram_format)
   Except: use raw _buffer as plain text, skip HTML splitting, send segments without parse_mode
5. Split HTML into segments of <=4096 chars using tag-aware splitter:
   - Split at newline boundaries (prefer) or hard break at 4096
   - At each split point, handle unclosed tags by type:
     a. INLINE tags (<b>, <i>, <code>, <u>, <s>, <a>):
        Backtrack to before the opening tag and split there.
        The tag moves intact to the next segment. Avoids mid-word formatting breaks.
     b. BLOCK tags (<pre>, <blockquote>):
        Close the tag at the split point, reopen at the start of the next segment.
        Block tags can span thousands of chars — backtracking is impractical.
   - This preserves formatting continuity across segments
6. For each segment:
   a. Try: bot.send_message(chat_id, text=segment, message_thread_id=thread_id, parse_mode="HTML")
   b. If Telegram rejects HTML (parse error): retry same segment as plain text (no parse_mode)
7. Return [] (no file paths — file handling is Unit 4)
```

The entire buffer is converted to HTML in one pass, then split with tag awareness.
This avoids the problem of splitting raw Markdown (which could break mid-formatting)
and the problem of splitting HTML naively (which could break mid-tag).
The tag-aware splitter tracks open tags across split boundaries, ensuring each segment
is valid HTML that Telegram can parse independently.
The draft is automatically cleared by Telegram after sendMessage delivers.

Note: In Unit 4, finalize() will parse `<send_file>` tags and return file paths. For Unit 3, it returns an empty list.

### cancel()

```
1. Set _cancelled = True
2. (No draft clear — without a subsequent sendMessage, the "…" draft would persist
   indefinitely. Better to leave the partial draft visible than show a misleading "…".
   The draft will be replaced by the next response's draft in the same thread.)
```

---

## C6: Bot Handlers (Partial — Unit 3 scope)

### /start command handler

```
async def cmd_start(message: Message):
    Send welcome text to the forum topic.
    Text: "I'm a Kiro-powered assistant. Send me a message in any forum topic and I'll respond."
```

### Text message handler

```
async def handle_message(message: Message):
    1. If message.text is None or message.from_user is None: return
       Extract user_id = message.from_user.id
       Extract thread_id = message.message_thread_id
       If thread_id is None: ignore (not a forum topic message)

    2. async with client_lock:   # guarantees release even if inner code raises

        3. If not client.is_alive():
             Respawn: client = await ACPClient.spawn(...); await client.initialize()

        4. Lookup session: store.get_session(user_id, thread_id)

        5. If no existing session:
             a. Create workspace dir: create_workspace_dir(config.workspace_base_path, user_id, thread_id)
             b. session_id = await client.session_new(cwd=workspace_path)
             c. store.upsert_session(user_id, thread_id, session_id, workspace_path)
           Else:
             a. session_id = record.session_id
             b. workspace_path = record.workspace_path
             c. Try: await client.session_load(session_id, cwd=workspace_path)
                Except RuntimeError:
                  # Session files missing/corrupted — create fresh session
                  Log warning: "session/load failed for {session_id}, creating new session"
                  session_id = await client.session_new(cwd=workspace_path)
                  store.upsert_session(user_id, thread_id, session_id, workspace_path)

        6. Create StreamWriter(bot, chat_id, thread_id, draft_id=random_draft_id())

        7. Try:
             async for update in client.session_prompt(session_id, [{type: "text", text: message.text}]):
               If agent_message_chunk with text content:
                   await writer.write_chunk(content["text"])
               If TURN_END:
                   await writer.finalize()
                   break
           Except Exception as e:
             Log error
             Send error message to user in thread (best-effort):
               "Something went wrong. Please try again."

    8. (No file handling in Unit 3)
```

### ACP Client lifecycle (Unit 3 — single process, no pool)

```
A single ACP Client is spawned at startup and shared across all requests.
An asyncio.Lock ensures one prompt at a time — handlers wait for the lock.

Each message targets a different (user_id, thread_id) → different session_id.
No session lock conflicts: the previous session is fully released (turn_end received)
before the next session/load. The single client just switches between sessions.

Unit 5 replaces this with ProcessPool for true concurrency.
```

### Known limitation: notification queue pollution (RESOLVED)

```
The ACP Client's _notification_queue accumulated ALL notifications from kiro-cli,
including session/update history replays from session/load. This was resolved with
a dual drain strategy:
  1. Drain at the end of session_load() — catches the bulk of stale notifications
  2. Drain at the start of session_prompt() — catches stragglers from the async reader

Between turns, kiro-cli may also send non-session notifications like
_kiro.dev/commands/available or _kiro.dev/compaction/status. These are harmless —
session_prompt only yields session/update notifications and ignores others.
```

---

## Entry Point (main.py rewrite)

```
main.py — rewritten from CLI demo to aiogram bot:

async def main():
    1. Config.load()
    2. Config.validate_kiro_cli()
    3. WorkspaceProvisioner(config).provision()
    4. store = SessionStore(db_path="./tg-acp.db")
    5. client = await ACPClient.spawn(config.kiro_agent_name)
    6. await client.initialize()
    7. client_lock = asyncio.Lock()
    8. bot = Bot(token=config.bot_token)
    9. dp = Dispatcher()
    10. Register handlers on aiogram Router:
          - /start → cmd_start
          - text messages → handle_message
          (handlers close over: client, client_lock, store, config)
    11. Register shutdown hook on dp:
          dp.shutdown.register(cleanup)
          cleanup(): await client.kill(); store.close()
    12. await dp.start_polling(bot)

asyncio.run(main())
```

### Graceful shutdown

```
On SIGINT/SIGTERM, aiogram's dp.start_polling stops and triggers shutdown hooks.
The cleanup hook:
  1. await client.kill()  — kills kiro-cli process group (prevents orphaned kiro-cli-chat)
  2. store.close()        — closes SQLite connection

Without this, every bot restart would leave orphaned kiro-cli processes holding
session lock files, causing session/load failures on the next startup.
```
