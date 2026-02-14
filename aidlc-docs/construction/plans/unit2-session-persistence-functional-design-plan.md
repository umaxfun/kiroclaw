# Functional Design Plan — Unit 2: Session Persistence

## Unit Context

- **Components**: C3 Session Store
- **Dependencies**: Unit 1 (C7 Config, C8 Provisioner, C1 ACP Client)
- **Delivers**: Session continuity across runs via SQLite, workspace directory creation
- **Requirements**: FR-05 (Session Management), FR-06 (Working Directory Management)

## Intelligent Assessment

Unit 2 is a focused, low-ambiguity unit:
- C3 Session Store is a thin SQLite wrapper with 4 methods (get, upsert, set_model, get_model)
- Schema is simple: one table mapping (user_id, thread_id) → (session_id, workspace_path, model)
- Workspace directory creation is a `mkdir -p` with a known path pattern
- All method signatures are defined in component-methods.md
- No external integrations beyond SQLite (stdlib)

**Questions needed**: 0 — all design decisions are resolved by existing requirements and component design.

## Plan Steps

### Step 1: Define Domain Entities
- [x] SessionRecord dataclass (user_id, thread_id, session_id, workspace_path, model)
- [x] SQLite schema (table name, columns, types, constraints, indexes)

### Step 2: Define Business Logic Model
- [x] SessionStore.__init__() — open/create DB, ensure schema
- [x] SessionStore.get_session() — lookup by (user_id, thread_id)
- [x] SessionStore.upsert_session() — insert or replace mapping
- [x] SessionStore.set_model() — update model for a thread
- [x] SessionStore.get_model() — get model with default "auto"
- [x] Workspace directory creation logic (in main.py, not SessionStore)
- [x] Updated main.py flow: CLI with user_id/thread_id args, session/load branch

### Step 3: Define Business Rules
- [x] BR-09: Session Store rules (schema, constraints, defaults)
- [x] BR-10: Workspace directory rules (path construction, creation)
- [x] Test strategy for Unit 2

### Step 4: Self-Check
- [x] Cross-reference against component-methods.md, requirements.md, unit-of-work.md
- [x] Verify consistency with Unit 1 artifacts

## Total: 4 Steps
