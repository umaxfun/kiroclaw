"""Unit 1 CLI entry point — throwaway, replaced by aiogram bot in Unit 3.

Loads config, provisions ~/.kiro/, spawns kiro-cli, sends a prompt,
and streams the response to stdout.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from tg_acp.acp_client import ACPClient, TURN_END
from tg_acp.config import Config
from tg_acp.provisioner import WorkspaceProvisioner


async def main() -> None:
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

    # Create a test workspace directory
    workspace_dir = str(Path(config.workspace_base_path) / "test_user" / "test_thread")
    Path(workspace_dir).mkdir(parents=True, exist_ok=True)

    logger.info("Spawning kiro-cli acp --agent %s...", config.kiro_agent_name)
    client = await ACPClient.spawn(config.kiro_agent_name, config.log_level)

    try:
        logger.info("Initializing ACP protocol...")
        await client.initialize()

        logger.info("Creating new session (cwd=%s)...", workspace_dir)
        session_id = await client.session_new(cwd=workspace_dir)
        logger.info("Session ID: %s", session_id)

        # Get user input or use default
        if len(sys.argv) > 1:
            user_message = " ".join(sys.argv[1:])
        else:
            user_message = input("Enter prompt (or press Enter for default): ").strip()
            if not user_message:
                user_message = "Say hello and tell me what you can do in one short paragraph."

        logger.info("Sending prompt: %s", user_message[:80])
        print("\n--- Response ---\n")

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
