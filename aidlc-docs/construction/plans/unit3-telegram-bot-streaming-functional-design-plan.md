# Functional Design Plan — Unit 3: Telegram Bot with Streaming

## Unit Context
- **Unit**: 3 — Telegram Bot with Streaming
- **Components**: C6 Bot Handlers (partial), C4 Stream Writer
- **Requirements**: FR-01 (bot core), FR-03 (streaming), FR-09 (/start only)
- **Dependencies**: C1 ACP Client (Unit 1), C3 Session Store (Unit 2), C7 Config (Unit 1)

## Plan Steps

- [x] Step 1: Define domain entities — StreamWriter state, draft lifecycle, message split model
- [x] Step 2: Define business logic model — StreamWriter chunk accumulation, sliding window, finalize flow; Bot Handlers orchestration (text message handler, /start command); entry point rewrite (aiogram dispatcher)
- [x] Step 3: Define business rules — draft update throttling, sliding window boundaries, message split rules, session lookup flow, error handling
- [x] Step 4: Self-check — verify all methods match component-methods.md, all FR coverage, no inconsistencies

## Self-Check Results

- C4 methods: write_chunk, finalize, cancel — all match component-methods.md ✓
- C6 handlers: cmd_start, handle_message — match component-methods.md (partial scope for Unit 3) ✓
- FR-01 (forum topic mode, thread→session): covered in BR-12 rules 1-3 ✓
- FR-03 (streaming, sliding window, multi-message split): covered in BR-11 rules 1-10 ✓
- FR-09 (/start command): covered in business-logic-model.md ✓
- No file handling (Unit 4), no process pool (Unit 5), no /model command (Unit 4) — correctly excluded ✓
- No inconsistencies found

## Questions

No questions needed. All design decisions are resolved by:
- FR-01, FR-03, FR-09 requirements
- C4 and C6 method signatures in component-methods.md
- sendMessageDraft API behavior documented in FINDINGS.md
- Unit 3 scope explicitly defined in unit-of-work.md (no file handling, no process pool, single kiro-cli process)
