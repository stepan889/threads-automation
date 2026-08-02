import asyncio

from aiogram import Bot, Dispatcher

from app.bot.handlers import router
from app.bot.middleware import AdminOnlyMiddleware, DatabaseSessionMiddleware
from app.config import get_settings
from app.db import SessionFactory
from app.logging import configure_logging
from app.scheduler import AutopostScheduler
from app.services.crypto import TokenCipher
from app.services.oauth_state import OAuthStateCodec
from app.services.openrouter import OpenRouterPostGenerator
from app.services.threads import ThreadsClient
from app.services.workflow import PublishingWorkflow


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    bot = Bot(token=settings.telegram_bot_token)
    generator = OpenRouterPostGenerator(settings.openrouter_api_key, settings.openrouter_model, settings.default_system_prompt, settings.openrouter_timeout_seconds)
    threads = ThreadsClient(settings.threads_client_id, settings.threads_client_secret, settings.threads_redirect_uri, settings.threads_api_version)
    cipher = TokenCipher(settings.token_encryption_key)
    oauth_state = OAuthStateCodec(settings.threads_client_secret, settings.oauth_state_ttl_seconds)
    workflow = PublishingWorkflow(generator, threads, cipher)
    autopost = AutopostScheduler(SessionFactory, workflow)
    await autopost.start()
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(DatabaseSessionMiddleware(SessionFactory))
    dispatcher.message.middleware(AdminOnlyMiddleware(settings.admin_telegram_ids))
    dispatcher.callback_query.middleware(AdminOnlyMiddleware(settings.admin_telegram_ids))
    dispatcher.include_router(router)
    try:
        await dispatcher.start_polling(bot, settings=settings, workflow=workflow, threads=threads,
                                       oauth_state=oauth_state, autopost=autopost)
    finally:
        autopost.shutdown()
        await generator.close(); await threads.close(); await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
