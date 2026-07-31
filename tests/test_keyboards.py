from app.bot.keyboards import interval_menu, main_menu


def test_main_menu_contains_all_actions() -> None:
    labels = [button.text for row in main_menu().inline_keyboard for button in row]
    assert any("Запустить" in label for label in labels)
    for expected in ("Пост сейчас", "Интервал", "Статус", "История", "Подключить Threads", "Настройки"):
        assert any(expected in label for label in labels)


def test_interval_menu_allows_custom_value() -> None:
    buttons = [button for row in interval_menu().inline_keyboard for button in row]
    assert any(button.callback_data == "custom_interval" for button in buttons)
