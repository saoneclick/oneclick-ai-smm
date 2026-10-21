# ai_smm

FastAPI-сервис личного AI-SMM. Принимает Telegram updates от n8n, ведёт интервью,
генерирует стратегию и тексты через DeepSeek, готовит карточку через `cardrender`,
хранит сессии и черновики в PostgreSQL и публикует в Telegram / MAX / VK / Дзен.

## Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/event` | Telegram update от n8n; отвечает `{"handled": bool}` |
| POST | `/test` | тихий тест, требует `X-Test-Token` |
| GET | `/health` | проверка БД, cardrender, Telegram, DeepSeek, MAX, VK, Dzen |
| GET | `/media/...` | отдача загруженных фото для cardrender и превью |

## Модули

| Файл | Что делает |
|---|---|
| `app.py` | FastAPI, роутинг, state machine, превью, публикация, тест |
| `interview.py` | шаги, кнопки, валидация ответов, summary |
| `prompts.py` | стратег, копирайтер, рубрика, anti-cliché |
| `generation.py` | два вызова DeepSeek, нормализация, вставка черновика |
| `deepseek.py` | JSON-режим, retry, парсинг |
| `media.py` | скачивание, EXIF, resize, хранение |
| `visual.py` | выбор обложки и вызов cardrender |
| `publisher.py` | Telegram / MAX / VK / Dzen, превью |
| `dzen.py` | статья, feed.xml, index.html, slug |
| `telegram.py` | relay-клиент |
| `db.py` | PostgreSQL и схема |

## Локальный запуск

```bash
pip install -r requirements.txt
export UPLOADS_DIR=/tmp/smm-uploads
export DATABASE_URL=postgresql://...
uvicorn app:app --reload --port 8000
```

## Тесты

```bash
pip install -r requirements.txt
pip install pytest
pytest -q
```

Покрыты: переходы интервью, редактирование, переключение каналов, промпты, Dzen,
resize и хранение изображений.

## Ограничения

- VK-фото зависят от типа токена; текст публикуется.
- Планировщик публикует `smm_scheduled` раз в минуту.
- Для AI-иллюстраций можно расширить `visual.py` вызовом DALL·E/Flux и передавать
  результат в `image_url`.
