"""Unit 3 entry point — aiogram Telegram bot with streaming via sendMessageDraft.

Usage:
    uv run main.py
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from tg_acp.acp_client import ACPClient
from tg_acp.bot_handlers import BotContext, router, setup
from tg_acp.config import Config
from tg_acp.provisioner import WorkspaceProvisioner
from tg_acp.session_store import SessionStore


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

    store = SessionStore(db_path="./tg-acp.db")

    logger.info("Spawning kiro-cli acp --agent %s...", config.kiro_agent_name)
    client = await ACPClient.spawn(config.kiro_agent_name, config.log_level)
    await client.initialize()
    logger.info("ACP Client ready")

    ctx = BotContext(config=config, store=store, client=client)
    setup(ctx)

    bot = Bot(token=config.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    async def on_shutdown() -> None:
        logger.info("Shutting down — killing ACP Client...")
        await client.kill()
        store.close()
        logger.info("Cleanup complete")

    dp.shutdown.register(on_shutdown)

    logger.info("Starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
