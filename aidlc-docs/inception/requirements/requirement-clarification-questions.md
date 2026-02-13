# Requirements Clarification Questions

I detected one ambiguity in your responses that needs clarification.

---

## Ambiguity 1: Forum Topics vs Private Chats (Q5)

You said: "For the threaded bots you cannot post outside of the thread, everything is the thread."

This is correct — when a bot has forum topics enabled, all messages live in threads. But this creates a design choice:

### Clarification Question 1
Should the bot require forum topic mode (threads), or should it work in regular private chats (no threads)?

The distinction matters because `sendMessageDraft` (streaming) works in both modes as of Bot API 9.3, but the session mapping changes:
- With threads: `(user_id, thread_id) → kiro_session_id` — user can have multiple sessions via different threads
- Without threads: `(user_id) → kiro_session_id` — one session per user in private chat

A) Forum topics mode — bot requires topics enabled, each thread = separate Kiro session (as in FINDINGS.md)
B) Private chats only — no threads, one session per user, simpler mapping
C) Support both — detect whether chat has topics and adapt mapping accordingly
D) Other (please describe after [Answer]: tag below)

[Answer]: Yes, our bot is working only in forum topic mode.

---

## Ambiguity 2: Content Types Scope (Q3)

You said: "See from the start, because we will work with files for sure."

### Clarification Question 2
When you say "work with files," do you mean:

A) Users send files/documents via Telegram, and the bot forwards them to Kiro CLI (e.g., user uploads a .py file for review)
B) Kiro CLI generates files in the workspace, and the bot sends them back to the user in Telegram
C) Both directions — users can upload files AND bot can send generated files back
D) Other (please describe after [Answer]: tag below)

[Answer]: User can send files via telegram. Think about the case when I'm sending an audio file and bot responds with a .txt file back.

---
