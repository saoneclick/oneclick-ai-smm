from __future__ import annotations

import html
import re
import shutil
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(text: str, limit: int = 70) -> str:
    text = (text or "").lower()
    text = "".join(TRANSLIT.get(ch, ch) for ch in text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text[:limit].strip("-") or "post")


def _paragraphs(text: str) -> list[str]:
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text or "") if c.strip()]
    if not chunks:
        chunks = [text.strip()] if text.strip() else [""]
    return chunks


def _article_html(title: str, cover: str, body: str, extra_images: list[str]) -> str:
    parts = [
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">',
        f"<title>{html.escape(title)}</title>",
        '</head><body><p><a href="/">One Click</a></p>',
        f"<h1>{html.escape(title)}</h1>",
        f'<p><img src="{html.escape(cover)}" style="max-width:100%"></p>',
    ]
    for para in _paragraphs(body):
        parts.append(f"<p>{html.escape(para).replace(chr(10), '<br>')}</p>")
    for image in extra_images:
        parts.append(f'<p><img src="{html.escape(image)}" style="max-width:100%"></p>')
    parts.append("</body></html>")
    return "".join(parts)


def _feed_item(
    *,
    title: str,
    link: str,
    description: str,
    cover: str,
    body: str,
    extra_images: list[str],
    published_at: datetime,
) -> str:
    def cdata(value: str) -> str:
        return value.replace("]]>", "]]]]><![CDATA[>")

    content_parts = [f'<figure><img src="{html.escape(cover)}"><figcaption>{html.escape(title)}</figcaption></figure>']
    for para in _paragraphs(body):
        content_parts.append(f"<p>{html.escape(para).replace(chr(10), '<br>')}</p>")
    for image in extra_images:
        content_parts.append(f'<figure><img src="{html.escape(image)}"></figure>')
    content = "".join(content_parts)
    pub = format_datetime(published_at)
    return (
        "<item>"
        f"<title>{escape(title)}</title>"
        f"<link>{escape(link)}</link>"
        f'<guid isPermaLink="true">{escape(link)}</guid>'
        f"<pubDate>{pub}</pubDate>"
        f"<description>{escape(description[:260])}</description>"
        "<category>native-draft</category><category>format-article</category>"
        "<category>index</category><category>comment-all</category>"
        f'<enclosure url="{escape(cover)}" type="image/png"/>'
        f"<content:encoded><![CDATA[{cdata(content)}]]></content:encoded>"
        "</item>"
    )


def publish_article(
    *,
    draft: dict[str, Any],
    cover_path: Path | None,
    extra_images: list[dict[str, Any]],
    base_dir: Path,
    public_base: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    draft_id = int(draft.get("id") or 0)
    title = (draft.get("dzen_title") or draft.get("headline") or draft.get("topic") or "One Click").strip()
    body = (draft.get("dzen_text") or draft.get("tg") or "").strip()
    if not title:
        title = "One Click"
    slug = slugify(title)
    article_name = f"{draft_id}-{slug}.html"
    article_rel = f"articles/{article_name}"
    article_url = f"{public_base}/{article_rel}"
    cover_url = f"{public_base}/cards/{draft_id}.png"
    extra_urls = [f"{public_base}/cards/{draft_id}-{i}.jpg" for i in range(1, len(extra_images) + 1)]

    base_dir = Path(base_dir)
    articles = base_dir / "articles"
    cards = base_dir / "cards"
    articles.mkdir(parents=True, exist_ok=True)
    cards.mkdir(parents=True, exist_ok=True)

    article_html = _article_html(title, cover_url, body, extra_urls)
    (articles / article_name).write_text(article_html, encoding="utf-8")

    if cover_path and Path(cover_path).exists():
        shutil.copyfile(cover_path, cards / f"{draft_id}.png")
    for i, asset in enumerate(extra_images, start=1):
        src = Path(asset.get("abs_path") or "")
        if src.exists():
            shutil.copyfile(src, cards / f"{draft_id}-{i}.jpg")

    feed_path = base_dir / "feed.xml"
    feed = feed_path.read_text(encoding="utf-8") if feed_path.exists() else (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:media="http://search.yahoo.com/mrss/"><channel>'
        "<title>One Click — автоматизация бизнеса</title>"
        f"<link>{public_base}/</link>"
        "<description>Битрикс24, AI-автоматизация, дашборды, боты и интеграции.</description>"
        "<language>ru</language></channel></rss>"
    )
    if f"articles/{draft_id}-" not in feed:
        now = datetime.now(timezone(timedelta(hours=5)))
        item = _feed_item(
            title=title,
            link=article_url,
            description=re.sub(r"\s+", " ", body),
            cover=cover_url,
            body=body,
            extra_images=extra_urls,
            published_at=now,
        )
        marker = "<language>ru</language>"
        if marker in feed:
            feed = feed.replace(marker, marker + item, 1)
        else:
            feed = feed.replace("</channel>", item + "</channel>", 1)
        if not dry_run:
            feed_path.write_text(feed, encoding="utf-8")

    index_path = base_dir / "index.html"
    if index_path.exists() and f"articles/{draft_id}-" not in index_path.read_text(encoding="utf-8"):
        index = index_path.read_text(encoding="utf-8")
        li = f'<li><a href="/{article_rel}">{html.escape(title)}</a></li>'
        if "<ul>" in index:
            index = index.replace("<ul>", "<ul>" + li, 1)
            if not dry_run:
                index_path.write_text(index, encoding="utf-8")

    return {"url": article_url, "article": article_rel, "title": title, "dry_run": dry_run}
