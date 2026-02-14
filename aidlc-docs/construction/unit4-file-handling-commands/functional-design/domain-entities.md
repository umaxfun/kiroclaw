# Domain Entities — Unit 4: File Handling + Commands

## C5: File Handler

```python
class FileHandler:
    """Downloads inbound files to workspace, sends outbound files to Telegram."""

    async def download_to_workspace(message: Message, workspace_path: str) -> str
        # Download file attached to a Telegram message into the workspace directory.
        # Supports: document, photo, audio, voice, video, video_note, sticker.
        # Returns the local file path (absolute).

    async def send_file(bot: Bot, chat_id: int, thread_id: int, file_path: str, caption: str | None) -> None
        # Send a file from the workspace to the Telegram thread via sendDocument.
        # caption is the description from the <send_file> tag (may be None).

    def validate_path(file_path: str, workspace_path: str) -> bool
        # Ensure file_path resolves to a location within workspace_path.
        # Prevents path traversal attacks (e.g., ../../etc/passwd).
```

### File Download Flow

```
Telegram message with attachment
    |
    v
Determine file_id from message (document, photo, audio, voice, etc.)
    |
    v
bot.get_file(file_id) -> File object with file_path
    |
    v
bot.download_file(file_path) -> bytes
    |
    v
Write to: {workspace_path}/{original_filename}
    |
    v
Return absolute path to saved file
```

File naming:
- Documents: use `message.document.file_name` (original filename from sender)
- Photos: `photo_{file_unique_id}.jpg`
- Audio: `message.audio.file_name` or `audio_{file_unique_id}.mp3`
- Voice: `voice_{file_unique_id}.ogg`
- Video: `message.video.file_name` or `video_{file_unique_id}.mp4`
- Video note: `videonote_{file_unique_id}.mp4`
- Sticker: `sticker_{file_unique_id}.webp`

If a file with the same name already exists, overwrite it (simplest approach — the workspace is per-thread, collisions are rare).

## Outbound File Tags

The agent emits XML tags in its response text to request file delivery:

```xml
<send_file path="/absolute/path/to/file.txt">Optional description for the user</send_file>
```

Parsing rules:
- Regex: `<send_file\s+path="([^"]+)">(.*?)</send_file>` (non-greedy, dotall for multiline descriptions)
- Multiple `<send_file>` tags may appear in a single response
- Tags are stripped from the displayed text before sending to the user
- The `path` attribute is an absolute path within the workspace
- The description becomes the `caption` on the Telegram document

## Outbound File — Missing File Retry

If a `<send_file>` tag references a file that doesn't exist:

```
1. Validate path (must be within workspace boundary)
2. Check file exists at path
3. If NOT exists:
   a. Send an internal follow-up prompt to the agent:
      "The file at {path} was not found. Please check the path and try again."
   b. Stream the agent's response (may contain corrected <send_file> tags)
   c. Parse again for <send_file> tags
   d. Retry at most once (to avoid infinite loops)
4. If exists: send via sendDocument
```

## /model Command Entity

Hardcoded model list (from FR-09):

```python
AVAILABLE_MODELS = [
    "auto",
    "claude-opus-4.6",
    "claude-opus-4.5",
    "claude-sonnet-4.5",
    "claude-sonnet-4",
    "claude-haiku-4.5",
]
DEFAULT_MODEL = "auto"
```

Command behavior:
- `/model` (no args) → display list with current selection marked
- `/model <name>` → validate against AVAILABLE_MODELS, store in SQLite, call session/set_model

## Prompt Content Types

Unit 4 extends the prompt content from text-only to mixed:

```python
# Unit 3 (text only):
content = [{"type": "text", "text": message.text}]

# Unit 4 (text + file reference):
content = [
    {"type": "text", "text": "User sent a file: /path/to/workspace/file.txt"},
    {"type": "text", "text": message.text or message.caption or ""},
]
```

When a message has both a file and text/caption, both are included. When a message has only a file (no text/caption), only the file reference is sent.
