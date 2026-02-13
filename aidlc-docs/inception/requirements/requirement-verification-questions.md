# Requirements Verification Questions

Based on the comprehensive FINDINGS.md, most architectural and technical decisions are already resolved. The following questions address remaining gaps needed before implementation.

---

## Question 1
What is the scope of this PoC build? Should we implement the full architecture from FINDINGS.md or start with a minimal working slice?

A) Minimal slice — single user, no process pool, no streaming (just send final message), get end-to-end working first
B) Core features — process pool, session management, streaming via sendMessageDraft, but single-user tested
C) Full PoC — everything in FINDINGS.md including folder-per-user workspaces, error recovery, idle timeouts
D) Other (please describe after [Answer]: tag below)

[Answer]: The scope is to implement the full architecture but I insist on the results being testable step by step. So before implementing process pool we have to make sure that the board works in one process and step by step going further.

---

## Question 2
How should the bot token and configuration be managed?

A) Environment variables only (BOT_TOKEN, WORKSPACE_BASE_PATH, etc.)
B) A .env file loaded with python-dotenv
C) A config.py / settings.py with defaults + env var overrides
D) Other (please describe after [Answer]: tag below)

[Answer]: It should be from .env file while developing and from somewhere when it would be deployed.

---

## Question 3
Should the bot handle only text messages, or also support other content types from Telegram?

A) Text messages only (sufficient for PoC)
B) Text + images (Kiro CLI can process images via ACP)
C) Text + images + documents/files
D) Other (please describe after [Answer]: tag below)

[Answer]: See from the start, because we will work with files for sure.

---

## Question 4
How should the bot handle concurrent messages from the same user while a previous prompt is still being processed?

A) Queue them — process sequentially, notify user their message is queued
B) Reject — reply "Please wait for the current response to finish"
C) Cancel previous — cancel the in-flight prompt and start the new one
D) Other (please describe after [Answer]: tag below)

[Answer]: I think it should cancel previous.

---

## Question 5
Should the bot support Telegram forum topics (threads), or just work in regular private chats?

A) Private chats only (simpler, sufficient for PoC)
B) Forum topics only (as implied by sendMessageDraft requirements)
C) Both private chats and forum topics
D) Other (please describe after [Answer]: tag below)

[Answer]: For the threaded bots you cannot post outside of the thread, everything is the thread.

---

## Question 6
What should happen when kiro-cli is not installed or not found on PATH?

A) Fail fast on startup with a clear error message
B) Gracefully degrade — bot starts but replies with "Backend unavailable" to user messages
C) Other (please describe after [Answer]: tag below)

[Answer]: It should fail fast.

---

## Question 7
Should the bot include any admin/management commands (e.g., /start, /reset to clear session, /status)?

A) Minimal — just /start (welcome message) and /reset (clear session)
B) Standard — /start, /reset, /status (show session info), /cancel (cancel in-flight prompt)
C) Full — above plus /model (switch model), /mode (switch agent mode)
D) Other (please describe after [Answer]: tag below)

[Answer]: Let's add one command like slash model.

---

## Question 8
Where should the workspace base directory be located?

A) /data/workspaces/ (as in FINDINGS.md)
B) Configurable via environment variable with a sensible default (e.g., ~/.tg-acp/workspaces/)
C) Relative to the bot's working directory (./workspaces/)
D) Other (please describe after [Answer]: tag below)

[Answer]: Let's keep it simple, C.

---
