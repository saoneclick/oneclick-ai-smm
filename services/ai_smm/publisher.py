from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from config import settings
from db import mark_published
from dzen import publish_article
from media import absolute_path
from telegram import Telegram, TelegramError

log = logging.getLogger(__name__)

MAX_API = "https://botapi.max.ru"
VK_API = "https://api.vk.com/method"


def _asset_path(asset: dict[str, Any]) -> Path:
    return absolute_path(str(asset.get("path") or ""))


def _preview_files(
    cover_path: Path | None, assets: list[dict[str, Any]]
) -> list[Path]:
    files: list[Path] = []
    if cover_path and cover_path.exists():
        files.append(cover_path)
        files.extend(_asset_path(a) for a in assets[1:] if a.get("path"))
    else:
        files.extend(_asset_path(a) for a in assets if a.get("path"))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in files:
        key = str(path.resolve())
        if key not in seen and path.exists():
            seen.add(key)
            unique.append(path)
    return unique[:10]


async def send_preview(
    *,
    chat_id: int,
    draft: dict[str, Any],
    cover_path: Path | None,
    assets: list[dict[str, Any]],
    test: bool = False,
) -> None:
    telegram = Telegram()
    draft_id = int(draft.get("id") or 0)
    files = _preview_files(cover_path, assets)
    prefix = "🧪 ТЕСТ · " if test else f"📝 Черновик #{draft_id} · "
    short = (draft.get("headline") or draft.get("topic") or "")[:80]
    try:
        if not files:
            await telegram.send_message(chat_id, f"{prefix}{short}\n\n{draft.get('tg') or ''}")
        elif len(files) == 1:
            await telegram.send_photo(
                chat_id,
                files[0],
                caption=f"{prefix}{short}",
                parse_mode=None,
            )
        else:
            await telegram.send_media_group(
                chat_id,
                files,
                caption=f"{prefix}{short} · {len(files)} фото",
                parse_mode=None,
            )

        text = draft.get("tg") or draft.get("topic") or ""
        if test:
            text = "🧪 ТЕСТ. Публикация отключена.\n\n" + text
        else:
            text = f"📝 Черновик #{draft_id}\n\n" + text
        rows = []
        if not test:
            rows.append([
                {"text": "✅ Опубликовать", "callback_data": f"smm:approve:{draft_id}"},
                {"text": "⏰ В очередь", "callback_data": f"smm:schedule:{draft_id}"},
            ])
            rows.append([
                {"text": "🔁 Другой вариант", "callback_data": f"smm:variant:{draft_id}"},
                {"text": "✏️ Изменить бриф", "callback_data": f"smm:brief:{draft_id}"},
            ])
            rows.append([
                {"text": "❌ Отмена", "callback_data": f"smm:reject:{draft_id}"},
            ])
        await telegram.send_message(
            chat_id,
            text[:4096],
            reply_markup={"inline_keyboard": rows} if rows else None,
            parse_mode=None,
        )
    finally:
        await telegram.close()


async def _publish_telegram(
    draft: dict[str, Any], cover_path: Path | None, assets: list[dict[str, Any]]
) -> dict[str, Any]:
    telegram = Telegram()
    try:
        files = _preview_files(cover_path, assets)
        text = (draft.get("tg") or draft.get("topic") or "").strip()
        chat_id = int(settings.telegram_channel_id or settings.admin_chat_id)
        if not files:
            result = await telegram.send_message(chat_id, text)
            return {"ok": True, "message_id": result.get("result", {}).get("message_id")}
        if len(files) == 1:
            result = await telegram.send_photo(chat_id, files[0], caption=text[:1024], parse_mode=None)
        else:
            result = await telegram.send_media_group(chat_id, files, caption=text[:1024], parse_mode=None)
        message = result.get("result")
        if isinstance(message, list):
            message = message[0] if message else {}
        return {"ok": True, "message_id": (message or {}).get("message_id")}
    finally:
        await telegram.close()


async def _upload_max(path: Path) -> str:
    headers = {"Authorization": settings.max_bot_token}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
        response = await client.post(
            f"{MAX_API}/uploads?type=image",
            headers=headers,
            content=b"",
        )
        response.raise_for_status()
        upload = response.json()
        data = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        response = await client.post(
            upload["url"],
            files={"data": (path.name, data, mime)},
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload["photos"]["0"]["token"])


async def _publish_max(
    draft: dict[str, Any], cover_path: Path | None, assets: list[dict[str, Any]]
) -> dict[str, Any]:
    if not settings.max_bot_token or not settings.max_chat_id:
        return {"ok": False, "error": "MAX is not configured"}
    files = _preview_files(cover_path, assets)
    attachments = []
    for path in files:
        token = await _upload_max(path)
        attachments.append({"type": "image", "payload": {"token": token}})
    body: dict[str, Any] = {"text": (draft.get("max") or draft.get("tg") or "")[:4000]}
    if attachments:
        body["attachments"] = attachments
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
        response = await client.post(
            f"{MAX_API}/messages",
            params={"chat_id": settings.max_chat_id},
            headers={"Authorization": settings.max_bot_token},
            json=body,
        )
        response.raise_for_status()
        payload = response.json()
        return {"ok": True, "message_id": (payload.get("message") or {}).get("body", {}).get("mid")}


async def _publish_vk(draft: dict[str, Any]) -> dict[str, Any]:
    if not settings.vk_token or not settings.vk_group_id:
        return {"ok": False, "error": "VK is not configured"}
    text = (draft.get("vk") or draft.get("tg") or "")[:700]
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        response = await client.post(
            f"{VK_API}/wall.post",
            data={
                "owner_id": f"-{settings.vk_group_id.lstrip('-')}",
                "from_group": 1,
                "message": text,
                "access_token": settings.vk_token,
                "v": settings.vk_api_version,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            return {"ok": False, "error": payload["error"]}
        return {"ok": True, "post_id": (payload.get("response") or {}).get("post_id")}


def _publish_dzen(
    draft: dict[str, Any], cover_path: Path | None, assets: list[dict[str, Any]]
) -> dict[str, Any]:
    extra = []
    for asset in assets[1:]:
        try:
            asset = dict(asset)
            asset["abs_path"] = str(_asset_path(asset))
            extra.append(asset)
        except Exception:
            continue
    return publish_article(
        draft=draft,
        cover_path=cover_path,
        extra_images=extra,
        base_dir=settings.dzen_dir,
        public_base=settings.dzen_public_base,
    )


async def publish_draft(
    *,
    draft: dict[str, Any],
    cover_path: Path | None,
    assets: list[dict[str, Any]],
    channels: list[str],
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    errors: list[str] = []
    if "telegram" in channels:
        try:
            results["telegram"] = await _publish_telegram(draft, cover_path, assets)
        except Exception as exc:
            errors.append(f"telegram: {exc}")
            results["telegram"] = {"ok": False, "error": str(exc)}
    if "max" in channels:
        try:
            results["max"] = await _publish_max(draft, cover_path, assets)
        except Exception as exc:
            errors.append(f"max: {exc}")
            results["max"] = {"ok": False, "error": str(exc)}
    if "vk" in channels:
        try:
            results["vk"] = await _publish_vk(draft)
        except Exception as exc:
            errors.append(f"vk: {exc}")
            results["vk"] = {"ok": False, "error": str(exc)}
    if "dzen" in channels:
        try:
            results["dzen"] = _publish_dzen(draft, cover_path, assets)
        except Exception as exc:
            errors.append(f"dzen: {exc}")
            results["dzen"] = {"ok": False, "error": str(exc)}

    if any((r or {}).get("ok") or (r or {}).get("url") for r in results.values()):
        await mark_published(
            int(draft["id"]),
            tg_message_id=results.get("telegram", {}).get("message_id"),
            vk_post_id=results.get("vk", {}).get("post_id"),
            max_message_id=results.get("max", {}).get("message_id"),
            dzen_url=results.get("dzen", {}).get("url"),
        )
    elif results:
        # keep the draft visible for retry; status is updated by the caller
        pass
    return {"results": results, "errors": errors}
