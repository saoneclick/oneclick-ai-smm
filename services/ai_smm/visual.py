from __future__ import annotations

import logging
from typing import Any

import httpx

from config import settings
from media import cover_path, media_url

log = logging.getLogger(__name__)

ARTS = {"orbit", "grid", "terminal", "network", "chart", "guard", "photo", "auto"}
THEMES = {"cyan", "violet", "emerald", "amber", "auto"}
LAYOUTS = {"poster", "split", "stage", "stack", "metrics", "auto"}

GOAL_TAGS = {
    "leads": "КЕЙС",
    "reach": "ИНСАЙТ",
    "trust": "ЭКСПЕРТИЗА",
    "launch": "АНОНС",
    "partners": "КОМАНДА",
}


def _tag_for(draft: dict[str, Any]) -> str:
    brief = draft.get("brief") or {}
    goal = brief.get("goal_code") or ""
    fmt = brief.get("format_code") or ""
    if goal == "leads":
        return "КЕЙС" if fmt == "case" else "ПОЛЬЗА"
    if goal in GOAL_TAGS:
        return GOAL_TAGS[goal]
    return "AI-АВТОМАТИЗАЦИЯ"


def _layout_for(draft: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    visual = draft.get("visual") or {}
    requested = (visual.get("layout") or "auto").lower()
    if assets:
        first = assets[0]
        width = int(first.get("width") or 1)
        height = int(first.get("height") or 1)
        screenshot = first.get("kind") == "screenshot"
        if requested in LAYOUTS and requested not in ("auto", "metrics"):
            return requested
        if screenshot or height > width * 1.08:
            return "split"
        return "stage"
    if requested in LAYOUTS and requested not in ("auto", "metrics"):
        return requested
    return "poster"


def _clean(value: str, allowed: set[str], fallback: str) -> str:
    value = (value or "").lower()
    return value if value in allowed and value != "auto" else fallback


async def render_cover(
    *,
    chat_id: int,
    session_id: int,
    draft: dict[str, Any],
    assets: list[dict[str, Any]],
) -> Path | None:
    visual = draft.get("visual") or {}
    art = _clean(visual.get("art", "auto"), ARTS, "auto")
    theme = _clean(visual.get("theme", "auto"), THEMES, "")
    layout = _layout_for(draft, assets)
    payload: dict[str, Any] = {
        "headline": draft.get("headline") or draft.get("topic") or "One Click",
        "tag": _tag_for(draft),
        "subtitle": draft.get("subtitle") or "",
        "bullets": (draft.get("bullets") or [])[:3],
        "layout": layout,
    }
    if assets:
        payload["art"] = "photo"
        payload["image_url"] = assets[0]["url"]
    elif art != "auto":
        payload["art"] = art
    if theme:
        payload["theme"] = theme

    url = f"{settings.cardrender_url}/card"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.content
            if not data or not data.startswith(b"\x89PNG"):
                raise RuntimeError("cardrender returned non-PNG")
    except Exception as exc:
        log.warning("cover render failed: %s", exc)
        return None

    path = cover_path(chat_id, session_id)
    path.write_bytes(data)
    return path


def cover_url(chat_id: int, session_id: int) -> str:
    return media_url(f"{chat_id}/{session_id}/cover.png")
