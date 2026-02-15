# Integration Test Instructions

## Automated Integration Tests

7 integration tests run against real `kiro-cli`:

```bash
uv run pytest tests/test_acp_protocol.py tests/test_session_continuity.py -v
```

### test_acp_protocol.py (5 tests)
- `test_acp_full_flow` — initialize → session/new → prompt → streaming → turn_end
- `test_acp_session_new_returns_id` — session/new returns valid session_id
- `test_acp_streaming_chunks` — at least 1 agent_message_chunk before turn_end
- `test_acp_process_kill` — client.kill() terminates subprocess
- `test_acp_dead_detection` — after kill, is_alive() returns False

### test_session_continuity.py (2 tests)
- `test_session_remembers_number` — session/new + prompt "remember 1234", kill, session/load + prompt "what number?" → agent recalls
- `test_session_load_after_prompt` — session/new + prompt, kill, session/load succeeds without error

## Manual Integration Tests (Telegram Bot)

These require a running bot with a Telegram test forum:

### Streaming
1. Send a text message in a forum topic
2. Verify: draft animation appears, then final message replaces it

### Session Continuity
1. Send "Remember the number 42" in a topic
2. Send "What number did I ask you to remember?" in the same topic
3. Verify: agent recalls 42

### File Handling (Inbound)
1. Send a .txt file in a topic
2. Verify: agent acknowledges the file and can read its contents

### File Handling (Outbound)
1. Ask the agent to create a file (e.g., "write a hello.py file")
2. Verify: bot sends the file back via sendDocument

### /model Command
1. Send `/model` — verify model list displayed
2. Send `/model claude-sonnet-4` — verify confirmation
3. Send a message — verify response uses the selected model

### Cancel In-Flight
1. Send a long prompt (e.g., "write a 2000-word essay")
2. Immediately send another message in the same topic
3. Verify: first response stops, second response starts

### Process Pool
1. Send messages from multiple topics simultaneously
2. Verify: all get responses (pool spawns additional processes)

## Prerequisites
- `kiro-cli` on PATH
- `.env` configured with valid `BOT_TOKEN`
- Telegram bot with forum topics enabled
- `KIRO_AGENT_NAME` set and `kiro-config/` template present
