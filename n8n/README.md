# n8n workflows

Экспорт боевых workflow без секретов. Перед импортом замените плейсхолдеры:

| Плейсхолдер | Что подставить |
|---|---|
| `RELAY_HOST/tg-RELAY_SECRET` | база вашего Telegram-релея |
| `BOT_TOKEN` | токен Telegram-бота |
| `DEEPSEEK_API_KEY` | ключ DeepSeek |
| `VK_TOKEN`, `0` в `owner_id` | токен и ID VK-сообщества |
| `MAX_BOT_TOKEN`, `0` в `chat_id` | токен и chat_id MAX |
| `0` в `chat_id` Telegram | ваш админ-чат |

## Что внутри

- `marketing-bot.json` — polling Telegram, меню, старый сценарий постов,
  подтверждение, сторис, AI-SMM router.
- `marketing-scheduler.json` — публикация отложенных постов.

## Как импортировать

1. n8n → Workflows → Import from File.
2. Привяжите credential `Postgres marketing` к Postgres-нодам.
3. Замените URL релея и токены (см. таблицу).
4. Активируйте workflow.

## AI-SMM router

В `Marketing Bot`:

```
Emit Updates → SMM Router → SMM Handled?
                              ├─ true  → SMM Done
                              └─ false → Is Business? → старый сценарий
```

`SMM Router` вызывает `http://smm:8000/event`. Если сервис недоступен, нода
настроена с `onError: continueRegularOutput`, и старый сценарий продолжает работу.

Кнопка меню: `🧠 AI-Маркетолог` → `menu:smm`.
