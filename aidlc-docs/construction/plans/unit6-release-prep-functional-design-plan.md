# Unit 6: Release Prep — Functional Design Plan

## Scope
- FR-14: Telegram ID Allowlist
- FR-15: README Documentation

## Plan Steps

- [x] Define domain entities: Config extension (allowed_telegram_ids field)
- [x] Define business rules: allowlist check logic, fail-closed semantics, /start behavior for unauthorized users
- [x] Define business logic model: handler flow with allowlist gate, rejection message format
- [x] Define README structure and content outline

## Questions

No questions needed — scope is clear from user discussion:
- Comma-separated Telegram IDs in .env
- Fail-closed (empty list = nobody allowed)
- Unauthorized users get rejection message with their Telegram ID
- /start still works but notes restricted access
- README covers deployment from scratch
