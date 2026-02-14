# Code Generation Plan — Unit 2: Session Persistence

## Unit Context

- **Components**: C3 Session Store
- **Dependencies**: Unit 1 (C7 Config, C8 Provisioner, C1 ACP Client)
- **Delivers**: Session continuity across runs via SQLite, workspace directory creation
- **Requirements**: FR-05, FR-06
- **Project Type**: Greenfield monolith — `src/tg_acp/` package
- **Workspace Root**: /Users/umaxfun/prj/temp/tg-acp

## Stories Covered

- FR-05 (Session Management): SQLite mapping `(user_id, thread_id) → session_id`, session/load for returning threads
- FR-06 (Working Directory Management): `./workspaces/{uid}/{tid}/` created on-demand

## Plan Steps

### Step 1: Session Store Module (C3)
- [x] Create `src/tg_acp/session_store.py`

### Step 2: Update main.py (Unit 2 CLI)
- [x] Update `main.py` — argparse, session/load branching, 2-run demo

### Step 3: Session Store Tests
- [x] Create `tests/test_session_store.py` — 10 tests

### Step 4: Session Continuity Integration Test
- [x] Create `tests/test_session_continuity.py` — 2 tests (remembers_number, load_after_prompt)

### Step 5: Run Tests and Verify
- [x] `uv run pytest tests/test_session_store.py -v` — 10/10 passed
- [x] `uv run pytest tests/test_session_continuity.py -v` — 2/2 passed
- [x] `uv run pytest tests/ -v` — 38/38 passed (full suite, Unit 1 regression OK)

### Step 6: Code Summary Documentation
- [x] Create `aidlc-docs/construction/unit2-session-persistence/code/code-summary.md`

## Total: 6 Steps — ALL COMPLETE
