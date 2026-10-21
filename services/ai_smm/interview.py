from __future__ import annotations

import re
from typing import Any

STEPS = ("goal", "audience", "topic", "proof", "format", "cta", "visual", "summary")
STEP_NUM = {step: i + 1 for i, step in enumerate(STEPS)}
TOTAL_QUESTIONS = 7

GOALS = (
    ("leads", "🎯 Заявки и лиды"),
    ("reach", "📣 Охват и репосты"),
    ("trust", "🧠 Доверие и экспертность"),
    ("launch", "🚀 Запуск и анонс"),
    ("partners", "🤝 Найм и партнёры"),
)
AUDIENCES = (
    ("owners", "Владельцы бизнеса"),
    ("sales", "Руководители продаж"),
    ("marketing", "Маркетологи"),
    ("it", "IT и разработчики"),
)
FORMATS = (
    ("auto", "🤖 Авто — выбери сам"),
    ("case", "Кейс с цифрами"),
    ("breakdown", "Разбор / мнение"),
    ("checklist", "Чек-лист"),
    ("story", "Личная история"),
    ("provocation", "Провокация"),
)
CTAS = (
    ("dm", "Написать в личку"),
    ("lead", "Оставить заявку"),
    ("link", "Перейти по ссылке"),
    ("subscribe", "Подписаться"),
    ("none", "Без CTA"),
)
CHANNELS = (
    ("telegram", "Telegram"),
    ("max", "MAX"),
    ("vk", "VK"),
    ("dzen", "Дзен"),
)
CHANNEL_TITLES = dict(CHANNELS)
GOAL_TITLES = dict(GOALS)
AUDIENCE_TITLES = dict(AUDIENCES)
FORMAT_TITLES = dict(FORMATS)
CTA_TITLES = dict(CTAS)
FIELD_TITLES = {
    "goal": "Цель",
    "audience": "Аудитория",
    "topic": "Тема",
    "proof": "Факты и цифры",
    "format": "Формат",
    "cta": "CTA",
    "visual": "Визуал",
    "channels": "Каналы",
}

MAX_LEN = {"topic": 1500, "proof": 1200, "constraints": 600}


def new_session_data() -> dict[str, Any]:
    return {
        "goal": "",
        "audience": "",
        "topic": "",
        "proof": "",
        "format": "",
        "cta": "",
        "constraints": "",
        "assets": [],
        "channels": ["telegram", "max", "vk", "dzen"],
        "editing": "",
        "assets_ack": False,
        "headline": "",
        "strategy": {},
        "draft_id": 0,
        "variant": 0,
    }


def _kb(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": rows}


def _btn(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


def _cancel_row() -> list[dict[str, str]]:
    return [_btn("❌ Отмена", "smm:cancel")]


def _step_error() -> tuple[str, dict[str, Any]]:
    return ("Не понял ответ. Попробуй ещё раз или нажми «Отмена».", _kb([_cancel_row()]))


def render_question(step: str, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if step == "goal":
        rows = [[_btn(t, f"smm:goal:{v}") for v, t in GOALS[i:i + 2]] for i in range(0, len(GOALS), 2)]
        rows.append(_cancel_row())
        return (
            "Шаг 1/7 · 🎯 **Какая цель у поста?**\n\n"
            "От цели зависит хук, структура и CTA. Выбери один вариант.",
            _kb(rows),
        )

    if step == "audience":
        rows = [[_btn(t, f"smm:aud:{v}") for v, t in AUDIENCES[i:i + 2]] for i in range(0, len(AUDIENCES), 2)]
        rows.append([_btn("✍️ Своя аудитория", "smm:aud:custom")])
        rows.append([_btn("⬅️ Назад", "smm:back"), *_cancel_row()])
        return (
            "Шаг 2/7 · 👥 **Кто аудитория?**\n\n"
            "Кому важнее всего прочитать этот пост?",
            _kb(rows),
        )

    if step == "topic":
        rows = [[_btn("⬅️ Назад", "smm:back"), *_cancel_row()]]
        return (
            "Шаг 3/7 · 🧠 **О чём пост?**\n\n"
            "Опиши повод и главную мысль в 1–3 предложениях. Можно своими словами, без красоты.\n\n"
            "Например: «Клиент терял заявки из Telegram, внедрили бота и CRM, первый ответ ускорился с 6 часов до 3 минут».",
            _kb(rows),
        )

    if step == "proof":
        rows = [
            [_btn("Нет цифр — без выдумок", "smm:skip")],
            [_btn("⬅️ Назад", "smm:back"), *_cancel_row()],
        ]
        return (
            "Шаг 4/7 · 📊 **Какие факты, цифры или результат можно использовать?**\n\n"
            "Сроки, проценты, суммы, количество заявок, отзыв клиента. Только реальные данные — "
            "AI не должен ничего придумывать.\n\n"
            "Если цифр нет — нажми «Нет цифр».",
            _kb(rows),
        )

    if step == "format":
        rows = [[_btn(t, f"smm:fmt:{v}") for v, t in FORMATS[i:i + 2]] for i in range(0, len(FORMATS), 2)]
        rows.append([_btn("⬅️ Назад", "smm:back"), *_cancel_row()])
        return (
            "Шаг 5/7 · 🧩 **Какой формат?**\n\n"
            "«Авто» — AI выберет сам под цель и тему.",
            _kb(rows),
        )

    if step == "cta":
        rows = [[_btn(t, f"smm:cta:{v}") for v, t in CTAS[i:i + 2]] for i in range(0, len(CTAS), 2)]
        rows.append([_btn("⬅️ Назад", "smm:back"), *_cancel_row()])
        return (
            "Шаг 6/7 · 📣 **Что должен сделать читатель?**\n\n"
            "Один пост — один CTA. Без «напишите, подпишитесь и перейдите».",
            _kb(rows),
        )

    if step == "visual":
        count = len(data.get("assets") or [])
        rows = [
            [_btn("✅ Готово", "smm:visual_done"), _btn("🎨 Сделай арт", "smm:visual_auto")],
            [_btn("🗑 Очистить", "smm:visual_clear"), *_cancel_row()],
        ]
        extra = f"\n\nПринято файлов: **{count}**." if count else ""
        return (
            "Шаг 7/7 · 🖼 **Визуал**\n\n"
            "Пришли до 10 фото/скриншотов или ссылку на картинку. Можно несколько сообщений. "
            "Когда закончишь — нажми «Готово». Если фото нет — «Сделай арт».\n\n"
            "Если есть ограничения по тону или фактам — просто напиши их текстом." + extra,
            _kb(rows),
        )

    if step == "summary":
        text, kb = render_summary(data)
        return text, kb

    return _step_error()


def _plain(value: str) -> str:
    return re.sub(r"^[^\w]+", "", value or "").strip()


def render_summary(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    assets = data.get("assets") or []
    channels = data.get("channels") or []
    ch_text = ", ".join(CHANNEL_TITLES.get(c, c) for c in channels) or "не выбраны"
    visual = f"{len(assets)} фото/скрин." if assets else "авто-арт (без фото)"
    constraints = (data.get("constraints") or "").strip()
    lines = [
        "📋 **Проверь бриф**",
        "",
        f"🎯 Цель: {_plain(GOAL_TITLES.get(data.get('goal'), '—'))}",
        f"👥 Аудитория: {AUDIENCE_TITLES.get(data.get('audience'), data.get('audience') or '—')}",
        f"🧠 Тема: {data.get('topic') or '—'}",
        f"📊 Факты: {data.get('proof') or '—'}",
        f"🧩 Формат: {_plain(FORMAT_TITLES.get(data.get('format'), 'Авто'))}",
        f"📣 CTA: {_plain(CTA_TITLES.get(data.get('cta'), 'Без CTA'))}",
        f"🖼 Визуал: {visual}",
        f"📡 Каналы: {ch_text}",
    ]
    if constraints:
        lines.append(f"⚠️ Ограничения: {constraints}")
    lines += ["", "Всё верно? Собираю стратегию, текст и карточку."]
    rows = []
    toggles = []
    for code, title in CHANNELS:
        mark = "✅" if code in channels else "⬜"
        toggles.append(_btn(f"{mark} {title}", f"smm:channel:{code}"))
    rows.append(toggles[:2])
    rows.append(toggles[2:])
    rows.append([_btn("▶️ Собрать пост", "smm:gen")])
    rows.append([_btn("✏️ Изменить", "smm:edit"), _btn("❌ Отмена", "smm:cancel")])
    return ("\n".join(lines), _kb(rows))


def render_edit_menu(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    fields = [
        ("goal", "🎯 Цель"),
        ("audience", "👥 Аудитория"),
        ("topic", "🧠 Тема"),
        ("proof", "📊 Факты"),
        ("format", "🧩 Формат"),
        ("cta", "📣 CTA"),
        ("visual", "🖼 Визуал"),
        ("channels", "📡 Каналы"),
    ]
    rows = [[_btn(label, f"smm:edit:{code}") for code, label in fields[i:i + 2]] for i in range(0, len(fields), 2)]
    rows.append([_btn("⬅️ К брифу", "smm:summary")])
    return ("✏️ **Что изменить?**\n\nВыбери поле — вернусь к нему и потом покажу бриф заново.", _kb(rows))


def next_step(step: str) -> str | None:
    try:
        idx = STEPS.index(step)
    except ValueError:
        return None
    return STEPS[idx + 1] if idx + 1 < len(STEPS) else None


def apply_answer(data: dict[str, Any], step: str, value: str) -> tuple[dict[str, Any], str]:
    value = (value or "").strip()
    data = dict(data or {})
    if step in MAX_LEN:
        value = value[: MAX_LEN[step]]
    if step == "goal":
        codes = {c for c, _ in GOALS}
        if value not in codes:
            raise ValueError("goal")
        data["goal"] = value
    elif step == "audience":
        codes = {c for c, _ in AUDIENCES}
        if value == "custom":
            data["audience"] = ""
            data["audience_custom"] = True
            return data, "audience_custom"
        if value not in codes:
            raise ValueError("audience")
        data["audience"] = value
        data.pop("audience_custom", None)
    elif step == "audience_custom":
        if not value:
            raise ValueError("audience_custom")
        data["audience"] = value[:120]
        data.pop("audience_custom", None)
    elif step == "topic":
        if len(value) < 8:
            raise ValueError("topic")
        data["topic"] = value
    elif step == "proof":
        data["proof"] = "" if value.lower() in ("нет", "нет цифр", "skip") else value
    elif step == "format":
        codes = {c for c, _ in FORMATS}
        if value not in codes:
            raise ValueError("format")
        data["format"] = "" if value == "auto" else value
    elif step == "cta":
        codes = {c for c, _ in CTAS}
        if value not in codes:
            raise ValueError("cta")
        data["cta"] = "" if value == "none" else value
    elif step == "constraints":
        data["constraints"] = value
    else:
        raise ValueError(step)

    if data.get("editing"):
        data["editing"] = ""
        return data, "summary"
    return data, next_step(step) or "summary"


def toggle_channel(data: dict[str, Any], channel: str) -> dict[str, Any]:
    codes = {c for c, _ in CHANNELS}
    if channel not in codes:
        return data
    channels = list(data.get("channels") or [])
    if channel in channels:
        if len(channels) == 1:
            return data
        channels.remove(channel)
    else:
        channels.append(channel)
    order = [c for c, _ in CHANNELS]
    data = dict(data)
    data["channels"] = [c for c in order if c in channels]
    return data


def brief_from_session(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "goal": GOAL_TITLES.get(data.get("goal"), data.get("goal", "")),
        "goal_code": data.get("goal", ""),
        "audience": AUDIENCE_TITLES.get(data.get("audience"), data.get("audience", "")),
        "topic": data.get("topic", ""),
        "proof": data.get("proof", ""),
        "format": FORMAT_TITLES.get(data.get("format"), "Авто"),
        "format_code": data.get("format", ""),
        "cta": CTA_TITLES.get(data.get("cta"), "Без CTA"),
        "cta_code": data.get("cta", ""),
        "constraints": data.get("constraints", ""),
        "channels": data.get("channels", []),
        "images_count": len(data.get("assets") or []),
    }


def parse_callback(data: str) -> tuple[str, str]:
    """Return (action, arg) for callback_data."""
    raw = (data or "").strip()
    if raw == "menu:smm":
        return ("start", "")
    if not raw.startswith("smm:"):
        return ("ignore", "")
    parts = raw.split(":")
    if len(parts) == 2:
        return (parts[1], "")
    return (parts[1], ":".join(parts[2:]))
