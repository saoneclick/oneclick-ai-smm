from __future__ import annotations

import asyncio
import html
import logging
import re
import tempfile
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from db import (
    close as db_close,
    close_session,
    connect as db_connect,
    delete_draft,
    due_scheduled,
    get_active_session,
    get_draft,
    get_recent_posts,
    get_style_rules,
    log_feedback,
    mark_scheduled,
    mark_status,
    pool,
    start_session,
    update_session,
)
from generation import generate_post, regenerate_variant
from interview import (
    AUDIENCE_TITLES,
    CHANNEL_TITLES,
    CTA_TITLES,
    FORMAT_TITLES,
    GOAL_TITLES,
    STEPS,
    apply_answer,
    brief_from_session,
    new_session_data,
    next_step,
    parse_callback,
    render_edit_menu,
    render_question,
    render_summary,
    toggle_channel,
)
from media import (
    absolute_path,
    delete_session_files,
    store_image,
    store_remote_image_async,
)
from publisher import publish_draft, send_preview
from telegram import Telegram, TelegramError

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ai_smm")

settings.uploads_dir.mkdir(parents=True, exist_ok=True)

_tg: Telegram | None = None
_tasks: set[asyncio.Task] = set()
_locks: dict[int, asyncio.Lock] = {}
_scheduler_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tg, _scheduler_task
    await db_connect()
    _tg = Telegram()
    _scheduler_task = asyncio.create_task(scheduler_loop())
    try:
        yield
    finally:
        if _scheduler_task:
            _scheduler_task.cancel()
            try:
                await _scheduler_task
            except asyncio.CancelledError:
                pass
        if _tg:
            await _tg.close()
        await db_close()


app = FastAPI(title="One Click AI SMM", lifespan=lifespan)
app.mount("/media", StaticFiles(directory=str(settings.uploads_dir)), name="media")


def tg() -> Telegram:
    if _tg is None:
        raise RuntimeError("telegram client is not initialized")
    return _tg


def lock_for(chat_id: int) -> asyncio.Lock:
    if chat_id not in _locks:
        _locks[chat_id] = asyncio.Lock()
    return _locks[chat_id]


def strip_md(text: str) -> str:
    return (text or "").replace("**", "")


def message_chat_id(update: dict[str, Any]) -> int:
    cq = update.get("callback_query") or {}
    if cq:
        message = cq.get("message") or {}
        chat = message.get("chat") or {}
        return int(chat.get("id") or (cq.get("from") or {}).get("id") or 0)
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    return int(chat.get("id") or 0)


def is_admin(chat_id: int) -> bool:
    return bool(settings.admin_chat_id) and chat_id == settings.admin_chat_id


async def should_handle(update: dict[str, Any]) -> bool:
    cq = update.get("callback_query")
    if cq:
        data = cq.get("data") or ""
        return data == "menu:smm" or data.startswith("smm:")
    message = update.get("message") or {}
    chat_id = message_chat_id(update)
    if not is_admin(chat_id):
        return False
    text = (message.get("text") or "").strip()
    if text.startswith("/smm") or text.startswith("/test_smm"):
        return True
    if message.get("photo") or message.get("document") or message.get("media_group_id"):
        return True
    if text:
        return (await get_active_session(chat_id)) is not None
    return False


async def send_text(chat_id: int, text: str, keyboard: dict[str, Any] | None = None) -> None:
    await tg().send_message(
        chat_id,
        strip_md(text)[:4096],
        reply_markup=keyboard,
        parse_mode=None,
    )


async def send_question(chat_id: int, step: str, data: dict[str, Any]) -> None:
    if step == "audience_custom":
        await send_text(
            chat_id,
            "Напиши аудиторию одним сообщением. Например: «владельцы оптовых компаний из Оренбурга».",
            {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "smm:back"}]]},
        )
        return
    text, keyboard = render_question(step, data)
    await send_text(chat_id, text, keyboard)


async def send_summary(chat_id: int, data: dict[str, Any]) -> None:
    text, keyboard = render_summary(data)
    await send_text(chat_id, text, keyboard)


async def start(chat_id: int, message: str = "") -> None:
    if not is_admin(chat_id):
        return
    data = new_session_data()
    session = await start_session(chat_id, "goal", data)
    intro = (
        "🧠 **AI-Маркетолог**\n\n"
        "Соберу бриф, стратегию, текст и визуал. 7 коротких шагов — можно пропускать и возвращаться.\n\n"
        "Начнём с цели."
    )
    await send_text(chat_id, intro + ("\n\n" + message if message else ""))
    await send_question(chat_id, "goal", data)


async def load_session(chat_id: int) -> dict[str, Any] | None:
    return await get_active_session(chat_id)


async def apply_and_advance(chat_id: int, session: dict[str, Any], step: str, value: str) -> None:
    data = dict(session.get("data") or {})
    try:
        data, nxt = apply_answer(data, step, value)
    except ValueError:
        await send_text(chat_id, "Не понял ответ. Попробуй ещё раз.")
        await send_question(chat_id, step, data)
        return
    await update_session(session["id"], nxt, data, "active")
    if nxt == "audience_custom":
        await send_question(chat_id, "audience_custom", data)
    elif nxt == "summary":
        await send_summary(chat_id, data)
    else:
        await send_question(chat_id, nxt, data)


async def handle_asset(chat_id: int, update: dict[str, Any], session: dict[str, Any]) -> None:
    message = update.get("message") or {}
    data = dict(session.get("data") or {})
    assets = list(data.get("assets") or [])
    if len(assets) >= settings.max_images:
        await send_text(chat_id, f"Уже {settings.max_images} файлов — больше не возьму. Нажми «Готово».")
        return

    file_id = ""
    kind = "photo"
    name = ""
    if message.get("photo"):
        sizes = message["photo"]
        file_id = max(sizes, key=lambda x: int(x.get("file_size") or 0)).get("file_id", "")
    elif message.get("document"):
        doc = message["document"]
        mime = (doc.get("mime_type") or "").lower()
        if not mime.startswith("image/"):
            await send_text(chat_id, "Это не картинка. Пришли фото, скрин или ссылку.")
            return
        file_id = doc.get("file_id", "")
        name = doc.get("file_name") or ""
        kind = "screenshot" if ("screen" in name.lower() or mime == "image/png") else "photo"

    if file_id:
        try:
            raw = await tg().get_file(file_id)
            asset = store_image(
                raw,
                chat_id=chat_id,
                session_id=int(session["id"]),
                kind=kind,
                source="telegram",
                original_name=name,
            )
            assets.append(asset)
        except Exception as exc:
            log.warning("telegram image failed: %s", exc)
            await send_text(chat_id, "Не смог скачать файл. Попробуй ещё раз или пришли ссылку.")
            return
    else:
        await send_text(chat_id, "Не вижу картинку. Пришли фото/скрин или ссылку.")
        return

    data["assets"] = assets
    await update_session(session["id"], data=data, status="active")
    if not data.get("assets_ack"):
        data["assets_ack"] = True
        await update_session(session["id"], data=data)
        await send_text(
            chat_id,
            f"Принял: {len(assets)} файл(а/ов). Пришли ещё или нажми «✅ Готово».",
        )


async def handle_visual_text(chat_id: int, text: str, session: dict[str, Any]) -> None:
    data = dict(session.get("data") or {})
    low = text.strip().lower()
    if low in ("готово", "done", "хватит", "всё", "все"):
        if data.get("editing") == "visual":
            data["editing"] = ""
            await update_session(session["id"], "summary", data, "active")
            await send_summary(chat_id, data)
        else:
            await update_session(session["id"], "summary", data, "active")
            await send_summary(chat_id, data)
        return
    if low in ("без фото", "арт", "сделай арт", "no photo"):
        data["assets"] = []
        data["editing"] = ""
        await update_session(session["id"], "summary", data, "active")
        await send_summary(chat_id, data)
        return

    urls = re.findall(r"https?://[^\s]+", text)
    if urls:
        assets = list(data.get("assets") or [])
        added = 0
        for url in urls:
            if len(assets) >= settings.max_images:
                break
            try:
                asset = await store_remote_image_async(
                    url,
                    chat_id=chat_id,
                    session_id=int(session["id"]),
                    kind="photo",
                )
                assets.append(asset)
                added += 1
            except Exception as exc:
                log.warning("url image failed: %s", exc)
        data["assets"] = assets
        await update_session(session["id"], data=data, status="active")
        if added:
            await send_text(
                chat_id,
                f"Добавил {added} картинку(и). Всего: {len(assets)}. Ещё или «✅ Готово».",
            )
        else:
            await send_text(chat_id, "Не смог загрузить картинку по ссылке. Попробуй другую или пришли файл.")
        return

    # Free text on the visual step = constraints / notes.
    data["constraints"] = ((data.get("constraints") or "") + "\n" + text).strip()[:600]
    await update_session(session["id"], data=data)
    await send_text(
        chat_id,
        "Записал как ограничение. Пришли ещё фото/ссылку или нажми «✅ Готово».",
    )


async def handle_message(chat_id: int, update: dict[str, Any]) -> None:
    message = update.get("message") or {}
    text = (message.get("text") or "").strip()
    if text.startswith("/test_smm"):
        await run_test(chat_id)
        return
    if text.startswith("/smm"):
        await start(chat_id, text.replace("/smm", "", 1).strip())
        return
    if text.startswith("/cancel"):
        await cancel_current(chat_id)
        return

    session = await load_session(chat_id)
    if not session:
        await send_text(chat_id, "Бриф не найден. Начни с меню «🧠 AI-Маркетолог».")
        return

    data = dict(session.get("data") or {})
    step = session.get("step") or "goal"

    if message.get("photo") or message.get("document") or message.get("media_group_id"):
        if step != "visual":
            await send_text(chat_id, "Сейчас не этап визуала. Нажми «✏️ Изменить» → «🖼 Визуал», если хочешь заменить фото.")
            return
        await handle_asset(chat_id, update, session)
        return

    if not text:
        await send_text(chat_id, "Пришли текст, фото или нажми кнопку.")
        return

    if step == "visual":
        await handle_visual_text(chat_id, text, session)
        return
    if step == "audience_custom":
        data["audience"] = text[:160]
        data.pop("audience_custom", None)
        await update_session(session["id"], "topic", data, "active")
        await send_question(chat_id, "topic", data)
        return
    if step == "summary":
        await send_text(chat_id, "Бриф готов. Нажми «▶️ Собрать пост» или «✏️ Изменить».")
        return

    await apply_and_advance(chat_id, session, step, text)


async def handle_callback(chat_id: int, cq: dict[str, Any]) -> None:
    callback_id = cq.get("id") or ""
    action, arg = parse_callback(cq.get("data") or "")
    if callback_id:
        await tg().answer_callback(callback_id)

    if action == "start":
        await start(chat_id)
        return
    if action == "ignore":
        return

    session = await load_session(chat_id)
    if not session:
        await send_text(chat_id, "Бриф устарел. Начни заново: меню «🧠 AI-Маркетолог».")
        return
    data = dict(session.get("data") or {})
    step = session.get("step") or "goal"

    try:
        if action in ("goal", "aud", "fmt", "cta"):
            if action == "goal" and step != "goal":
                await send_question(chat_id, step, data)
                return
            value = arg
            if action == "aud":
                value = "custom" if arg == "custom" else arg
                if arg == "custom":
                    data["audience_custom"] = True
                    await update_session(session["id"], "audience_custom", data, "active")
                    await send_question(chat_id, "audience_custom", data)
                    return
            await apply_and_advance(chat_id, session, step, value)

        elif action == "skip":
            if step == "proof":
                await apply_and_advance(chat_id, session, "proof", "skip")
            elif step == "visual":
                data["assets"] = []
                data["editing"] = ""
                await update_session(session["id"], "summary", data, "active")
                await send_summary(chat_id, data)
            else:
                await send_question(chat_id, step, data)

        elif action == "back":
            if step == "audience_custom":
                prev = "audience"
            else:
                idx = STEPS.index(step) if step in STEPS else 0
                prev = STEPS[max(0, idx - 1)]
            await update_session(session["id"], prev, data, "active")
            await send_question(chat_id, prev, data)

        elif action == "summary":
            await update_session(session["id"], "summary", data, "active")
            await send_summary(chat_id, data)

        elif action == "edit" and not arg:
            text, keyboard = render_edit_menu(data)
            await send_text(chat_id, text, keyboard)

        elif action == "edit" and arg:
            field = arg
            if field == "channels":
                await update_session(session["id"], "summary", data, "active")
                await send_summary(chat_id, data)
                return
            if field == "visual":
                data["editing"] = "visual"
                await update_session(session["id"], "visual", data, "active")
                await send_question(chat_id, "visual", data)
                return
            data["editing"] = field
            await update_session(session["id"], field, data, "active")
            await send_question(chat_id, field, data)

        elif action == "channel":
            data = toggle_channel(data, arg)
            await update_session(session["id"], "summary", data, "active")
            await send_summary(chat_id, data)

        elif action == "visual_done":
            data["editing"] = ""
            await update_session(session["id"], "summary", data, "active")
            await send_summary(chat_id, data)

        elif action == "visual_auto":
            data["assets"] = []
            data["editing"] = ""
            await update_session(session["id"], "summary", data, "active")
            await send_summary(chat_id, data)

        elif action == "visual_clear":
            data["assets"] = []
            await update_session(session["id"], "visual", data, "active")
            await send_question(chat_id, "visual", data)

        elif action == "gen":
            await run_generation(chat_id, session)

        elif action == "variant":
            await run_variant(chat_id, int(arg or 0))
        elif action == "approve":
            await run_publish(chat_id, int(arg or 0))
        elif action == "schedule":
            await run_schedule(chat_id, int(arg or 0))
        elif action == "reject":
            await run_reject(chat_id, int(arg or 0))
        elif action == "brief":
            await run_brief(chat_id, int(arg or 0))
        elif action == "cancel":
            await cancel_current(chat_id)
        else:
            await send_text(chat_id, "Кнопка устарела. Открой меню «🧠 AI-Маркетолог».")

    except Exception as exc:
        log.error("callback failed: %s", traceback.format_exc())
        await send_text(chat_id, f"Ошибка: {str(exc)[:300]}. Можно нажать «Отмена» и начать заново.")


async def cancel_current(chat_id: int) -> None:
    session = await load_session(chat_id)
    if session:
        data = session.get("data") or {}
        try:
            delete_session_files(chat_id, int(session["id"]))
        except Exception:
            pass
        await close_session(session["id"], "cancelled")
    await send_text(chat_id, "Ок, отменил. Когда будет новый пост — заходи в «🧠 AI-Маркетолог».")


async def run_generation(chat_id: int, session: dict[str, Any]) -> None:
    data = dict(session.get("data") or {})
    if not data.get("topic"):
        await send_text(chat_id, "Нет темы. Нажми «✏️ Изменить» → «🧠 Тема».")
        return
    brief = brief_from_session(data)
    assets = list(data.get("assets") or [])
    channels = list(data.get("channels") or ["telegram"])
    existing_draft = int(data.get("draft_id") or 0) or None

    await update_session(session["id"], data=data, status="generating")
    await send_text(chat_id, "🧠 Собираю стратегию…")

    style_rules = await get_style_rules()
    recent = await get_recent_posts(3)
    last_progress = {"text": "🧠 Собираю стратегию…"}

    async def progress(text: str) -> None:
        if text != last_progress["text"]:
            last_progress["text"] = text
            await send_text(chat_id, text)

    try:
        draft = await generate_post(
            chat_id=chat_id,
            session_id=int(session["id"]),
            brief=brief,
            assets=assets,
            style_rules=style_rules,
            recent_posts=recent,
            channels=channels,
            status="draft",
            draft_id=existing_draft,
            progress=progress,
        )
    except Exception as exc:
        log.error("generation failed: %s", traceback.format_exc())
        await update_session(session["id"], "summary", data, "active")
        await send_text(
            chat_id,
            "Не смог собрать пост: " + str(exc)[:300] + "\n\nМожно попробовать ещё раз кнопкой «Собрать».",
        )
        return

    data["draft_id"] = draft["id"]
    data["headline"] = draft.get("headline", "")
    data["strategy"] = draft.get("strategy_full") or {}
    await update_session(session["id"], "summary", data, "preview")

    cover = Path(draft["cover_path"]) if draft.get("cover_path") else None
    await send_preview(
        chat_id=chat_id,
        draft=draft,
        cover_path=cover,
        assets=assets,
        test=False,
    )


async def _draft_material(draft: dict[str, Any]) -> tuple[Path | None, list[dict[str, Any]]]:
    images = draft.get("images") or {}
    assets = list(images.get("assets") or [])
    cover_rel = images.get("cover") or ""
    cover = None
    if cover_rel:
        try:
            candidate = absolute_path(cover_rel)
            if candidate.exists():
                cover = candidate
        except Exception:
            cover = None
    return cover, assets


async def run_variant(chat_id: int, draft_id: int) -> None:
    draft = await get_draft(draft_id)
    if not draft:
        await send_text(chat_id, "Черновик не найден.")
        return
    if draft.get("status") in ("published", "smm_scheduled"):
        await send_text(chat_id, "Этот пост уже опубликован или в очереди — новый вариант не делаю.")
        return
    session = await load_session(chat_id)
    if not session:
        await send_text(chat_id, "Бриф не найден. Начни заново из меню.")
        return
    brief = draft.get("brief") or brief_from_session(session.get("data") or {})
    _, assets = await _draft_material(draft)
    channels = [c for c in (draft.get("channels") or "telegram").split(",") if c]
    await update_session(session["id"], status="generating")
    await send_text(chat_id, "🔁 Собираю другой вариант…")
    style_rules = await get_style_rules()
    recent = await get_recent_posts(3)
    try:
        variant = await regenerate_variant(
            draft_id=draft_id,
            chat_id=chat_id,
            session_id=int(session["id"]),
            brief=brief,
            assets=assets,
            style_rules=style_rules,
            recent_posts=recent,
            channels=channels,
        )
    except Exception as exc:
        log.error("variant failed: %s", traceback.format_exc())
        await update_session(session["id"], status="preview")
        await send_text(chat_id, f"Не получилось: {str(exc)[:300]}")
        return
    data = dict(session.get("data") or {})
    data["draft_id"] = draft_id
    await update_session(session["id"], "summary", data, "preview")
    cover = Path(variant["cover_path"]) if variant.get("cover_path") else None
    await send_preview(chat_id=chat_id, draft=variant, cover_path=cover, assets=assets, test=False)


async def run_publish(chat_id: int, draft_id: int) -> None:
    draft = await get_draft(draft_id)
    if not draft:
        await send_text(chat_id, "Черновик не найден.")
        return
    if draft.get("status") == "test":
        await send_text(chat_id, "Это тестовый черновик — публикация отключена.")
        return
    cover, assets = await _draft_material(draft)
    channels = [c for c in (draft.get("channels") or "").split(",") if c]
    if not channels:
        await send_text(chat_id, "Не выбраны каналы.")
        return
    await send_text(chat_id, "🚀 Публикую: " + ", ".join(CHANNEL_TITLES.get(c, c) for c in channels))
    result = await publish_draft(draft=draft, cover_path=cover, assets=assets, channels=channels)
    results = result.get("results") or {}
    ok_lines = []
    for channel, info in results.items():
        if info.get("ok") or info.get("url"):
            line = f"✅ {CHANNEL_TITLES.get(channel, channel)}"
            if info.get("url"):
                line += f": {info['url']}"
            ok_lines.append(line)
        else:
            ok_lines.append(f"⚠️ {CHANNEL_TITLES.get(channel, channel)}: {str(info.get('error'))[:200]}")
    await send_text(chat_id, "\n".join(ok_lines) or "Не удалось опубликовать.")
    if any(info.get("ok") or info.get("url") for info in results.values()):
        session = await load_session(chat_id)
        if session:
            try:
                delete_session_files(chat_id, int(session["id"]))
            except Exception:
                pass
            await close_session(session["id"], "done")
        await log_feedback(draft_id, "approve")
    else:
        await send_text(chat_id, "Ни один канал не принял пост. Черновик сохранил — можно попробовать ещё раз.")


async def run_schedule(chat_id: int, draft_id: int) -> None:
    draft = await get_draft(draft_id)
    if not draft:
        await send_text(chat_id, "Черновик не найден.")
        return
    await mark_scheduled(draft_id)
    await log_feedback(draft_id, "schedule")
    session = await load_session(chat_id)
    if session:
        await close_session(session["id"], "scheduled")
    await send_text(chat_id, "⏰ Поставил в очередь на завтра 09:00 (Екатеринбург).")


async def run_reject(chat_id: int, draft_id: int) -> None:
    draft = await get_draft(draft_id)
    if not draft:
        await send_text(chat_id, "Черновик не найден.")
        return
    await mark_status(draft_id, "rejected")
    await log_feedback(draft_id, "reject")
    session = await load_session(chat_id)
    if session:
        try:
            delete_session_files(chat_id, int(session["id"]))
        except Exception:
            pass
        await close_session(session["id"], "rejected")
    await send_text(chat_id, "❌ Отменил. Ничего не публикую.")


async def run_brief(chat_id: int, draft_id: int) -> None:
    draft = await get_draft(draft_id)
    if not draft:
        await send_text(chat_id, "Черновик не найден.")
        return
    if draft.get("status") in ("published", "test"):
        await send_text(chat_id, "Этот черновик уже нельзя редактировать.")
        return
    brief = draft.get("brief") or {}
    _, assets = await _draft_material(draft)
    data = new_session_data()
    data.update(
        {
            "goal": brief.get("goal_code") or "",
            "audience": brief.get("audience") or "",
            "topic": brief.get("topic") or draft.get("topic") or "",
            "proof": brief.get("proof") or "",
            "format": brief.get("format_code") or "",
            "cta": brief.get("cta_code") or "",
            "constraints": brief.get("constraints") or "",
            "assets": assets,
            "channels": [c for c in (draft.get("channels") or "telegram").split(",") if c]
            or ["telegram"],
            "draft_id": draft_id,
        }
    )
    session = await load_session(chat_id)
    if session:
        await update_session(session["id"], "summary", data, "preview")
    else:
        session = await start_session(chat_id, "summary", data, "preview")
    await send_summary(chat_id, data)


async def run_test(chat_id: int) -> None:
    await send_text(chat_id, "🧪 Запускаю тихий тест: бриф → стратегия → текст → карточка → превью. Публикации не будет.")
    report = await execute_test(chat_id)
    lines = ["🧪 **Отчёт теста**", ""]
    for key, value in report.get("checks", {}).items():
        lines.append(f"{'✅' if value else '⚠️'} {key}")
    lines += [
        "",
        f"draft_id: {report.get('draft_id')}",
        f"quality: {report.get('quality_score')}",
        f"cover: {'да' if report.get('cover') else 'нет'}",
        f"images: {report.get('images')}",
        "",
        "Публикация: отключена.",
    ]
    await send_text(chat_id, "\n".join(lines))


async def execute_test(chat_id: int | None = None) -> dict[str, Any]:
    chat_id = chat_id or settings.admin_chat_id
    session_id = int(time.time())
    assets: list[dict[str, Any]] = []
    palettes = (
        ((18, 40, 75), (40, 140, 190)),
        ((45, 28, 20), (220, 120, 50)),
        ((20, 50, 42), (70, 180, 120)),
    )
    for i, (start, end) in enumerate(palettes, start=1):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (1200, 900), start)
        draw = ImageDraw.Draw(image, "RGBA")
        for y in range(900):
            t = y / 899
            color = tuple(int(start[c] * (1 - t) + end[c] * t) for c in range(3))
            draw.line((0, y, 1200, y), fill=color)
        draw.ellipse((700, -180, 1300, 420), fill=(255, 255, 255, 32))
        draw.ellipse((-180, 520, 380, 1080), fill=(255, 180, 80, 36))
        draw.rounded_rectangle((80, 620, 620, 820), radius=28, fill=(6, 16, 28, 150))
        draw.rectangle((110, 660, 500, 680), fill=(255, 255, 255, 180))
        draw.rectangle((110, 700, 420, 714), fill=(255, 255, 255, 100))
        draw.rectangle((110, 730, 320, 744), fill=(255, 255, 255, 70))
        draw.text((90, 80), f"TEST IMAGE {i}", fill=(255, 255, 255, 210))
        import io

        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=92)
        assets.append(
            store_image(
                buf.getvalue(),
                chat_id=chat_id,
                session_id=session_id,
                kind="photo",
                source="test",
                original_name=f"test-{i}.jpg",
            )
        )
    brief = {
        "goal": "Заявки и лиды",
        "goal_code": "leads",
        "audience": "Владельцы бизнеса",
        "topic": "Тест AI-SMM: автоматизировали заявки из Telegram в Битрикс24, первый ответ ускорился с 6 часов до 3 минут.",
        "proof": "3 менеджера, 400 заявок в месяц, первый ответ 6 ч → 3 мин.",
        "format": "Кейс с цифрами",
        "format_code": "case",
        "cta": "Написать в личку",
        "cta_code": "dm",
        "constraints": "Не выдумывать цифры.",
        "channels": ["telegram"],
        "images_count": len(assets),
    }
    checks: dict[str, bool] = {"images": len(assets) == 3}
    draft_id = 0
    quality = None
    cover = None
    try:
        style_rules = await get_style_rules()
        recent = await get_recent_posts(3)
        draft = await generate_post(
            chat_id=chat_id,
            session_id=session_id,
            brief=brief,
            assets=assets,
            style_rules=style_rules,
            recent_posts=recent,
            channels=["telegram"],
            status="test",
        )
        draft_id = draft["id"]
        quality = draft.get("quality_score")
        cover = draft.get("cover_path")
        checks["strategy"] = bool(draft.get("strategy_full"))
        checks["copy"] = bool(draft.get("tg")) and bool(draft.get("headline"))
        checks["cover"] = bool(cover and Path(cover).exists())
        checks["quality_scores"] = bool(draft.get("scores"))
        await send_preview(
            chat_id=chat_id,
            draft=draft,
            cover_path=Path(cover) if cover else None,
            assets=assets,
            test=True,
        )
        checks["telegram_preview"] = True
    except Exception as exc:
        log.error("test generation failed: %s", traceback.format_exc())
        checks["generation"] = False
        await send_text(chat_id, f"Тест упал на генерации: {str(exc)[:300]}")

    # Dzen dry-run in a temp dir: validates article + feed without touching production.
    try:
        from dzen import publish_article

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "cards").mkdir()
            (tmp_path / "articles").mkdir()
            (tmp_path / "index.html").write_text("<ul></ul>", encoding="utf-8")
            (tmp_path / "feed.xml").write_text(
                '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
                "<language>ru</language></channel></rss>",
                encoding="utf-8",
            )
            dzen_report = publish_article(
                draft={
                    "id": 999999,
                    "dzen_title": "Тестовая статья AI-SMM",
                    "dzen_text": "Первый абзац.\n\nВторой абзац с проверкой.",
                    "tg": "Текст",
                },
                cover_path=Path(cover) if cover else None,
                extra_images=[],
                base_dir=tmp_path,
                public_base="https://example.test",
                dry_run=False,
            )
            checks["dzen_article"] = (tmp_path / dzen_report["article"]).exists()
            checks["dzen_feed"] = "Тестовая статья" in (tmp_path / "feed.xml").read_text(encoding="utf-8")
    except Exception as exc:
        log.warning("dzen test failed: %s", exc)
        checks["dzen_article"] = False
        checks["dzen_feed"] = False

    checks["max_config"] = bool(settings.max_bot_token and settings.max_chat_id)
    checks["vk_config"] = bool(settings.vk_token and settings.vk_group_id)
    checks["no_public_publish"] = True
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "draft_id": draft_id,
        "quality_score": quality,
        "cover": cover,
        "images": len(assets),
    }


@app.post("/event")
async def event(request: Request) -> JSONResponse:
    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")
    try:
        handled = await should_handle(update)
    except Exception:
        log.error("should_handle failed: %s", traceback.format_exc())
        handled = False
    if handled:
        task = asyncio.create_task(process_update(update))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return JSONResponse({"handled": handled})


async def process_update(update: dict[str, Any]) -> None:
    chat_id = message_chat_id(update)
    if not is_admin(chat_id):
        return
    async with lock_for(chat_id):
        try:
            cq = update.get("callback_query")
            if cq:
                await handle_callback(chat_id, cq)
            else:
                await handle_message(chat_id, update)
        except Exception:
            log.error("update failed: %s", traceback.format_exc())
            try:
                await send_text(chat_id, "Внутренняя ошибка. Попробуй ещё раз или начни заново.")
            except Exception:
                pass


async def scheduler_loop() -> None:
    while True:
        try:
            rows = await due_scheduled()
            for row in rows:
                draft = await get_draft(int(row["id"]))
                if not draft:
                    continue
                cover, assets = await _draft_material(draft)
                channels = [c for c in (draft.get("channels") or "").split(",") if c]
                result = await publish_draft(draft=draft, cover_path=cover, assets=assets, channels=channels)
                results = result.get("results") or {}
                lines = []
                for channel, info in results.items():
                    if info.get("ok") or info.get("url"):
                        lines.append(f"✅ {CHANNEL_TITLES.get(channel, channel)}")
                    else:
                        lines.append(f"⚠️ {CHANNEL_TITLES.get(channel, channel)}: {str(info.get('error'))[:120]}")
                if any(info.get("ok") or info.get("url") for info in results.values()):
                    await mark_status(int(draft["id"]), "published")
                    images = draft.get("images") or {}
                    if images.get("chat_id") and images.get("session_id"):
                        try:
                            delete_session_files(int(images["chat_id"]), int(images["session_id"]))
                        except Exception:
                            pass
                else:
                    await mark_status(int(draft["id"]), "smm_error")
                if settings.admin_chat_id:
                    await send_text(
                        settings.admin_chat_id,
                        f"⏰ Опубликовал по расписанию #{draft['id']}\n" + "\n".join(lines),
                    )
        except Exception:
            log.error("scheduler failed: %s", traceback.format_exc())
        await asyncio.sleep(60)


@app.get("/health")
async def health() -> dict[str, Any]:
    checks = {
        "db": False,
        "cardrender": False,
        "telegram": bool(settings.telegram_relay_base and settings.telegram_bot_token),
        "deepseek": bool(settings.deepseek_api_key),
        "max": bool(settings.max_bot_token and settings.max_chat_id),
        "vk": bool(settings.vk_token and settings.vk_group_id),
        "dzen": settings.dzen_dir.exists(),
    }
    try:
        async with pool().acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["db"] = True
    except Exception:
        pass
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{settings.cardrender_url}/health")
            checks["cardrender"] = response.status_code == 200
    except Exception:
        pass
    return {"ok": all(checks.values()), "checks": checks}


@app.post("/test")
async def test_endpoint(
    request: Request,
    x_test_token: str = Header(default=""),
) -> dict[str, Any]:
    if settings.test_token and x_test_token != settings.test_token:
        raise HTTPException(status_code=403, detail="forbidden")
    if not settings.admin_chat_id:
        raise HTTPException(status_code=400, detail="admin chat is not configured")
    async with lock_for(settings.admin_chat_id):
        return await execute_test(settings.admin_chat_id)


@app.get("/")
async def root() -> dict[str, Any]:
    return {"service": "oneclick-ai-smm", "ok": True}
