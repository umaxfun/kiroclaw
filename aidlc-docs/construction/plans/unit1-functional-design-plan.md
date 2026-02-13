# Functional Design Plan — Unit 1: Foundation + ACP Echo

## Plan Steps

- [x] Define C7 Config business rules (validation logic, defaults, fail-fast behavior)
- [x] Define C8 Workspace Provisioner business rules (idempotent copy logic, template structure)
- [x] Define the `kiro-config/` template content (agent JSON, steering files)
- [x] Define C1 ACP Client protocol state machine (JSON-RPC flow, error states, streaming)
- [x] Define domain entities (Config, ACPClient state, JSON-RPC message types)
- [x] Define the main.py entry point flow (Unit 1 CLI version)
- [x] Define test strategy for Unit 1

## Questions

### Q1: Agent Config — `<send_file>` Tag Format

FR-08 says the agent's `prompt` field instructs Kiro to emit `<send_file path="..."/>` when it wants to send a file. Should the tag be:

A) Self-closing: `<send_file path="/absolute/path/to/file.py"/>`
B) With content: `<send_file path="/absolute/path/to/file.py">description</send_file>`
C) Multiple files in one tag: `<send_files><file path="..."/><file path="..."/></send_files>`
D) Other (describe)

[Answer]: I think that the file with description would work. I don't know why do we need the description, but it feels right to have it. B

### Q2: Agent Config — Allowed Tools

The agent JSON has `tools` and `allowedTools` fields. For the PoC bot agent, which tools should be allowed? The FINDINGS.md experiments showed the agent used the write tool without prompting when `allowedTools` was set.

A) All tools allowed (no restrictions) — simplest for PoC
B) Restricted set — only file read/write, terminal, web search
C) Minimal — only file read/write (safest)
D) Other (describe)

[Answer]: Let's go for all tools. A

### Q3: Agent Config — Default Model

FR-09 says default model is `auto`. Should the agent JSON also set `"model": "auto"`, or leave it unset (letting kiro-cli use its own default)?

A) Set `"model": "auto"` explicitly in agent JSON
B) Leave model unset in agent JSON — the bot controls model via `session/set_model` per-thread
C) Other

[Answer]: Yeah, let's set the module auto in the agent.json for now.

### Q4: ACP Client — stderr Handling

kiro-cli writes diagnostic output to stderr. How should the ACP Client handle it?

A) Capture stderr and log it at DEBUG level — useful for debugging but noisy
B) Capture stderr and log only on process crash — quieter, still useful for diagnostics
C) Pipe stderr to /dev/null — ignore it entirely
D) Other

[Answer]: I don't know. Let's maybe enable and disable logs or set the log level somewhere?

### Q5: Provisioner — Overwrite Behavior

C8 is idempotent — "only creates missing files, does not overwrite existing." But what if the `kiro-config/` template is updated (new version of the bot)? Should the provisioner:

A) Never overwrite — user must manually update `~/.kiro/` files. Safe but manual.
B) Overwrite if template is newer (compare mtime or hash). Automatic but could clobber user customizations.
C) Overwrite always — `~/.kiro/` is fully managed by the bot, user shouldn't edit it directly.
D) Other

[Answer]: No, we can't overwrite because it's a system-wide thing. So we can manage not the whole Kiro folder but one of them like ACP agent folder and stuff like that. And inside this folder we do can overwrite. Clarified: bot owns all files matching `{KIRO_AGENT_NAME}*` prefix in agents/, steering/, skills/. On every startup: delete all matching, copy fresh from template. Everything outside the prefix is untouched. Subdirectory agents don't work in kiro-cli (verified experimentally).

