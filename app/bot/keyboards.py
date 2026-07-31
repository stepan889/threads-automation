from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu(is_running: bool = False) -> InlineKeyboardMarkup:
    toggle = "⏹ Остановить" if is_running else "▶️ Запустить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle, callback_data="toggle")],
            [InlineKeyboardButton(text="✍️ Пост сейчас", callback_data="post_now")],
            [InlineKeyboardButton(text="⏱ Интервал", callback_data="interval")],
            [InlineKeyboardButton(text="📊 Статус", callback_data="status")],
            [InlineKeyboardButton(text="📚 История", callback_data="history")],
            [InlineKeyboardButton(text="🔗 Подключить Threads", callback_data="connect")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        ]
    )


def interval_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="30 мин", callback_data="set_interval:30"),
                InlineKeyboardButton(text="1 час", callback_data="set_interval:60"),
                InlineKeyboardButton(text="3 часа", callback_data="set_interval:180"),
            ],
            [InlineKeyboardButton(text="✍️ Ввести вручную", callback_data="custom_interval")],
            [InlineKeyboardButton(text="← Назад", callback_data="menu")],
        ]
    )


def preview_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="publish_draft")],
        [InlineKeyboardButton(text="🔄 Перегенерировать", callback_data="regenerate"), InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_draft")],
    ])


def settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить системный промпт", callback_data="edit_prompt")],
        [InlineKeyboardButton(text="Изменить тему", callback_data="edit_topic")],
        [InlineKeyboardButton(text="← Назад", callback_data="menu")],
    ])
