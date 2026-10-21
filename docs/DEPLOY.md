# Деплой

## Состав

```
n8n  ──HTTP──▶  smm:8000  ──▶  postgres
                   │
                   ├──▶ cardrender:8000
                   ├──▶ DeepSeek API
                   ├──▶ Telegram relay
                   ├──▶ MAX Bot API
                   ├──▶ VK API
                   └──▶ /srv/dzen (Dzen feed)
```

## Переменные окружения

Заполняются в `infra/.env` (в git не попадает):

| Переменная | Зачем |
|---|---|
| `POSTGRES_*` | база |
| `TELEGRAM_RELAY_BASE` | база Netlify-релея до Telegram API |
| `TELEGRAM_MARKETING_BOT_TOKEN` | токен бота |
| `TELEGRAM_ADMIN_CHAT_ID` | твой чат |
| `TELEGRAM_CHANNEL_ID` | канал для публикации |
| `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` | AI |
| `MAX_BOT_TOKEN`, `MAX_CHAT_ID` | MAX |
| `VK_TOKEN`, `VK_GROUP_ID` | VK |
| `SMM_TEST_TOKEN` | защита `/test` |
| `TIMEZONE` | планировщик |

`services/ai_smm` читает их через `config.py`. В репозитории есть только
`infra/.env.example`.

## Запуск

```bash
docker compose -f infra/docker-compose.yml up -d --build
```

При первом старте `ai_smm` сам создаёт `smm_sessions` и добавляет колонки
`brief`, `strategy`, `images`, `quality_score`, `goal`, `audience`, `dzen_url`
в `content_drafts`.

## Продакшен-раскладка

На VPS проект лежит в `/opt/marketing`:

```
/opt/marketing/
  docker-compose.yml
  .env
  cardrender/
  smm/
  dzen/
  uploads/
```

Build-контексты в server-compose: `./cardrender` и `./smm`. В репозитории —
`../services/cardrender` и `../services/ai_smm`.

## n8n

В `Marketing Bot` добавлены три узла:

- `SMM Router` — POST `http://smm:8000/event`;
- `SMM Handled?` — IF по `handled`;
- `SMM Done` — no-op.

`Emit Updates` → `SMM Router` → IF:
- `true` → `SMM Done`;
- `false` → старый `Is Business?`.

Меню содержит кнопку `🧠 AI-Маркетолог` (`menu:smm`). Если сервис недоступен,
HTTP-узел с `onError: continueRegularOutput` пропускает событие в старый сценарий.

## Проверка

```bash
curl -s http://127.0.0.1:8101/health
curl -X POST http://127.0.0.1:8101/test -H "X-Test-Token: $SMM_TEST_TOKEN"
```

`/health` проверяет БД, cardrender, Telegram, DeepSeek, MAX, VK и Dzen-каталог.
`/test` делает полный тихий прогон и не публикует в каналы.

## Dzen

`dzen/` монтируется в `smm` как `/srv/dzen` read-write и в Caddy как `/srv/dzen`
read-only. Публикация создаёт:

- `articles/<id>-<slug>.html`
- `cards/<id>.png`
- запись в `feed.xml`
- ссылку в `index.html`

Caddy уже отдаёт `dzen.saoneclick.ru` и `feed.xml` с правильным Content-Type.
