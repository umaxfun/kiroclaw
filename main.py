"""Entry point — aiogram Telegram bot with process pool and streaming via sendMessageDraft.

Usage:
    uv run main.py
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from tg_acp.bot_handlers import BotContext, get_background_tasks, router, setup
from tg_acp.config import Config
from tg_acp.process_pool import ProcessPool
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

    logger.info("Initializing process pool (agent=%s)...", config.kiro_agent_name)
    pool = ProcessPool(config)
    await pool.initialize()
    logger.info("Process pool ready")

    bot = Bot(token=config.bot_token)
    dp = Dispatcher()

    ctx = BotContext(config=config, store=store, pool=pool, bot=bot)
    setup(ctx)
    dp.include_router(router)

    async def on_shutdown() -> None:
        logger.info("Shutting down — cancelling background tasks...")
        tasks = get_background_tasks()
        for task in list(tasks):
            task.cancel()
        # Wait for all background tasks to finish (with cancellation)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Shutting down — stopping process pool...")
        await pool.shutdown()
        store.close()
        logger.info("Cleanup complete")

    dp.shutdown.register(on_shutdown)

    logger.info("Starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
