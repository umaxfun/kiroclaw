# Unit of Work — Requirements Map

User Stories stage was skipped. This document maps functional and non-functional requirements to units.

## Functional Requirements → Units

| Requirement | Description | Unit(s) |
|-------------|-------------|---------|
| FR-01 | Telegram Bot Core (forum topic mode, thread→session) | 3, 4, 5 |
| FR-02 | ACP Client (JSON-RPC 2.0, kiro-cli integration) | 1 |
| FR-03 | Streaming Responses (sendMessageDraft, sliding window, multi-message split) | 3 |
| FR-04 | Process Pool (scale-to-one, idle timeout, queue, dedup) | 5 |
| FR-05 | Session Management (SQLite mapping, session/load, session/new) | 2 |
| FR-06 | Working Directory Management (./workspaces/{uid}/{tid}/) | 2 |
| FR-07 | Concurrency Handling (cancel-in-flight) | 5 |
| FR-08 | File Handling (bidirectional, `<send_file>` tags) | 4 |
| FR-09 | Bot Commands (/start, /model) | 3 (/start), 4 (/model) |
| FR-10 | Error Recovery (crash → respawn → session/load) | 5 |
| FR-11 | Custom Agent Support (global ~/.kiro/agents/) | 1 |
| FR-12 | Subagent Support (agent config provisioning) | 1 |
| FR-13 | Skills Support (kiro-config/ template provisioning) | 1 |
| FR-14 | Telegram ID Allowlist (authorized access gate) | 6 |
| FR-15 | README Documentation (release artifact) | 6 |

## Non-Functional Requirements → Units

| Requirement | Description | Unit(s) |
|-------------|-------------|---------|
| NFR-01 | Configuration (.env, typed config values) | 1 |
| NFR-02 | Startup Validation (fail-fast checks) | 1 |
| NFR-03 | Testing Strategy (integration tests, no mocks) | All (per-unit tests) |
| NFR-04 | Tech Stack (Python 3.12, uv, aiogram) | 1 (base), 3 (aiogram) |
| NFR-05 | Logging (structured, stderr capture) | 1 (ACP logging), 3+ (bot logging) |

## Coverage Check

All 15 functional requirements and 5 non-functional requirements are assigned to at least one unit. No orphaned requirements.
