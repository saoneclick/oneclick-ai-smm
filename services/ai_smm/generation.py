from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from db import insert_draft, log_feedback, update_draft_content
from deepseek import DeepSeek
from media import media_url
from prompts import COPYWRITER_SYSTEM, STRATEGIST_SYSTEM, copy_user, strategy_user
from visual import render_cover

log = logging.getLogger(__name__)

Progress = Callable[[str], Awaitable[None]]


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _strip_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = text.replace("**", "").replace("__", "")
    return text.strip()


def _norm_hashtags(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    result = []
    for item in value or []:
        tag = str(item).strip().lstrip("#")
        if tag:
            result.append("#" + tag[:40])
    return result[:4]


def _quality(scores: Any) -> float | None:
    if not isinstance(scores, dict) or not scores:
        return None
    numbers = []
    for value in scores.values():
        try:
            numbers.append(float(value))
        except (TypeError, ValueError):
            continue
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 2)


async def generate_post(
    *,
    chat_id: int,
    session_id: int,
    brief: dict[str, Any],
    assets: list[dict[str, Any]],
    style_rules: list[str],
    recent_posts: list[str],
    channels: list[str],
    status: str = "draft",
    draft_id: int | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    ai = DeepSeek()
    try:
        if progress:
            await progress("🧠 Собираю стратегию…")
        strategy = await ai.json_chat(
            STRATEGIST_SYSTEM,
            strategy_user(brief, style_rules, recent_posts),
            temperature=0.8,
            max_tokens=4000,
        )

        if progress:
            await progress("✍️ Пишу и проверяю по чек-листу…")
        copy = await ai.json_chat(
            COPYWRITER_SYSTEM,
            copy_user(
                brief,
                strategy,
                style_rules,
                recent_posts,
                has_images=bool(assets),
                channels=channels,
            ),
            temperature=1.0,
            max_tokens=8000,
        )
    finally:
        await ai.close()

    headline = _strip_markdown(_text(copy.get("headline") or brief.get("topic"), 120))
    if not headline:
        headline = _text(brief.get("topic"), 80) or "One Click"
    subtitle = _strip_markdown(_text(copy.get("subtitle"), 220))
    if len(subtitle.split()) > 30:
        subtitle = " ".join(subtitle.split()[:30]).rstrip(".,;:") + "."
    bullets = [_strip_markdown(_text(b, 100)) for b in (copy.get("bullets") or [])[:3] if _text(b)]
    tg = _strip_markdown(_text(copy.get("tg") or brief.get("topic"), 900))
    vk = _strip_markdown(_text(copy.get("vk") or tg, 700))
    max_text = _strip_markdown(_text(copy.get("max") or tg, 700))
    dzen_title = _strip_markdown(_text(copy.get("dzen_title") or headline, 140))
    dzen_text = _strip_markdown(_text(copy.get("dzen_text") or tg, 5000))
    hashtags = _norm_hashtags(copy.get("hashtags"))
    if hashtags:
        tg = (tg + "\n\n" + " ".join(hashtags))[:900]
    visual = copy.get("visual") if isinstance(copy.get("visual"), dict) else {}
    alt_text = [str(x)[:300] for x in (copy.get("alt_text") or []) if str(x).strip()][:10]
    scores = copy.get("scores") if isinstance(copy.get("scores"), dict) else {}
    score = _quality(scores)

    draft = {
        "headline": headline,
        "subtitle": subtitle,
        "bullets": bullets,
        "tg": tg,
        "vk": vk,
        "max": max_text,
        "dzen_title": dzen_title,
        "dzen_text": dzen_text,
        "hashtags": hashtags,
        "cta_text": _text(copy.get("cta_text"), 120),
        "visual": visual,
        "alt_text": alt_text,
        "scores": scores,
        "self_review": _text(copy.get("self_review"), 500),
        "quality_score": score,
        "brief": brief,
    }

    if progress:
        await progress("🎨 Делаю карточку…")
    cover = await render_cover(
        chat_id=chat_id,
        session_id=session_id,
        draft=draft,
        assets=assets,
    )
    cover_rel = f"{chat_id}/{session_id}/{cover.name}" if cover else ""
    cover_url = media_url(cover_rel) if cover else ""
    images_payload = {
        "assets": assets,
        "cover": cover_rel,
        "cover_url": cover_url,
        "chat_id": chat_id,
        "session_id": session_id,
    }

    if draft_id is None:
        draft_id = await insert_draft(
            topic=_text(brief.get("topic"), 500),
            tg_text=tg,
            vk_text=vk,
            max_text=max_text,
            headline=headline,
            body=dzen_text,
            image_url=cover_url,
            channels=channels,
            brief=brief,
            strategy={"strategy": strategy, "copy": draft},
            images=images_payload,
            quality_score=score,
            goal=_text(brief.get("goal_code") or brief.get("goal"), 120),
            audience=_text(brief.get("audience"), 160),
            status=status,
        )
    else:
        await update_draft_content(
            draft_id,
            tg_text=tg,
            vk_text=vk,
            max_text=max_text,
            headline=headline,
            body=dzen_text,
            image_url=cover_url,
            strategy={"strategy": strategy, "copy": draft},
            images=images_payload,
            quality_score=score,
        )
    await log_feedback(draft_id, "generated", angle=_text(strategy.get("angle"), 200))
    draft["id"] = draft_id
    draft["cover_path"] = str(cover) if cover else ""
    draft["cover_url"] = cover_url
    draft["images"] = images_payload
    draft["brief"] = brief
    draft["strategy_full"] = strategy
    return draft


async def regenerate_variant(
    *,
    draft_id: int,
    chat_id: int,
    session_id: int,
    brief: dict[str, Any],
    assets: list[dict[str, Any]],
    style_rules: list[str],
    recent_posts: list[str],
    channels: list[str],
    progress: Progress | None = None,
) -> dict[str, Any]:
    variant = await generate_post(
        chat_id=chat_id,
        session_id=session_id,
        brief=brief,
        assets=assets,
        style_rules=style_rules,
        recent_posts=recent_posts,
        channels=channels,
        status="draft",
        draft_id=draft_id,
        progress=progress,
    )
    variant["id"] = draft_id
    return variant
