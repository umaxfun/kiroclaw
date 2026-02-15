# Unit Test Execution

## Run All Tests

```bash
uv run pytest -q
```

Expected: `84 passed` (as of Unit 5 completion)

## Run Tests by Module

```bash
# Unit 1: Config + Provisioner + ACP Protocol
uv run pytest tests/test_config.py tests/test_provisioner.py tests/test_acp_protocol.py -v

# Unit 2: Session Store + Session Continuity
uv run pytest tests/test_session_store.py tests/test_session_continuity.py -v

# Unit 3: Stream Writer + Bot Handlers
uv run pytest tests/test_stream_writer.py tests/test_bot_handlers.py -v

# Unit 5: Process Pool
uv run pytest tests/test_process_pool.py -v
```

## Test Results Summary

| Test File | Count | Type | Unit |
|-----------|-------|------|------|
| test_config.py | 11 | Unit (pure logic) | 1 |
| test_provisioner.py | 10 | Unit (real filesystem) | 1 |
| test_acp_protocol.py | 5 | Integration (real kiro-cli) | 1 |
| test_session_store.py | 10 | Unit (real SQLite) | 2 |
| test_session_continuity.py | 2 | Integration (real kiro-cli + SQLite) | 2 |
| test_stream_writer.py | 19 | Unit (mocked Telegram API) | 3+4 |
| test_bot_handlers.py | 17 | Unit (mocked pool + client) | 3+4+5 |
| test_process_pool.py | 2 | Unit (mocked slots) | 5 |
| **Total** | **76 unit + 7 integration = 84** | | |

## Notes

- `test_acp_protocol.py` and `test_session_continuity.py` require `kiro-cli` on PATH (real subprocess)
- All other tests use mocks/fakes and run without external dependencies
- Timeout: tests have a 60s default timeout via `pytest-timeout`
