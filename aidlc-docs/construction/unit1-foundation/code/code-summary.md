# Code Summary — Unit 1: Foundation + ACP Echo

## Files Created

| File | Component | Description |
|------|-----------|-------------|
| `src/tg_acp/__init__.py` | — | Package init |
| `src/tg_acp/config.py` | C7 | Config dataclass, .env loading, validation |
| `src/tg_acp/provisioner.py` | C8 | Prefix-based sync of ~/.kiro/ from kiro-config/ template |
| `src/tg_acp/acp_client.py` | C1 | JSON-RPC 2.0 client for kiro-cli acp subprocess |
| `kiro-config/agents/tg-acp.json` | — | Agent config template with send_file steering |
| `kiro-config/steering/.gitkeep` | — | Placeholder for future steering files |
| `kiro-config/skills/.gitkeep` | — | Placeholder for future skill files |
| `.env.example` | — | Documented config template (7 keys) |
| `main.py` | — | Throwaway CLI entry point (replaced in Unit 3) |
| `tests/test_config.py` | — | 11 unit tests for Config validation (BR-01) |
| `tests/test_provisioner.py` | — | 10 unit tests for Provisioner safety/sync (BR-03, BR-04) |
| `tests/test_acp_protocol.py` | — | 5 integration tests with real kiro-cli (BR-05 through BR-08) |

## Files Modified

| File | Change |
|------|--------|
| `pyproject.toml` | Added python-dotenv, pytest, pytest-asyncio, pytest-timeout deps; hatchling build config |
| `.gitignore` | Added .env, workspaces/, tg-acp.db, __pycache__/, .venv/ |
| `FINDINGS.md` | Corrected: `prompt` not `content` in session/prompt params; snake_case update types |

## Dependencies Added

- `python-dotenv>=1.0.0` — .env file loading
- `pytest>=8.0.0` (dev) — test runner
- `pytest-asyncio>=0.24.0` (dev) — async test support
- `pytest-timeout>=2.3.0` (dev) — test timeouts

## How to Run

```bash
# Install dependencies
uv sync

# Set up .env
cp .env.example .env
# Edit .env — set BOT_TOKEN (can be dummy for Unit 1 CLI)

# Run Unit 1 CLI
uv run python main.py "Hello, what can you do?"

# Run all tests
uv run pytest -v

# Run only unit tests (fast, no kiro-cli needed)
uv run pytest tests/test_config.py tests/test_provisioner.py -v

# Run integration tests (requires kiro-cli on PATH)
uv run pytest tests/test_acp_protocol.py -v -s --log-cli-level=INFO
```

## Discoveries During Implementation

1. kiro-cli `session/prompt` uses `prompt` field, not `content` (FINDINGS.md was incorrect)
2. Session update types are snake_case (`agent_message_chunk`, `tool_call`, `turn_end`), not PascalCase
3. Non-session/update notifications (MCP server init, metadata, commands) arrive on stdout and must be filtered
