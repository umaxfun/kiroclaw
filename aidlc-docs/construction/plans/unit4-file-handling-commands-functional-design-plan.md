# Functional Design Plan — Unit 4: File Handling + Commands

## Plan Steps

- [x] Step 1: Analyze Unit 4 context (unit-of-work.md, story map, component methods)
- [x] Step 2: Generate questions (if any ambiguities remain)
- [x] Step 3: Generate domain-entities.md
- [x] Step 4: Generate business-logic-model.md
- [x] Step 5: Generate business-rules.md
- [x] Step 6: Self-check against inception docs
- [x] Step 7: Present completion message

## Questions

After thorough analysis of Unit 4 scope against existing requirements and component design, the following areas need clarification:

### Q1: Inbound File Prompt Format
When a user sends a file (e.g., `report.txt`), how should the bot reference it in the ACP prompt?

- A) Include the file path as text: `[User sent file: /path/to/workspace/report.txt]`
- B) Include file path + file content inline (for text files only): `[File: report.txt]\n<content>...</content>`
- C) Just the file path, no special formatting — let the agent figure it out via tools
- D) Other (describe)

[Answer]:For the first question, our job for now is to save the file into the workspace path. Not into the workspace path but to the path with workspace user id thread id and then file. And let the agent read it using its own tools.

### Q2: Audio File Handling
For audio messages, should the bot attempt speech-to-text conversion before sending to the agent, or just download the audio file and reference it?

- A) Download only — reference the audio file path in the prompt, let the agent handle it
- B) Convert audio to text (using an external service or library) and send the transcription as text
- C) Both — download the file AND send a transcription if available
- D) Other (describe)

[Answer]:File handling should be generic, it's up to agent to decide what to do next with the file.

### Q3: Outbound File — Missing File Behavior
If the agent emits `<send_file path="...">` but the file doesn't exist at that path, what should the bot do?

- A) Skip silently — just strip the tag from the response text
- B) Send an inline note to the user: "⚠️ File not found: filename.txt"
- C) Log a warning and skip — no user-visible message
- D) Other (describe)

[Answer]: If the agent emits send file, but the file is not found, we should internally prompt the agent. Like, the file is not there and let the agent fix something.

### Q4: /model Command — Thread Context
The /model command sets the model for a thread. Should it also call `session/set_model` immediately on the current session, or just store it in SQLite for the next prompt?

- A) Store in SQLite only — applied on next session/load or session/new
- B) Store in SQLite AND call session/set_model immediately (requires acquiring the ACP client)
- C) Other (describe)

[Answer]: Storing SQLite and call setModel immediately.

### Q5: Inbound File Types — Size Limits
Should the bot enforce any file size limits when downloading files from Telegram to the workspace?

- A) No limits — download whatever Telegram allows (up to 20MB for bots)
- B) Set a configurable limit (e.g., MAX_FILE_SIZE_MB in .env)
- C) Limit by file type (e.g., audio up to 10MB, documents up to 20MB)
- D) Other (describe)

[Answer]: There should be no limits, I believe. And it is not 20MB for bots, I believe, it's much bigger. So let's Telegram decide. Everything that fits into Telegram works for us.
