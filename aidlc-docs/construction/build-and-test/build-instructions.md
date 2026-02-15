# Build Instructions

## Prerequisites
- Python 3.12 (pinned via `.python-version`)
- `uv` package manager (https://docs.astral.sh/uv/getting-started/installation/)
- `kiro-cli` on PATH (required at runtime, not build time)

## Environment Variables
Copy `.env.example` to `.env` and fill in:
- `BOT_TOKEN` — Telegram bot token (REQUIRED)
- `KIRO_AGENT_NAME` — custom agent name, >= 3 chars (REQUIRED, default: `tg-acp`)
- See `.env.example` for all optional values

## Install Dependencies

```bash
uv sync
```

## Verify Build

```bash
uv run python -c "from tg_acp import config, provisioner, acp_client, session_store, stream_writer, bot_handlers, file_handler, process_pool; print('All modules import OK')"
```

Expected: `All modules import OK`

## Run the Bot

```bash
uv run main.py
```

## Troubleshooting

### `ModuleNotFoundError: No module named 'tg_acp'`
Run `uv sync` — hatchling build backend resolves the `src/` layout.

### `RuntimeError: kiro-cli not found on PATH`
Install kiro-cli and ensure it's on your PATH.

### `RuntimeError: KIRO_AGENT_NAME is not set`
Create `.env` from `.env.example` and set `KIRO_AGENT_NAME`.
