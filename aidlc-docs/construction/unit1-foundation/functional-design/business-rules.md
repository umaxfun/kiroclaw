# Business Rules — Unit 1: Foundation + ACP Echo

## BR-01: Config Validation

| Rule | Description |
|------|-------------|
| BR-01.1 | BOT_TOKEN must be non-empty string |
| BR-01.2 | KIRO_AGENT_NAME must be non-empty, >= 3 chars |
| BR-01.3 | KIRO_AGENT_NAME must match `^[a-zA-Z0-9_-]+$` |
| BR-01.4 | MAX_PROCESSES must be positive integer (>= 1) |
| BR-01.5 | IDLE_TIMEOUT_SECONDS must be non-negative integer (>= 0) |
| BR-01.6 | LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR |
| BR-01.7 | Config is immutable after load — no runtime changes |

## BR-02: Startup Prerequisites

| Rule | Description |
|------|-------------|
| BR-02.1 | kiro-cli must be on PATH (shutil.which) |
| BR-02.2 | KIRO_CONFIG_PATH directory must exist |
| BR-02.3 | Template must contain `agents/{KIRO_AGENT_NAME}.json` |
| BR-02.4 | WORKSPACE_BASE_PATH must be writable (create if needed) |
| BR-02.5 | All prerequisite failures are fatal — bot does not start |

## BR-03: Provisioner Safety

| Rule | Description |
|------|-------------|
| BR-03.1 | Only files matching `{KIRO_AGENT_NAME}*` prefix are deleted |
| BR-03.2 | Files without the prefix are never modified or deleted |
| BR-03.3 | If > 20 files match prefix across all dirs, abort with error |
| BR-03.4 | Template must contain agent JSON — refuse to sync empty template |
| BR-03.5 | After sync, verify agent JSON exists at target — fail if missing |
| BR-03.6 | Target directories created if they don't exist (mkdir -p) |

## BR-04: Provisioner Sync Behavior

| Rule | Description |
|------|-------------|
| BR-04.1 | Sync runs on every startup, not just first run |
| BR-04.2 | Sync is delete-then-copy, not merge |
| BR-04.3 | Sync covers 3 directories: agents/, steering/, skills/ |
| BR-04.4 | Directory entries (not just files) are matched and synced |
| BR-04.5 | Prefix match is case-sensitive |

## BR-05: ACP Client Protocol

| Rule | Description |
|------|-------------|
| BR-05.1 | JSON-RPC request IDs are monotonically increasing per client |
| BR-05.2 | Responses are matched to requests by ID |
| BR-05.3 | Notifications have no ID field |
| BR-05.4 | session/cancel is a notification (no response expected) |
| BR-05.5 | All other methods are requests (response expected) |
| BR-05.6 | One line = one JSON message on both stdin and stdout |
| BR-05.7 | Messages are newline-delimited (`\n` after each JSON object) |

## BR-06: ACP Client State

| Rule | Description |
|------|-------------|
| BR-06.1 | Client must be initialized before any session command |
| BR-06.2 | Only one prompt can be in-flight at a time (state = BUSY) |
| BR-06.3 | session/cancel can be sent in any state (it's a notification) |
| BR-06.4 | Process death transitions to DEAD from any state |
| BR-06.5 | DEAD state is terminal — client cannot be reused |
| BR-06.6 | Kill waits up to 5 seconds, then force-kills |

## BR-07: ACP Streaming

| Rule | Description |
|------|-------------|
| BR-07.1 | session/update notifications arrive between prompt request and response |
| BR-07.2 | agent_message_chunk contains incremental text (not cumulative) |
| BR-07.3 | turn_end signals the prompt response is complete |
| BR-07.4 | Other update types (tool_call, tool_call_update, plan) are logged but not acted on in Unit 1 |
| BR-07.5 | Subagent notifications (_session/terminate) are logged, no action |

## BR-08: stderr Handling

| Rule | Description |
|------|-------------|
| BR-08.1 | stderr is always captured (never piped to /dev/null) |
| BR-08.2 | stderr lines are logged at the configured LOG_LEVEL |
| BR-08.3 | stderr EOF does not imply process death (check returncode) |

## Test Strategy — Unit 1

### Integration Tests (real kiro-cli)

| Test | What it verifies |
|------|-----------------|
| test_acp_full_flow | initialize → session/new → prompt → streaming chunks → turn_end |
| test_acp_session_new_returns_id | session/new returns a valid session_id string |
| test_acp_streaming_chunks | At least 1 agent_message_chunk received before turn_end |
| test_acp_process_kill | client.kill() terminates the subprocess cleanly |
| test_acp_dead_detection | After kill, is_alive() returns False, state is DEAD |

### Unit Tests (pure logic, no kiro-cli)

| Test | What it verifies |
|------|-----------------|
| test_config_load_valid | Valid .env loads all fields correctly |
| test_config_missing_required | Missing BOT_TOKEN or KIRO_AGENT_NAME raises error |
| test_config_invalid_agent_name | Agent name with wildcards/dots/slashes rejected |
| test_config_agent_name_too_short | Agent name < 3 chars rejected |
| test_provisioner_safety_limit | > 20 matching files triggers abort |
| test_provisioner_empty_template | Empty template directory triggers abort |
| test_provisioner_sync | Files matching prefix deleted and replaced |
| test_provisioner_no_collateral | Files without prefix are untouched after sync |
| test_json_rpc_request_format | Request has jsonrpc, id, method, params |
| test_json_rpc_notification_format | Notification has jsonrpc, method, params, no id |
