# Telegram-бот для OpenRouter → Threads

Рабочая асинхронная панель: OpenRouter генерирует текст, Telegram показывает предпросмотр, официальный Threads Graph API публикует его вручную или по расписанию. Доступ ограничен `ADMIN_TELEGRAM_IDS`.

## Возможности

- предпросмотр, публикация, перегенерация и отмена;
- системный промпт и тема отдельно для каждого администратора;
- официальный Threads OAuth, долгоживущий токен и refresh перед истечением;
- Fernet-шифрование токенов в PostgreSQL;
- восстановление активных расписаний после рестарта, coalescing и один экземпляр job;
- история `published`/`failed`, безопасная диагностика и внешний ID;
- `/health` (процесс) и `/ready` (PostgreSQL).

## Настройка

1. Скопируйте `.env.example` в `.env`. Укажите Telegram-токен, разрешённые числовые ID, пароль PostgreSQL и совпадающий пароль в `DATABASE_URL`.
2. Создайте OpenRouter API key и задайте точный slug модели в `OPENROUTER_MODEL`. Код использует `POST /api/v1/chat/completions` с Bearer-аутентификацией.
3. Сгенерируйте `TOKEN_ENCRYPTION_KEY` один раз командой из `.env.example`. Не меняйте ключ, пока в БД есть подключения: старые токены перестанут расшифровываться.
4. Нужен публичный HTTPS-адрес API (например, домен или HTTPS-туннель на порт 8000). Укажите точный URI вида `https://bot.example.com/oauth/threads/callback` в `THREADS_REDIRECT_URI`.
5. В Meta for Developers создайте приложение с Threads API, добавьте Threads use case, настройте Threads App ID/Secret и OAuth Redirect URL **в точности** равный `THREADS_REDIRECT_URI`. Добавьте тестового Threads-пользователя до публикации приложения либо пройдите App Review. Боту нужны permissions `threads_basic` и `threads_content_publish`.

Ссылка кнопки «Подключить Threads» ведёт на `https://threads.net/oauth/authorize`. Callback меняет authorization code на short-lived token, затем на long-lived token, получает `/me` и сохраняет токен зашифрованным. Публикация выполняет два официальных шага: `/{threads-user-id}/threads` с `media_type=TEXT`, затем `/{threads-user-id}/threads_publish`.

Официальная документация: [OpenRouter Chat Completions](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request), [OpenRouter errors](https://openrouter.ai/docs/api/reference/errors-and-debugging), [Threads access tokens and permissions](https://developers.facebook.com/docs/threads/get-started/get-access-tokens-and-permissions), [Threads publishing](https://developers.facebook.com/docs/threads/posts), [Threads API getting started](https://developers.facebook.com/docs/threads/get-started/).

## Запуск

Путь проекта содержит кириллицу, поэтому используйте стабильное явное имя Compose:

```bash
docker compose -p threads-bot up --build -d
docker compose -p threads-bot ps
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
docker compose -p threads-bot logs --tail=100 api bot
```

Для развёртывания в Amvera используйте готовые `Dockerfile` и `amvera.yml`.
Полная пошаговая инструкция, включая безопасный одноразовый импорт токена
отдельного Threads-пользователя: [DEPLOY_AMVERA.md](DEPLOY_AMVERA.md).

Миграции выполняются отдельным контейнером до API и бота. Volume `postgres_data` сохраняется; `0002_integrations` только добавляет поля.

## Разработка

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

Сетевые unit-тесты используют `httpx.MockTransport` и не обращаются к OpenRouter или Meta. Реальный OAuth невозможно завершить без настроенного приложения, публичного HTTPS callback и ручного входа владельца Threads.

## Эксплуатационные замечания

- Секреты и токены не выводятся в Telegram или логи. Диагностика сохраняет только нормализованное сообщение и HTTP-код.
- При неопределённом результате таймаута Threads операция получает `failed`; перед ручным повтором проверьте профиль/историю, поскольку сервер мог принять запрос до разрыва соединения.
- Jobs живут в памяти APScheduler, а источником истины служит `bot_states.is_running`; при старте они восстанавливаются из PostgreSQL. Один экземпляр бота обязателен, иначе нужна распределённая блокировка.
