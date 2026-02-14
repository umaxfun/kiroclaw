"""Unit 2 CLI entry point — throwaway, replaced by aiogram bot in Unit 3.

Loads config, provisions ~/.kiro/, opens SessionStore, spawns kiro-cli,
and runs a 2-run demo: first run memorizes a number, second run recalls it.

Usage:
    uv run main.py --user-id 42 --thread-id 7
    uv run main.py  # defaults: user_id=1, thread_id=1
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from tg_acp.acp_client import ACPClient, TURN_END
from tg_acp.config import Config
from tg_acp.provisioner import WorkspaceProvisioner
from tg_acp.session_store import SessionStore, create_workspace_dir


async def main() -> None:
    parser = argparse.ArgumentParser(description="tg-acp Unit 2 demo")
    parser.add_argument("--user-id", type=int, default=1, help="Fake Telegram user ID")
    parser.add_argument("--thread-id", type=int, default=1, help="Fake Telegram thread ID")
    args = parser.parse_args()

    config = Config.load()

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger = logging.getLogger("main")

    logger.info("Validating prerequisites...")
    config.validate_kiro_cli()

    logger.info("Provisioning ~/.kiro/ from %s...", config.kiro_config_path)
    WorkspaceProvisioner(config).provision()

    with SessionStore(db_path="./tg-acp.db") as store:
        workspace_path = create_workspace_dir(
            config.workspace_base_path, args.user_id, args.thread_id
        )

        logger.info("Spawning kiro-cli acp --agent %s...", config.kiro_agent_name)
        client = await ACPClient.spawn(config.kiro_agent_name, config.log_level)

        try:
            logger.info("Initializing ACP protocol...")
            await client.initialize()

            session_record = store.get_session(args.user_id, args.thread_id)

            if session_record is not None:
                logger.info("Loading existing session %s...", session_record.session_id)
                await client.session_load(session_record.session_id, cwd=workspace_path)
                session_id = session_record.session_id
                is_new_session = False
            else:
                logger.info("Creating new session (cwd=%s)...", workspace_path)
                session_id = await client.session_new(cwd=workspace_path)
                store.upsert_session(args.user_id, args.thread_id, session_id, workspace_path)
                is_new_session = True

            logger.info("Session ID: %s (new=%s)", session_id, is_new_session)

            if is_new_session:
                user_message = "Remember this number: 1234. Just confirm you memorized it."
            else:
                user_message = "What number did I ask you to remember?"

            logger.info("Sending: %s", user_message)
            print(f"\n--- Prompt: {user_message} ---\n")

            async for update in client.session_prompt(
                session_id,
                [{"type": "text", "text": user_message}],
            ):
                update_type = update.get("sessionUpdate", "")

                if update_type == "agent_message_chunk":
                    content = update.get("content", {})
                    if content.get("type") == "text":
                        print(content["text"], end="", flush=True)
                elif update_type == TURN_END:
                    print("\n\n--- End ---")
                    break
                else:
                    logger.debug("Update: %s", update_type)

        finally:
            await client.kill()


if __name__ == "__main__":
    asyncio.run(main())
