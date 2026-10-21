from __future__ import annotations

import io
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from config import settings

log = logging.getLogger(__name__)

MAX_SIDE = 1800


def _session_dir(chat_id: int, session_id: int) -> Path:
    path = settings.uploads_dir / str(chat_id) / str(session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def absolute_path(rel: str) -> Path:
    root = settings.uploads_dir.resolve()
    path = (settings.uploads_dir / rel).resolve()
    if root not in path.parents and path != root:
        raise ValueError("path traversal")
    return path


def media_url(rel: str) -> str:
    clean = rel.replace(chr(92), "/")
    return f"{settings.media_base_url}/{clean}"


def _normalize_image(data: bytes, kind: str) -> tuple[bytes, int, int, str]:
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
    except UnidentifiedImageError as exc:
        raise ValueError("not an image") from exc

    if image.mode in ("RGBA", "P", "LA"):
        background = Image.new("RGB", image.size, (10, 16, 26))
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    width, height = image.size

    out = io.BytesIO()
    if kind == "screenshot":
        image.save(out, format="PNG", optimize=True)
        ext, mime = ".png", "image/png"
    else:
        image.save(out, format="JPEG", quality=88, optimize=True, progressive=True)
        ext, mime = ".jpg", "image/jpeg"
    return out.getvalue(), width, height, ext + "|" + mime


def store_image(
    data: bytes,
    *,
    chat_id: int,
    session_id: int,
    kind: str = "photo",
    source: str = "telegram",
    original_name: str = "",
) -> dict[str, Any]:
    if len(data) > settings.max_image_bytes:
        raise ValueError("image too large")
    normalized, width, height, meta = _normalize_image(data, kind)
    ext, mime = meta.split("|", 1)
    name = uuid.uuid4().hex + ext
    directory = _session_dir(chat_id, session_id)
    path = directory / name
    path.write_bytes(normalized)
    rel = f"{chat_id}/{session_id}/{name}"
    return {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "path": rel,
        "url": media_url(rel),
        "width": width,
        "height": height,
        "mime": mime,
        "source": source,
        "original_name": original_name[:160],
    }


async def download_remote(url: str) -> bytes:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("unsupported url")
    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0), follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").lower()
            if content_type and not content_type.startswith("image/"):
                raise ValueError("url is not an image")
            chunks = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > settings.max_image_bytes:
                    raise ValueError("image too large")
                chunks.append(chunk)
            return b"".join(chunks)


async def store_remote_image_async(
    url: str,
    *,
    chat_id: int,
    session_id: int,
    kind: str = "photo",
) -> dict[str, Any]:
    data = await download_remote(url)
    return store_image(
        data,
        chat_id=chat_id,
        session_id=session_id,
        kind=kind,
        source="url",
    )


def delete_session_files(chat_id: int, session_id: int) -> None:
    path = settings.uploads_dir / str(chat_id) / str(session_id)
    if not path.exists():
        return
    for item in path.rglob("*"):
        if item.is_file():
            item.unlink(missing_ok=True)
    for item in sorted(path.rglob("*"), reverse=True):
        if item.is_dir():
            item.rmdir()
    path.rmdir()


def cover_path(chat_id: int, session_id: int) -> Path:
    return _session_dir(chat_id, session_id) / "cover.png"
