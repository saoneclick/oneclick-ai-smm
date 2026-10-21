"""Refresh legacy Dzen cover cards with the new cardrender design system.

Run on the server after cardrender is deployed:

    python3 services/dzen/refresh_legacy_cards.py

The mapping is intentionally editorial: these are the 10 legacy articles that
were published before the AI-SMM card pipeline existed.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.request
from pathlib import Path

DZEN_DIR = Path(os.environ.get("DZEN_DIR", "/opt/marketing/dzen"))
CARD_RENDER_URL = os.environ.get("CARD_RENDER_URL", "http://127.0.0.1:8100").rstrip("/")

CARDS = (
    {"id": 6, "headline": "Гуманитарий в кодинге", "subtitle": "Как человек без технического бэкграунда собрал первого бота.", "tag": "ИСТОРИЯ", "art": "terminal", "theme": "violet", "layout": "poster"},
    {"id": 7, "headline": "Считаем, а не гадаем", "subtitle": "Пять цифр, которые меняют решения в бизнесе.", "tag": "МЕТРИКИ", "art": "chart", "theme": "cyan", "layout": "stack"},
    {"id": 8, "headline": "Активность — не прибыль", "subtitle": "Почему Битрикс24 показывает движение, а не деньги.", "tag": "МЕТРИКИ", "art": "chart", "theme": "emerald", "layout": "split"},
    {"id": 9, "headline": "AI-агент вместо колл-центра", "subtitle": "Что реально можно отдать AI, а что — людям.", "tag": "AI-АГЕНТ", "art": "network", "theme": "violet", "layout": "stage"},
    {"id": 10, "headline": "Ваш сайт в зоне риска?", "subtitle": "Критическая уязвимость PHP. Проверьте версию.", "tag": "БЕЗОПАСНОСТЬ", "art": "guard", "theme": "amber", "layout": "stack"},
    {"id": 11, "headline": "Из 3 часов в 10 секунд", "subtitle": "Автоматизировали отчётность в Битрикс24.", "tag": "КЕЙС", "art": "chart", "theme": "emerald", "layout": "split"},
    {"id": 12, "headline": "Хаос в Telegram — задачи в CRM", "subtitle": "Бот превращает переписку в понятные задачи.", "tag": "ИНТЕГРАЦИЯ", "art": "terminal", "theme": "cyan", "layout": "stack"},
    {"id": 13, "headline": "Дашборд вместо отчёта", "subtitle": "Пять цифр, которые нужны каждое утро.", "tag": "ДАШБОРД", "art": "chart", "theme": "cyan", "layout": "poster"},
    {"id": 14, "headline": "AI-агент первой линии", "subtitle": "Что автоматизируется, а где нужен человек.", "tag": "AI-АГЕНТ", "art": "network", "theme": "cyan", "layout": "split"},
    {"id": 15, "headline": "5 признаков автоматизации продаж", "subtitle": "Проверьте, не пора ли менять процессы.", "tag": "ЧЕК-ЛИСТ", "art": "grid", "theme": "amber", "layout": "stack"},
)


def main() -> None:
    cards_dir = DZEN_DIR / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    backup = DZEN_DIR / f"cards.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copytree(cards_dir, backup)
    print("backup", backup)
    for card in CARDS:
        payload = {k: card[k] for k in ("headline", "subtitle", "tag", "art", "theme", "layout") if card.get(k)}
        req = urllib.request.Request(
            f"{CARD_RENDER_URL}/card",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            data = response.read()
        path = cards_dir / f"{card['id']}.png"
        path.write_bytes(data)
        print(card["id"], path, len(data))


if __name__ == "__main__":
    main()
