# Business Logic Model — Unit 4: File Handling + Commands

## C5: FileHandler

### download_to_workspace(message: Message, workspace_path: str) -> str

```
1. Determine attachment type and extract file_id + filename:
   - message.document → file_id = document.file_id, name = document.file_name or "document_{unique_id}"
   - message.photo → file_id = photo[-1].file_id (largest), name = "photo_{unique_id}.jpg"
   - message.audio → file_id = audio.file_id, name = audio.file_name or "audio_{unique_id}.mp3"
   - message.voice → file_id = voice.file_id, name = "voice_{unique_id}.ogg"
   - message.video → file_id = video.file_id, name = video.file_name or "video_{unique_id}.mp4"
   - message.video_note → file_id = video_note.file_id, name = "videonote_{unique_id}.mp4"
   - message.sticker → file_id = sticker.file_id, name = "sticker_{unique_id}.webp"
   - None of the above → raise ValueError("No downloadable attachment")

2. destination = Path(workspace_path) / filename

3. bot = message.bot  # aiogram provides bot instance via message
   await bot.download(file_id, destination=destination)

4. Return str(destination.resolve())
```

### send_file(bot: Bot, chat_id: int, thread_id: int, file_path: str, caption: str | None) -> None

```
1. Open file_path as FSInputFile
2. await bot.send_document(
       chat_id=chat_id,
       document=FSInputFile(file_path),
       caption=caption,
       message_thread_id=thread_id,
   )
```

### validate_path(file_path: str, workspace_path: str) -> bool

```
1. resolved = Path(file_path).resolve()
2. workspace_resolved = Path(workspace_path).resolve()
3. Return resolved.is_relative_to(workspace_resolved)
```

This prevents path traversal: `../../etc/passwd` would resolve outside the workspace and be rejected.

---

## C4: StreamWriter Extensions

### finalize() -> list[tuple[str, str]]  (UPDATED from Unit 3)

```
1. If _cancelled: return []
2. If _buffer is empty: return []
3. Send draft "…" to signal completion (best-effort, swallow errors)

4. Parse <send_file> tags from _buffer BEFORE Markdown conversion:
   a. Find all matches of: <send_file\s+path="([^"]+)">(.*?)</send_file>
   b. Collect list of (path, description) tuples
   c. Strip all <send_file> tags from _buffer (replace with empty string)
   d. Strip leading/trailing whitespace from cleaned buffer

5. If cleaned buffer is non-empty:
   a. Convert to HTML via chatgpt-md-converter
   b. Split with tag-aware splitter
   c. Send segments via sendMessage (same as Unit 3)
   If cleaned buffer is empty (agent response was only <send_file> tags):
   a. Skip sendMessage entirely

6. Return list of (path, description) tuples
```

Key change: `<send_file>` tags are parsed and stripped BEFORE Markdown→HTML conversion. This prevents the converter from mangling the XML tags. The return type changes from `list[str]` to `list[tuple[str, str]]` — each tuple is (file_path, description).

---

## C6: Bot Handlers Extensions

### /model command handler

```
async def cmd_model(message: Message):
    0. Guard: if message.from_user is None or message.message_thread_id is None: return
       Extract user_id = message.from_user.id, thread_id = message.message_thread_id

    1. Extract args: text after "/model " (strip whitespace)

    2. If no args (just "/model"):
       a. Get current model: store.get_model(user_id, thread_id)
       b. Format model list with current selection marked:
          "Available models:\n"
          For each model in AVAILABLE_MODELS:
            If model == current: "  ✓ {model}"
            Else: "  • {model}"
          "\nUse /model <name> to change."
       c. Send to thread

    3. If args present:
       a. model_name = args.lower().strip()
       b. If model_name not in AVAILABLE_MODELS:
            Send: "Unknown model: {model_name}\nAvailable: {', '.join(AVAILABLE_MODELS)}"
            Return
       c. store.set_model(user_id, thread_id, model_name)
       d. Acquire client lock:
          - Lookup session: store.get_session(user_id, thread_id)
          - If session exists:
              Try: await client.session_load(session_id, cwd=workspace_path)
              Then: await client.session_set_model(session_id, model_name)
              Except: log warning (model will apply on next session/load)
          - If no session: model stored in SQLite, will apply when session is created
       e. Send: "Model set to {model_name} for this thread."
```

### handle_message (UPDATED from Unit 3)

```
async def handle_message(message: Message):
    1. Guard: if message.from_user is None, return
       Extract user_id, thread_id (must not be None)

    2. Determine if message has text, file attachment, or both:
       has_file = any of: message.document, message.photo, message.audio,
                          message.voice, message.video, message.video_note, message.sticker
       has_text = message.text is not None or message.caption is not None
       text_content = message.text or message.caption or ""

       If not has_file and not has_text: return  # nothing to process

    3. async with client_lock:

        4. Respawn client if dead (same as Unit 3)

        5. Session lookup / create (same as Unit 3)
           workspace_path is available after this step

        6. If has_file:
             file_path = await FileHandler.download_to_workspace(message, workspace_path)
             Build prompt content:
               content = [{"type": "text", "text": f"User sent a file: {file_path}"}]
               If text_content:
                 content.append({"type": "text", "text": text_content})
           Else:
             content = [{"type": "text", "text": text_content}]

        7. Create StreamWriter

        8. Stream response (same loop as Unit 3)

        9. On TURN_END:
             file_results = await writer.finalize()
             # file_results is list of (path, description) tuples

        10. Process outbound files:
            missing_files = []
            for (path, description) in file_results:
                if not FileHandler.validate_path(path, workspace_path):
                    logger.warning("Path traversal blocked: %s", path)
                    continue
                if not Path(path).exists():
                    missing_files.append((path, description))
                    continue
                Try: await FileHandler.send_file(bot, chat_id, thread_id, path, description)
                Except: log error, continue (don't crash on one file failure)

        11. If missing_files and retry not yet attempted:
            # Internal retry: ask agent to fix missing files
            # Note: still inside client_lock, client was alive for the first prompt.
            # If client died during first prompt, we'd have hit the except block — no retry.
            retry_prompt = "The following files were not found:\n"
            for (path, desc) in missing_files:
                retry_prompt += f"- {path}\n"
            retry_prompt += "Please check the paths and try again."

            writer2 = StreamWriter(bot, chat_id, thread_id)
            async for update in client.session_prompt(session_id, [{"type": "text", "text": retry_prompt}]):
                if update_type == "agent_message_chunk" with text:
                    await writer2.write_chunk(content["text"])
                elif update_type == TURN_END:
                    retry_file_results = await writer2.finalize()
                    # Send any files from retry (no further retries)
                    for (path, description) in retry_file_results:
                        if FileHandler.validate_path(path, workspace_path) and Path(path).exists():
                            Try: await FileHandler.send_file(bot, chat_id, thread_id, path, description)
                    break
```

### Message type routing

Unit 3 only handled `message.text is not None`. Unit 4 extends to handle file attachments:

```
@router.message(CommandStart())       → cmd_start (unchanged)
@router.message(Command("model"))     → cmd_model (NEW)
@router.message()                     → handle_message (EXTENDED — now handles text + files)
```

The catch-all `@router.message()` handler now processes both text messages and file messages. The guard changes from `if message.text is None` to checking for either text or file attachment.

---

## Entry Point Changes (main.py)

Minimal changes from Unit 3:

```
1. Import Command filter for /model
2. Register cmd_model handler on router with Command("model") filter
3. No other entry point changes — FileHandler is stateless, instantiated per-use
```
