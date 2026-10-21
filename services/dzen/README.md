# Dzen builder

Полная пересборка статического мини-сайта и RSS для Дзена из `content_drafts`.

```bash
cd /opt/marketing
python3 services/dzen/build.py
```

Скрипт:

- собирает `index.html`, `feed.xml`, `robots.txt`;
- создаёт статьи `articles/<id>-<slug>.html`;
- при отсутствии карточки рендерит её через `cardrender`;
- исключает статусы `rejected`, `test`, `smm_error`.

Переменные: `DZEN_DIR`, `DZEN_PUBLIC_BASE`, `COMPOSE_DIR`, `POSTGRES_USER`,
`POSTGRES_DB`, `CARD_RENDER_URL`.

Если AI-SMM уже публикует в Дзен инкрементально (статья + запись в feed), полную
пересборку запускайте осознанно: она перезапишет `feed.xml` и `index.html` по
содержимому БД.

## Обновить старые обложки

`refresh_legacy_cards.py` перерисовывает 10 legacy-обложек новым алгоритмом
(щит, терминал, графики, сетка, разные темы и раскладки) и сохраняет поверх
`cards/<id>.png`:

```bash
python3 services/dzen/refresh_legacy_cards.py
```
