import io
from pathlib import Path

from PIL import Image

import dzen
import interview
import media
from prompts import COPYWRITER_SYSTEM, STRATEGIST_SYSTEM, copy_user, strategy_user


def test_interview_happy_path():
    data = interview.new_session_data()
    for step, value in [
        ("goal", "leads"),
        ("audience", "owners"),
        ("topic", "Автоматизировали заявки из Telegram в Битрикс24"),
        ("proof", "400 заявок, 6 часов → 3 минуты"),
        ("format", "case"),
        ("cta", "dm"),
    ]:
        data, nxt = interview.apply_answer(data, step, value)
    assert nxt == "visual"
    text, keyboard = interview.render_summary(data)
    assert "Заявки и лиды" in text
    assert "Кейс с цифрами" in text
    assert keyboard["inline_keyboard"]


def test_interview_edit_returns_summary():
    data = interview.new_session_data()
    data["editing"] = "topic"
    data, nxt = interview.apply_answer(data, "topic", "Новая тема поста про автоматизацию")
    assert nxt == "summary"
    assert data["editing"] == ""
    assert data["topic"].startswith("Новая тема")


def test_channels_toggle_keeps_one():
    data = interview.new_session_data()
    data = interview.toggle_channel(data, "telegram")
    assert "telegram" not in data["channels"]
    data = interview.toggle_channel(data, "max")
    assert "max" not in data["channels"]
    data = interview.toggle_channel(data, "vk")
    assert "vk" not in data["channels"]
    data = interview.toggle_channel(data, "dzen")
    assert data["channels"] == ["dzen"]


def test_parse_callback():
    assert interview.parse_callback("menu:smm") == ("start", "")
    assert interview.parse_callback("smm:goal:leads") == ("goal", "leads")
    assert interview.parse_callback("smm:edit:topic") == ("edit", "topic")
    assert interview.parse_callback("smm:approve:12") == ("approve", "12")


def test_strategy_and_copy_prompts_include_brief():
    brief = {"topic": "Тест", "goal": "Заявки"}
    s = strategy_user(brief, ["правило"], ["пример"])
    assert "Тест" in s and "правило" in s and "пример" in s
    c = copy_user(brief, {"angle": "кейс"}, [], [], True, ["telegram"])
    assert "кейс" in c and "КАРТИНКИ: есть" in c
    assert "hook" in COPYWRITER_SYSTEM
    assert "insight" in STRATEGIST_SYSTEM


def test_dzen_publish_writes_article_feed_and_index(tmp_path):
    base = tmp_path / "dzen"
    (base / "articles").mkdir(parents=True)
    (base / "cards").mkdir()
    (base / "feed.xml").write_text(
        '<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        "<channel><title>Test</title><language>ru</language></channel></rss>",
        encoding="utf-8",
    )
    (base / "index.html").write_text("<html><body><ul></ul></body></html>", encoding="utf-8")
    cover = tmp_path / "cover.png"
    Image.new("RGB", (10, 10), (10, 20, 30)).save(cover)
    report = dzen.publish_article(
        draft={
            "id": 16,
            "dzen_title": "Тестовая статья",
            "dzen_text": "Первый абзац.\n\nВторой абзац.",
            "tg": "Текст",
        },
        cover_path=cover,
        extra_images=[],
        base_dir=base,
        public_base="https://example.test",
        dry_run=False,
    )
    assert (base / report["article"]).exists()
    assert "Тестовая статья" in (base / "feed.xml").read_text(encoding="utf-8")
    assert "articles/16-" in (base / "index.html").read_text(encoding="utf-8")


def test_media_store_and_resize(tmp_path, monkeypatch):
    image = Image.new("RGB", (3000, 1000), (120, 80, 40))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    asset = media.store_image(
        buf.getvalue(),
        chat_id=1,
        session_id=2,
        kind="photo",
        source="test",
    )
    path = media.absolute_path(asset["path"])
    assert path.exists()
    assert asset["width"] <= 1800
    assert asset["url"].startswith("http")
    media.delete_session_files(1, 2)
    assert not path.exists()
