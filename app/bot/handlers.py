from datetime import timezone
from html import escape

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import interval_menu, main_menu, preview_menu, settings_menu
from app.config import Settings
from app.repository import get_or_create_state, recent_posts
from app.scheduler import AutopostScheduler
from app.services.errors import ServiceError
from app.services.oauth_state import OAuthStateCodec
from app.services.threads import ThreadsClient
from app.services.workflow import PublishingWorkflow

router = Router()


class EditSettings(StatesGroup):
    prompt = State()
    topic = State()


class SetCustomInterval(StatesGroup):
    minutes = State()


async def _state(session: AsyncSession, telegram_id: int, settings: Settings):
    return await get_or_create_state(session, telegram_id, settings.default_post_interval_minutes)


@router.message(CommandStart())
async def start(message: Message, session: AsyncSession, settings: Settings) -> None:
    state = await _state(session, message.from_user.id, settings)  # type: ignore[union-attr]
    await message.answer("Панель управления публикациями в Threads", reply_markup=main_menu(state.is_running))


@router.callback_query(F.data == "menu")
async def show_menu(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    state = await _state(session, callback.from_user.id, settings)
    await callback.message.edit_text("Главное меню", reply_markup=main_menu(state.is_running))  # type: ignore[union-attr]
    await callback.answer()


@router.callback_query(F.data == "toggle")
async def toggle(callback: CallbackQuery, session: AsyncSession, settings: Settings, autopost: AutopostScheduler) -> None:
    state = await _state(session, callback.from_user.id, settings)
    if not state.is_running and not state.threads_connected:
        await callback.answer("Сначала подключите Threads.", show_alert=True)
        return
    state.is_running = not state.is_running
    await session.commit()
    if state.is_running:
        autopost.schedule(state.telegram_id, state.interval_minutes)
    else:
        autopost.stop(state.telegram_id)
    await callback.message.edit_text("Автопостинг запущен." if state.is_running else "Автопостинг остановлен.", reply_markup=main_menu(state.is_running))  # type: ignore[union-attr]
    await callback.answer()


async def _generate_preview(callback: CallbackQuery, session: AsyncSession, settings: Settings, workflow: PublishingWorkflow, fsm: FSMContext) -> None:
    state = await _state(session, callback.from_user.id, settings)
    status_message = await callback.message.answer("⏳ Генерирую пост…")  # type: ignore[union-attr]
    try:
        generated = await workflow.generate(state)
        await fsm.update_data(draft=generated.text)
        await status_message.edit_text(f"Предпросмотр:\n\n{escape(generated.text)}", reply_markup=preview_menu(), parse_mode="HTML")
    except ServiceError as exc:
        await status_message.edit_text(f"Не удалось сгенерировать пост: {escape(str(exc))}", parse_mode="HTML")


@router.callback_query(F.data.in_({"post_now", "regenerate"}))
async def post_now(callback: CallbackQuery, session: AsyncSession, settings: Settings, workflow: PublishingWorkflow, state: FSMContext) -> None:
    await callback.answer()
    await _generate_preview(callback, session, settings, workflow, state)


@router.callback_query(F.data == "publish_draft")
async def publish_draft(callback: CallbackQuery, session: AsyncSession, settings: Settings, workflow: PublishingWorkflow, state: FSMContext) -> None:
    draft = (await state.get_data()).get("draft")
    if not draft:
        await callback.answer("Черновик устарел. Сгенерируйте заново.", show_alert=True)
        return
    if (await state.get_data()).get("publishing"):
        await callback.answer("Публикация уже выполняется.", show_alert=True)
        return
    await state.update_data(publishing=True)
    await callback.answer()
    await callback.message.edit_text("⏳ Публикую в Threads…")  # type: ignore[union-attr]
    bot_state = await _state(session, callback.from_user.id, settings)
    post = await workflow.publish_text(session, bot_state, draft)
    await state.clear()
    if post.status == "published":
        await callback.message.edit_text(f"✅ Опубликовано. ID: {escape(post.external_id or '')}", parse_mode="HTML")  # type: ignore[union-attr]
    else:
        await callback.message.edit_text(f"❌ Не опубликовано: {escape(post.diagnostic or 'неизвестная ошибка')}", parse_mode="HTML")  # type: ignore[union-attr]


@router.callback_query(F.data == "cancel_draft")
async def cancel_draft(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Публикация отменена.")  # type: ignore[union-attr]
    await callback.answer()


@router.callback_query(F.data == "interval")
async def choose_interval(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Выберите интервал:", reply_markup=interval_menu())  # type: ignore[union-attr]
    await callback.answer()


@router.callback_query(F.data.startswith("set_interval:"))
async def set_interval(callback: CallbackQuery, session: AsyncSession, settings: Settings, autopost: AutopostScheduler) -> None:
    minutes = int(callback.data.split(":", 1)[1])  # type: ignore[union-attr]
    state = await _state(session, callback.from_user.id, settings)
    state.interval_minutes = minutes
    await session.commit()
    if state.is_running:
        autopost.schedule(state.telegram_id, minutes)
    await callback.message.edit_text(f"Интервал: {minutes} мин.", reply_markup=main_menu(state.is_running))  # type: ignore[union-attr]
    await callback.answer()


@router.callback_query(F.data == "custom_interval")
async def request_custom_interval(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SetCustomInterval.minutes)
    await callback.message.answer(  # type: ignore[union-attr]
        "Отправьте интервал в минутах — целое число от 1 до 10080 (7 дней).\n"
        "Например: 45"
    )
    await callback.answer()


@router.message(SetCustomInterval.minutes)
async def save_custom_interval(message: Message, session: AsyncSession, settings: Settings, autopost: AutopostScheduler, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value.isdecimal():
        await message.answer("Введите целое число минут. Например: 45")
        return
    minutes = int(value)
    if not 1 <= minutes <= 10080:
        await message.answer("Интервал должен быть от 1 до 10080 минут.")
        return

    bot_state = await _state(session, message.from_user.id, settings)  # type: ignore[union-attr]
    bot_state.interval_minutes = minutes
    await session.commit()
    if bot_state.is_running:
        autopost.schedule(bot_state.telegram_id, minutes)
    await state.clear()
    await message.answer(f"Интервал сохранён: {minutes} мин.", reply_markup=main_menu(bot_state.is_running))


@router.callback_query(F.data == "status")
async def status(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    state = await _state(session, callback.from_user.id, settings)
    connected = f"подключён (@{state.threads_username})" if state.threads_connected and state.threads_username else ("подключён" if state.threads_connected else "не подключён")
    await callback.message.answer(f"Автопостинг: {'работает' if state.is_running else 'остановлен'}\nИнтервал: {state.interval_minutes} мин.\nThreads: {connected}")  # type: ignore[union-attr]
    await callback.answer()


@router.callback_query(F.data == "history")
async def history(callback: CallbackQuery, session: AsyncSession) -> None:
    posts = await recent_posts(session, callback.from_user.id)
    lines = []
    for post in posts:
        created = post.created_at.astimezone(timezone.utc).strftime("%d.%m %H:%M")
        suffix = f" · ID {post.external_id}" if post.external_id else ""
        lines.append(f"• {created} · {post.status}{suffix}\n{post.content[:80] or '—'}")
    await callback.message.answer("История пуста." if not lines else "Последние операции (UTC):\n" + "\n".join(lines))  # type: ignore[union-attr]
    await callback.answer()


@router.callback_query(F.data == "connect")
async def connect(callback: CallbackQuery, threads: ThreadsClient, oauth_state: OAuthStateCodec) -> None:
    try:
        url = threads.authorization_url(oauth_state.encode(callback.from_user.id))
        await callback.message.answer(f"Откройте ссылку для подключения Threads (действует ограниченное время):\n{url}")  # type: ignore[union-attr]
    except ServiceError as exc:
        await callback.message.answer(str(exc))  # type: ignore[union-attr]
    await callback.answer()


@router.callback_query(F.data == "settings")
async def settings_menu_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Настройки генерации:", reply_markup=settings_menu())  # type: ignore[union-attr]
    await callback.answer()


@router.callback_query(F.data == "edit_prompt")
async def edit_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(EditSettings.prompt)
    await callback.message.answer("Отправьте новый системный промпт (до 4000 символов).")  # type: ignore[union-attr]
    await callback.answer()


@router.callback_query(F.data == "edit_topic")
async def edit_topic(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(EditSettings.topic)
    await callback.message.answer("Отправьте тему публикаций (до 1000 символов).")  # type: ignore[union-attr]
    await callback.answer()


@router.message(EditSettings.prompt)
async def save_prompt(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or len(text) > 4000:
        await message.answer("Промпт должен содержать от 1 до 4000 символов.")
        return
    bot_state = await _state(session, message.from_user.id, settings)  # type: ignore[union-attr]
    bot_state.system_prompt = text
    await session.commit(); await state.clear()
    await message.answer("Системный промпт сохранён.")


@router.message(EditSettings.topic)
async def save_topic(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or len(text) > 1000:
        await message.answer("Тема должна содержать от 1 до 1000 символов.")
        return
    bot_state = await _state(session, message.from_user.id, settings)  # type: ignore[union-attr]
    bot_state.topic = text
    await session.commit(); await state.clear()
    await message.answer("Тема сохранена.")
