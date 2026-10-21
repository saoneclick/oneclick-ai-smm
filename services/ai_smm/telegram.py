from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from config import settings

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=20.0),
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _url(self, method: str) -> str:
        return f"{settings.telegram_relay_base}/bot{settings.telegram_bot_token}/{method}"

    async def _call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._client.post(self._url(method), **kwargs)
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text[:1000]}
        if response.status_code >= 400 or not payload.get("ok", False):
            raise TelegramError(f"{method}: {response.status_code} {payload}")
        return payload

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
        disable_preview: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096]}
        if reply_markup:
            body["reply_markup"] = reply_markup
        if parse_mode:
            body["parse_mode"] = parse_mode
        if disable_preview:
            body["link_preview_options"] = {"is_disabled": True}
        return await self._call("sendMessage", json=body)

    async def send_photo(
        self,
        chat_id: int,
        path: Path,
        caption: str = "",
        *,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = "HTML",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption[:1024]
        if reply_markup:
            import json

            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        if parse_mode:
            data["parse_mode"] = parse_mode
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        with path.open("rb") as fh:
            return await self._call(
                "sendPhoto",
                data=data,
                files={"photo": (path.name, fh.read(), mime)},
            )

    async def send_media_group(
        self,
        chat_id: int,
        paths: list[Path],
        caption: str = "",
        *,
        parse_mode: str | None = "HTML",
    ) -> dict[str, Any]:
        import json

        if not paths:
            raise TelegramError("sendMediaGroup: no files")
        paths = paths[:10]
        media = []
        files: dict[str, Any] = {}
        for i, path in enumerate(paths):
            item: dict[str, Any] = {"type": "photo", "media": f"attach://photo{i}"}
            if i == 0 and caption:
                item["caption"] = caption[:1024]
                if parse_mode:
                    item["parse_mode"] = parse_mode
            media.append(item)
            mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            files[f"photo{i}"] = (path.name, path.read_bytes(), mime)
        return await self._call(
            "sendMediaGroup",
            data={"chat_id": str(chat_id), "media": json.dumps(media, ensure_ascii=False)},
            files=files,
        )

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        body: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            body["text"] = text[:200]
        try:
            await self._call("answerCallbackQuery", json=body)
        except TelegramError as exc:
            log.warning("answerCallbackQuery failed: %s", exc)

    async def get_file(self, file_id: str) -> bytes:
        payload = await self._call("getFile", json={"file_id": file_id})
        file_path = payload.get("result", {}).get("file_path")
        if not file_path:
            raise TelegramError("getFile: no file_path")
        url = f"{settings.telegram_relay_base}/file/bot{settings.telegram_bot_token}/{file_path}"
        response = await self._client.get(url)
        response.raise_for_status()
        return response.content
