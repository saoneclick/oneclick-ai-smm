"""Рендер карточек для постов: HTML/CSS/SVG -> PNG через Playwright.

Каждая карточка собирается из трёх независимых слоёв:
  * тема     — цветовой акцент (cyan / violet / emerald / amber);
  * арт      — процедурная графика (orbit / grid / terminal / network / chart / photo);
  * раскладка — как текст и графика расположены (poster / split / stage / stack / metrics).

Если поля art/theme/layout не переданы, они выбираются детерминированно по
заголовку (плюс подсказки из ключевых слов), поэтому один и тот же пост всегда
рендерится одинаково, а разные посты выглядят по-разному.
"""

from __future__ import annotations

import base64
import hashlib
import html as htmllib
import json as jsonlib
import re
import uuid
import urllib.request
from pathlib import Path
from string import Template

from fastapi import FastAPI
from fastapi.responses import Response
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, Field

app = FastAPI()
_browser = None

FONT_DIR = Path(__file__).with_name("fonts")

ARTS = ("orbit", "grid", "terminal", "network", "chart", "guard")
THEMES = ("cyan", "violet", "emerald", "amber")
CARD_LAYOUTS = ("poster", "split", "stage", "stack", "metrics")


# --------------------------------------------------------------------------- #
#  Модели
# --------------------------------------------------------------------------- #
class Metric(BaseModel):
    value: str | int | float = ""
    label: str = ""


class Card(BaseModel):
    headline: str = ""
    tag: str = "AI-АВТОМАТИЗАЦИЯ"
    kicker: str = ""
    subtitle: str = ""
    bullets: list[str] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)

    footer_left: str = "Битрикс24 · AI-автоматизация"
    footer_right: str = "saoneclick.ru"

    # Необязательные подсказки. Пустая строка = выбрать автоматически.
    art: str = ""
    theme: str = ""
    layout: str = ""
    image_url: str = ""


class Story(Card):
    cta: str = "Читать пост"


class MaxPhoto(Card):
    max_token: str


# --------------------------------------------------------------------------- #
#  Шрифты (вшиваются в HTML как data URI, чтобы не зависеть от сети)
# --------------------------------------------------------------------------- #
UNICODE_RANGES = {
    "cyrillic": "U+0301, U+0400-045F, U+0490-0491, U+04B0-04B1, U+2116",
    "latin": (
        "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, "
        "U+02DC, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, "
        "U+2215, U+FEFF, U+FFFD"
    ),
}


def _font_css() -> str:
    faces = []
    for family, filename, weight in (
        ("Inter", "Inter-var-{subset}.woff2", "400 700"),
        ("Unbounded", "Unbounded-var-{subset}.woff2", "400 800"),
    ):
        for subset, urange in UNICODE_RANGES.items():
            path = FONT_DIR / filename.format(subset=subset)
            if not path.exists():
                continue
            b64 = base64.b64encode(path.read_bytes()).decode()
            faces.append(
                "@font-face{font-family:'%s';font-style:normal;font-weight:%s;"
                "font-display:block;src:url(data:font/woff2;base64,%s) format('woff2');"
                "unicode-range:%s;}" % (family, weight, b64, urange)
            )
    return "\n".join(faces)


FONT_CSS = _font_css()


# --------------------------------------------------------------------------- #
#  Выбор варианта
# --------------------------------------------------------------------------- #
def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16)


def _pick_theme(c: Card) -> str:
    if c.theme in THEMES:
        return c.theme
    return THEMES[_seed(c.headline or c.tag) % len(THEMES)]


ART_HINTS = (
    ("guard", ("php", "безопасн", "уязвим", "взлом", "фишинг", "защит", "хакер", "пароль", "утечк")),
    ("terminal", ("бот", "telegram", "телеграм", "n8n", "сценар", "интеграц", "автоматизац", "api", "код", "парсер")),
    ("network", ("crm", "битрикс", "bitrix", "нейрос", "данны", "связ", "систем", "ии", "ai")),
    ("chart", ("рост", "вырос", "кейс", "результат", "эконом", "быстрее", "%", "цифр", "прибыл", "лид", "метрик")),
    ("grid", ("внедрен", "запуск", "релиз", "процесс", "порядок", "поток", "workflow")),
)


def _pick_art(c: Card) -> str:
    if c.art in ARTS and c.art != "photo":
        return c.art
    if c.art == "photo":
        return "photo" if _safe_url(c.image_url) else ARTS[_seed(c.headline or "oneclick") % len(ARTS)]
    if c.image_url and _safe_url(c.image_url):
        return "photo"
    parts = [c.headline, c.subtitle]
    if c.tag and c.tag.strip().upper() != "AI-АВТОМАТИЗАЦИЯ":
        parts.append(c.tag)
    haystack = " ".join(parts).lower()
    scores = {art: sum(1 for word in words if word in haystack) for art, words in ART_HINTS}
    best = max(scores.values())
    if best:
        winners = [art for art, score in scores.items() if score == best]
        return winners[_seed(haystack) % len(winners)]
    return ARTS[_seed(haystack or "oneclick") % len(ARTS)]


def _pick_layout(c: Card) -> str:
    if c.layout in CARD_LAYOUTS:
        return c.layout
    if c.metrics:
        return "metrics"
    if c.art == "photo" or (c.image_url and _safe_url(c.image_url)):
        return "stage"
    return CARD_LAYOUTS[_seed((c.headline or "oneclick") + "layout") % 4]


# --------------------------------------------------------------------------- #
#  Текст
# --------------------------------------------------------------------------- #
HIGHLIGHT_RE = re.compile(
    r"([+\-−]?\d+(?:[.,]\d+)?\s*(?:%|₽|руб\.?|млн|тыс\.?|ч|мин|дн[а-я]*|раз[а-я]*|"
    r"задач[а-я]*|клиент[а-я]*|лид[а-я]*|день|дней)?|"
    r"\bAI\b|\bИИ\b|Битрикс24|Bitrix24|\bn8n\b|\bCRM\b|\bMAX\b|Telegram|автоматизац[а-я]*)",
    re.IGNORECASE,
)


def _rich_text(value: str, limit: int = 2) -> str:
    """Экранирует текст и подсвечивает цифры/ключевые слова (не больше limit штук)."""
    text = (value or "").strip()
    if not text:
        return ""
    parts: list[str] = []
    last = 0
    used = 0
    for match in HIGHLIGHT_RE.finditer(text):
        if used >= limit:
            break
        parts.append(htmllib.escape(text[last:match.start()]))
        parts.append('<span class="hl">%s</span>' % htmllib.escape(match.group(0)))
        last = match.end()
        used += 1
    parts.append(htmllib.escape(text[last:]))
    return "".join(parts).replace("\n", "<br>")


def _esc(value: str) -> str:
    return htmllib.escape("" if value is None else str(value), quote=True)


def _safe_url(url: str) -> str:
    url = (url or "").strip()
    if not re.match(r"^(https?://|data:image/)", url, re.IGNORECASE):
        return ""
    if any(ch in url for ch in ('"', "'", "\n", "\r", "\\")):
        return ""
    return url


def _headline_size(text: str, story: bool) -> str:
    n = len(text or "")
    if story:
        return "h-xl" if n <= 22 else "h-l" if n <= 42 else "h-m" if n <= 68 else "h-s"
    return "h-xl" if n <= 20 else "h-l" if n <= 38 else "h-m" if n <= 62 else "h-s"


# --------------------------------------------------------------------------- #
#  Арт-слой
# --------------------------------------------------------------------------- #
def _icon(kind: str) -> str:
    paths = {
        "bolt": '<path d="M13 2 4.5 13.2h6L11 22l8.5-11.2h-6L13 2Z"/>',
        "bot": '<rect x="4" y="7" width="16" height="12" rx="4"/><path d="M12 3v4M8 13h.01M16 13h.01M9 17h6"/>',
        "chart": '<path d="M4 19V9M10 19V5M16 19v-6M22 19H2"/>',
        "gear": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 9 19.4a1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>',
        "arrow": '<path d="M7 17 17 7M9 7h8v8"/>',
        "check": '<path d="m5 12 4.5 4.5L19 6.5"/>',
    }
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">%s</svg>' % paths[kind]
    )


def _art_orbit() -> str:
    return """
<div class="orb"></div>
<div class="oring r1"></div><div class="oring r2"></div>
<span class="sat s1"></span><span class="sat s2"></span><span class="sat s3"></span>
<div class="gchip chip-a"><span class="pulse"></span>AI online</div>
<div class="gchip chip-b">Bitrix24</div>
"""


def _art_grid() -> str:
    panel = """
    <div class="gpanel {pos}">
      <div class="gico">{icon}</div>
      <span class="gline w80"></span><span class="gline w55"></span><span class="gline w35"></span>
    </div>"""
    return (
        '<div class="gfloor"></div><div class="ghorizon"></div>'
        + panel.format(pos="gp1", icon=_icon("bot"))
        + panel.format(pos="gp2", icon=_icon("chart"))
        + panel.format(pos="gp3", icon=_icon("gear"))
    )


def _art_terminal() -> str:
    return """
<div class="term">
  <div class="tbar"><i class="tdot d1"></i><i class="tdot d2"></i><i class="tdot d3"></i><span class="tname">oneclick · ai</span></div>
  <div class="tbody">
    <div><span class="prompt">$</span> <span class="cmd">oneclick deploy --crm bitrix24</span></div>
    <div class="ok">✓ сценарий собран</div>
    <div class="ok">✓ интеграция активна</div>
    <div class="dim">→ заявка → AI → сделка</div>
    <div class="dim"><span class="cursor"></span></div>
  </div>
</div>
<div class="term-pill"><span class="pulse"></span>running</div>
"""


def _art_network() -> str:
    nodes = (
        ("CRM", 126, 138, "n1"),
        ("AI", 452, 120, "n2"),
        ("TG", 470, 402, "n3"),
        ("MAX", 136, 396, "n4"),
    )
    lines = "".join(
        '<path class="nlink" d="M300 300 Q%s 300 %s %s"/>' % (300, x, y) for _, x, y, _ in nodes
    )
    chips = "".join(
        '<g class="nnode"><rect x="%s" y="%s" width="104" height="50" rx="25"/>'
        '<text x="%s" y="%s">%s</text></g>'
        % (x - 52, y - 25, x, y + 7, label)
        for label, x, y, _ in nodes
    )
    return f"""
<svg class="net" viewBox="0 0 600 600" aria-hidden="true">
  <defs>
    <linearGradient id="nlg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="var(--a1)" stop-opacity=".7"/>
      <stop offset="1" stop-color="var(--a2)" stop-opacity=".15"/>
    </linearGradient>
    <radialGradient id="ncore" cx="35%" cy="30%" r="80%">
      <stop offset="0" stop-color="#ffffff" stop-opacity=".95"/>
      <stop offset=".22" stop-color="var(--a1)"/>
      <stop offset="1" stop-color="var(--a2)"/>
    </radialGradient>
    <filter id="nglow" x="-80%" y="-80%" width="260%" height="260%">
      <feGaussianBlur stdDeviation="12" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <circle class="norbit" cx="300" cy="300" r="228"/>
  <circle class="norbit thin" cx="300" cy="300" r="150"/>
  <g class="nlinks">{lines}</g>
  <circle class="ncore" cx="300" cy="300" r="74" filter="url(#nglow)"/>
  <text class="ncore-label" x="300" y="311">AI</text>
  <g class="nchips">{chips}</g>
</svg>
"""


def _art_guard(c: Card) -> str:
    haystack = f"{c.headline} {c.subtitle} {c.tag}".lower()
    php = "php" in haystack
    has_date = "21 октября" in haystack or "21.10" in haystack
    if php and has_date:
        chip = "PHP · 21.10"
    elif php:
        chip = "PHP · check"
    else:
        chip = "secure · 24/7"
    return """
<div class="guard">
  <div class="rring rr1"></div><div class="rring rr2"></div><div class="rring rr3"></div>
  <div class="rsweep"></div>
  <span class="threat t1"></span><span class="threat t2"></span><span class="threat t3"></span>
  <div class="shield">
    <svg viewBox="0 0 240 280" aria-hidden="true">
      <defs>
        <linearGradient id="guardg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="var(--a1)"/>
          <stop offset=".55" stop-color="var(--a2)"/>
          <stop offset="1" stop-color="var(--a1)"/>
        </linearGradient>
      </defs>
      <path class="gp" d="M120 14 216 52v82c0 62-42 101-96 132-54-31-96-70-96-132V52Z"/>
      <path class="gc" d="m76 138 32 32 62-70"/>
    </svg>
  </div>
  <div class="gchip chip-a"><span class="pulse"></span>scan · active</div>
  <div class="gchip chip-b">%s</div>
</div>
""" % chip


def _art_chart(c: Card) -> str:
    heights = (34, 46, 40, 58, 52, 72, 88)
    bars = "".join('<i style="--h:%d%%"></i>' % h for h in heights)
    if c.metrics and str(c.metrics[0].value).strip() != "":
        value = _esc(c.metrics[0].value)
        label = _esc(c.metrics[0].label or "результат")
        head = '<div class="cval">%s</div><div class="clabel">%s</div>' % (value, label)
    else:
        head = '<div class="cval cval-ico">%s</div><div class="clabel">автоматизация</div>' % _icon("chart")
    return """
<div class="chart">
  <div class="chead"><span>динамика</span><span class="cup">↗</span></div>
  %s
  <div class="bars">%s</div>
  <div class="cline"></div>
  <div class="cfoot"><span class="pulse"></span>без ручной рутины</div>
</div>
""" % (head, bars)


def _art_photo(c: Card) -> str:
    url = _safe_url(c.image_url)
    if not url:
        return _art_orbit()
    url = htmllib.escape(url, quote=True)
    return (
        '<div class="photo" style="background-image:url(\'%s\')"></div>'
        '<div class="photo-scrim"></div>'
        '<div class="photo-frame"></div>' % url
    )


def _render_art(c: Card, art: str) -> str:
    if art == "orbit":
        return _art_orbit()
    if art == "grid":
        return _art_grid()
    if art == "terminal":
        return _art_terminal()
    if art == "network":
        return _art_network()
    if art == "guard":
        return _art_guard(c)
    if art == "chart":
        return _art_chart(c)
    return _art_photo(c)


# --------------------------------------------------------------------------- #
#  Шаблон
# --------------------------------------------------------------------------- #
BASE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:__W__px;height:__H__px;overflow:hidden}
body{font-family:Inter,'DejaVu Sans',Arial,sans-serif;-webkit-font-smoothing:antialiased;
     text-rendering:optimizeLegibility;background:#050a13}
.card{position:relative;width:__W__px;height:__H__px;overflow:hidden;color:#fff;
      background:#050a13;isolation:isolate}
.card::before{content:'';position:absolute;inset:0;z-index:1;pointer-events:none;
  background-image:radial-gradient(rgba(255,255,255,.16) 1px,transparent 1.3px);
  background-size:46px 46px;opacity:.5;
  -webkit-mask-image:linear-gradient(145deg,rgba(0,0,0,.9),transparent 62%);
  mask-image:linear-gradient(145deg,rgba(0,0,0,.9),transparent 62%)}
.card::after{content:'';position:absolute;inset:0;z-index:1;pointer-events:none;opacity:.055;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2'/%3E%3C/filter%3E%3Crect width='180' height='180' filter='url(%23n)'/%3E%3C/svg%3E")}
.bg{position:absolute;inset:0;z-index:0;
  background:
    radial-gradient(900px 660px at 82% -8%,var(--glow1),transparent 62%),
    radial-gradient(760px 580px at -10% 108%,var(--glow2),transparent 62%),
    radial-gradient(1200px 900px at 50% 45%,#0a1829 0%,#060d17 56%,#04070d 100%)}
.top{position:absolute;z-index:4;top:54px;left:64px;right:64px;display:flex;align-items:center;gap:15px}
.logo{width:32px;height:32px;border-radius:10px;background:linear-gradient(135deg,var(--a1),var(--a2));
  box-shadow:0 8px 26px var(--glow1);position:relative;flex:none}
.logo::after{content:'';position:absolute;inset:10px 9px;border:2px solid rgba(4,16,28,.75);
  border-radius:3px;border-right-color:transparent;border-bottom-color:transparent}
.brand{font-family:Unbounded,Inter,sans-serif;font-size:21px;font-weight:600;letter-spacing:3.2px;
  color:#eaf6ff}
.tag{margin-left:auto;padding:11px 18px;border-radius:999px;border:1px solid rgba(255,255,255,.14);
  background:rgba(255,255,255,.045);font-size:15px;font-weight:500;letter-spacing:2.2px;
  text-transform:uppercase;color:#a9c6de;white-space:nowrap;backdrop-filter:blur(8px);
  max-width:520px;overflow:hidden;text-overflow:ellipsis}
.main{position:absolute;z-index:3;left:64px;width:660px;display:flex;flex-direction:column}
.rule{width:88px;height:7px;border-radius:4px;background:linear-gradient(90deg,var(--a1),var(--a2));
  box-shadow:0 0 30px var(--glow1);margin-bottom:26px;flex:none}
.kicker{font-size:18px;font-weight:600;letter-spacing:4px;text-transform:uppercase;color:var(--a1);
  margin-bottom:16px}
.headline{font-family:Unbounded,Inter,sans-serif;font-weight:600;line-height:1.055;
  letter-spacing:-.6px;overflow:hidden;text-wrap:balance}
.headline.brk{overflow-wrap:anywhere}
.h-xl{font-size:92px}.h-l{font-size:76px}.h-m{font-size:62px}.h-s{font-size:52px}
.hl{color:var(--a1);text-shadow:0 0 46px var(--glow1)}
.sub{margin-top:24px;font-size:24px;line-height:1.42;color:#a7c1d8;max-width:580px;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.bullets{list-style:none;margin-top:28px;display:flex;flex-direction:column;gap:13px}
.bullets li{display:flex;gap:14px;align-items:flex-start;font-size:21px;line-height:1.3;color:#dcebf8}
.bullets li>span:last-child{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
  overflow:hidden}
.bullets .n{font-family:Unbounded,Inter,sans-serif;font-size:13px;font-weight:600;color:var(--a1);
  padding-top:5px;min-width:28px;letter-spacing:1px}
.foot{position:absolute;z-index:4;left:64px;right:64px;bottom:42px;display:flex;align-items:center;
  padding-top:20px;border-top:1px solid rgba(255,255,255,.11);font-size:18px;color:#8ca7c0}
.foot .site{margin-left:auto;display:flex;align-items:center;gap:12px;color:#a9c6de}
.foot .arr{width:38px;height:38px;border-radius:50%;border:1px solid rgba(255,255,255,.18);
  display:grid;place-items:center;color:var(--a1);background:rgba(255,255,255,.04)}
.foot svg{width:17px;height:17px}

/* ---- art ---- */
.art{position:absolute;z-index:2;pointer-events:none;--k:1}
.art-inner{position:absolute;left:50%;top:50%;width:600px;height:600px;margin:-300px 0 0 -300px;
  transform:scale(var(--k))}
.orb{position:absolute;left:50%;top:50%;width:336px;height:336px;margin:-168px 0 0 -168px;
  border-radius:50%;
  background:
    radial-gradient(circle at 33% 26%,rgba(255,255,255,.98) 0,rgba(220,248,255,.9) 6%,rgba(120,220,255,.32) 17%,transparent 34%),
    radial-gradient(circle at 68% 78%,rgba(3,16,36,.95) 0,transparent 58%),
    conic-gradient(from 215deg at 50% 50%,var(--a1),var(--a2) 40%,var(--a3) 66%,var(--a1));
  box-shadow:inset -42px -54px 92px rgba(0,0,0,.62),inset 28px 24px 70px rgba(255,255,255,.2),
             0 80px 170px -24px var(--glow1)}
.orb::after{content:'';position:absolute;inset:0;border-radius:50%;
  background:radial-gradient(circle at 30% 23%,rgba(255,255,255,.85),transparent 19%),
             linear-gradient(148deg,transparent 44%,rgba(255,255,255,.14) 50%,transparent 57%);
  mix-blend-mode:screen}
.oring{position:absolute;left:50%;top:50%;border-radius:50%;
  border:1px solid rgba(150,222,255,.16);transform:translate(-50%,-50%) rotate(-18deg)}
.oring.r1{width:466px;height:466px;border-top-color:var(--a1);
  border-right-color:rgba(255,255,255,.03);box-shadow:0 0 40px -18px var(--glow1) inset}
.oring.r2{width:556px;height:556px;border-bottom-color:var(--a2);
  border-left-color:rgba(255,255,255,.02)}
.sat{position:absolute;width:11px;height:11px;border-radius:50%;background:var(--a1);
  box-shadow:0 0 16px 3px var(--glow1)}
.sat.s1{left:292px;top:60px}.sat.s2{left:548px;top:300px;width:8px;height:8px;opacity:.8}
.sat.s3{left:206px;top:536px;width:7px;height:7px;opacity:.6}
.gchip{position:absolute;display:flex;align-items:center;gap:10px;padding:12px 17px;border-radius:999px;
  border:1px solid rgba(255,255,255,.16);background:rgba(9,23,39,.6);backdrop-filter:blur(14px);
  font-size:17px;font-weight:600;color:#e2f4ff;box-shadow:0 20px 50px rgba(0,0,0,.4);white-space:nowrap}
.chip-a{left:22px;top:74px;transform:rotate(-7deg)}
.chip-b{right:48px;bottom:86px;transform:rotate(5deg)}
.pulse{width:9px;height:9px;border-radius:50%;background:var(--a1);
  box-shadow:0 0 12px 2px var(--glow1);flex:none}

/* ---- grid ---- */
.gfloor{position:absolute;inset:-10%;
  background-image:linear-gradient(rgba(140,220,255,.22) 1px,transparent 1px),
                   linear-gradient(90deg,rgba(140,220,255,.22) 1px,transparent 1px);
  background-size:52px 52px;
  -webkit-mask-image:radial-gradient(ellipse at 50% 52%,#000 0,transparent 70%);
  mask-image:radial-gradient(ellipse at 50% 52%,#000 0,transparent 70%);opacity:.9}
.ghorizon{position:absolute;left:50%;top:48%;width:560px;height:220px;transform:translate(-50%,-50%);
  background:radial-gradient(ellipse at center,var(--glow1) 0,transparent 68%);filter:blur(20px)}
.gpanel{position:absolute;width:158px;height:196px;padding:18px;border-radius:24px;
  border:1px solid rgba(255,255,255,.15);
  background:linear-gradient(160deg,rgba(26,54,84,.86),rgba(7,17,30,.82));
  box-shadow:0 34px 70px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.12);
  backdrop-filter:blur(10px);display:flex;flex-direction:column;gap:11px}
.gp1{left:64px;top:188px;transform:rotate(-11deg)}
.gp2{left:222px;top:132px;transform:rotate(4deg);width:172px;height:214px;z-index:2}
.gp3{left:402px;top:224px;transform:rotate(12deg)}
.gico{width:46px;height:46px;border-radius:14px;display:grid;place-items:center;color:#04121f;
  background:linear-gradient(135deg,var(--a1),var(--a2));box-shadow:0 10px 24px var(--glow1);margin-bottom:6px}
.gico svg{width:25px;height:25px}
.gline{display:block;height:7px;border-radius:4px;background:rgba(180,220,250,.22)}
.gline.w80{width:80%}.gline.w55{width:55%}.gline.w35{width:35%}

/* ---- terminal ---- */
.term{position:absolute;left:50%;top:50%;width:478px;transform:translate(-50%,-50%) rotate(-2deg);
  border-radius:20px;border:1px solid rgba(255,255,255,.16);overflow:hidden;
  background:linear-gradient(180deg,rgba(11,27,45,.95),rgba(4,10,19,.97));
  box-shadow:0 54px 120px rgba(0,0,0,.62),inset 0 1px 0 rgba(255,255,255,.08);
  font-family:'DejaVu Sans Mono','Liberation Mono',monospace}
.tbar{display:flex;align-items:center;gap:8px;padding:14px 18px;
  border-bottom:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03)}
.tdot{width:10px;height:10px;border-radius:50%}.d1{background:#ff5f57}.d2{background:#ffbd2e}.d3{background:#28c840}
.tname{margin-left:8px;font-size:13px;letter-spacing:1.4px;color:#7f98b0}
.tbody{padding:22px 24px 26px;font-size:16px;line-height:1.9;color:#a9c5db}
.cmd{color:#eaf6ff}.prompt{color:var(--a1);font-weight:700}
.ok{color:#5ee08a}.dim{color:#6f8aa3}
.cursor{display:inline-block;width:9px;height:17px;background:var(--a1);vertical-align:-3px;
  box-shadow:0 0 10px var(--glow1)}
.term-pill{position:absolute;right:66px;bottom:132px;display:flex;align-items:center;gap:9px;
  padding:11px 16px;border-radius:999px;border:1px solid rgba(255,255,255,.16);
  background:rgba(9,23,39,.72);backdrop-filter:blur(12px);font-size:15px;color:#dff4ff;
  transform:rotate(4deg);box-shadow:0 20px 44px rgba(0,0,0,.4)}

/* ---- network ---- */
.net{position:absolute;inset:0;width:600px;height:600px;overflow:visible}
.norbit{fill:none;stroke:rgba(150,220,255,.16);stroke-width:1}
.norbit.thin{stroke-dasharray:3 8;stroke:rgba(150,220,255,.28)}
.nlink{fill:none;stroke:url(#nlg);stroke-width:1.6}
.ncore{fill:url(#ncore)}
.ncore-label{fill:#04121f;font-family:Unbounded,Inter,sans-serif;font-size:34px;font-weight:600;
  text-anchor:middle}
.nchips rect{fill:rgba(8,21,36,.82);stroke:rgba(180,230,255,.26);stroke-width:1}
.nchips text{fill:#dff2ff;font-family:Inter,sans-serif;font-size:17px;font-weight:600;
  letter-spacing:1.5px;text-anchor:middle}

/* ---- guard ---- */
.guard{position:absolute;inset:0}
.rring{position:absolute;left:50%;top:50%;border-radius:50%;
  border:1px solid rgba(150,222,255,.18);transform:translate(-50%,-50%)}
.rr1{width:520px;height:520px}
.rr2{width:390px;height:390px;border-style:dashed;border-color:rgba(150,222,255,.3)}
.rr3{width:270px;height:270px;border-color:rgba(150,222,255,.12)}
.rsweep{position:absolute;left:50%;top:50%;width:520px;height:520px;margin:-260px 0 0 -260px;
  border-radius:50%;opacity:.6;
  background:conic-gradient(from 0deg,transparent 0 74%,var(--glow1) 88%,transparent 96%);
  -webkit-mask-image:radial-gradient(circle,transparent 0 24%,#000 25% 100%);
  mask-image:radial-gradient(circle,transparent 0 24%,#000 25% 100%)}
.threat{position:absolute;width:9px;height:9px;border-radius:2px;background:#ff6b57;
  box-shadow:0 0 14px 2px rgba(255,107,87,.5);transform:rotate(45deg);opacity:.9}
.t1{left:112px;top:126px}
.t2{right:96px;top:224px;width:7px;height:7px}
.t3{left:154px;bottom:88px;width:6px;height:6px}
.shield{position:absolute;left:50%;top:50%;width:236px;height:276px;margin:-138px 0 0 -118px;
  filter:drop-shadow(0 24px 70px var(--glow1))}
.shield svg{width:100%;height:100%}
.gp{fill:rgba(8,21,37,.62);stroke:url(#guardg);stroke-width:9;stroke-linejoin:round}
.gc{fill:none;stroke:url(#guardg);stroke-width:13;stroke-linecap:round;stroke-linejoin:round}

/* ---- chart ---- */
.chart{position:absolute;left:50%;top:50%;width:438px;transform:translate(-50%,-50%) rotate(-1.5deg);
  padding:26px 26px 22px;border-radius:24px;border:1px solid rgba(255,255,255,.15);
  background:linear-gradient(160deg,rgba(15,34,55,.93),rgba(5,13,24,.95));
  box-shadow:0 50px 120px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.08)}
.chead{display:flex;justify-content:space-between;align-items:center;font-size:14px;
  letter-spacing:2.4px;text-transform:uppercase;color:#93aec6}
.cup{color:var(--a1);font-size:22px}
.cval{font-family:Unbounded,Inter,sans-serif;font-size:52px;font-weight:600;margin:12px 0 2px;
  line-height:1.05}
.cval-ico{color:var(--a1);display:flex;align-items:center;height:58px}
.cval-ico svg{width:44px;height:44px}
.clabel{font-size:15px;color:#9db8cf;margin-bottom:20px}
.bars{display:flex;align-items:flex-end;gap:11px;height:146px}
.bars i{flex:1;height:var(--h);border-radius:7px 7px 3px 3px;
  background:linear-gradient(180deg,var(--a1),var(--a2));opacity:.88;
  box-shadow:0 0 22px -4px var(--glow1)}
.cfoot{display:flex;align-items:center;gap:10px;margin-top:18px;font-size:14px;color:#8fa9c1;
  letter-spacing:.6px}

/* ---- photo ---- */
.art.art-photo .art-inner{position:absolute;left:0;top:0;width:100%;height:100%;margin:0;
  transform:none}
.photo{position:absolute;inset:0;background-size:cover;background-position:center;
  filter:saturate(1.06) contrast(1.03)}
.photo-scrim{position:absolute;inset:0;background:
  linear-gradient(180deg,rgba(5,10,19,.6) 0%,rgba(5,10,19,.04) 20%,rgba(5,10,19,.16) 46%,
    rgba(5,10,19,.8) 80%,#04070d 100%),
  radial-gradient(ellipse at 72% 38%,transparent 34%,rgba(5,10,19,.35) 100%)}
.photo-frame{position:absolute;inset:22px;border:1px solid rgba(255,255,255,.18);border-radius:18px}

/* ---- metrics ---- */
.metrics{position:absolute;z-index:3;right:64px;top:248px;width:306px;display:flex;
  flex-direction:column;gap:14px}
.metric{padding:22px 24px;border-radius:20px;border:1px solid rgba(255,255,255,.13);
  background:linear-gradient(150deg,rgba(22,47,74,.82),rgba(6,15,27,.88));
  box-shadow:0 26px 60px rgba(0,0,0,.4),inset 0 1px 0 rgba(255,255,255,.08);
  backdrop-filter:blur(12px);position:relative;overflow:hidden}
.metric::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;
  background:linear-gradient(180deg,var(--a1),var(--a2))}
.metric b{display:block;font-family:Unbounded,Inter,sans-serif;font-size:40px;font-weight:600;
  line-height:1.05;margin-bottom:6px}
.metric span{font-size:15px;line-height:1.3;color:#9fb9d1;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}

/* ---- layouts ---- */
.layout-poster .main{bottom:144px}
.layout-poster .headline{max-height:392px}
.layout-poster .art{top:30px;right:-20px;width:640px;height:640px;--k:.94}
.layout-split .main{top:50%;transform:translateY(-50%);width:512px}
.layout-split .headline{max-height:470px}
.layout-split .sub{max-width:470px}
.layout-split .art{top:0;right:0;width:470px;height:1080px;
  border-left:1px solid rgba(255,255,255,.09);
  background:linear-gradient(180deg,rgba(16,38,61,.55),rgba(5,11,20,.2))}
.layout-split .art-inner{transform:scale(.82)}
.layout-split .art-terminal .art-inner{transform:scale(.95)}
.layout-split .art-chart .art-inner{transform:scale(.9)}
.layout-split .art-network .art-inner{transform:scale(.82)}
.layout-stage .main{bottom:150px;width:760px}
.layout-stage .headline{max-height:400px}
.layout-stage .art{top:-70px;bottom:30px;left:-40px;right:-40px;--k:1.28}
.layout-stage .art::after{content:'';position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(180deg,transparent 46%,rgba(5,10,19,.45) 72%,rgba(5,10,19,.92) 100%)}
.layout-stage .bg{background:
  radial-gradient(1000px 760px at 76% 18%,var(--glow1),transparent 60%),
  radial-gradient(760px 580px at -10% 108%,var(--glow2),transparent 62%),
  linear-gradient(180deg,rgba(10,24,41,.2) 0%,rgba(5,10,18,.72) 62%,#04070d 100%),
  radial-gradient(1200px 900px at 50% 45%,#0a1829 0%,#060d17 56%,#04070d 100%)}
.layout-stage .main{text-shadow:0 6px 40px rgba(0,0,0,.55)}
.layout-stack .main{top:594px;width:860px}
.layout-stack .headline{max-height:348px}
.layout-stack .art{top:-70px;left:0;right:0;height:670px;--k:.86}
.layout-stack .bg{background:
  radial-gradient(900px 600px at 76% -12%,var(--glow1),transparent 60%),
  radial-gradient(760px 580px at -10% 108%,var(--glow2),transparent 62%),
  radial-gradient(1200px 900px at 50% 45%,#0a1829 0%,#060d17 56%,#04070d 100%)}
.layout-metrics .main{top:50%;transform:translateY(-50%);width:512px}
.layout-metrics .headline{max-height:390px}
.layout-metrics .sub{max-width:470px}
.layout-metrics .art{display:none}
.layout-metrics .metrics{top:50%;transform:translateY(-50%)}

/* ---- story ---- */
.story .top{top:70px;left:74px;right:74px;gap:18px}
.story .logo{width:38px;height:38px;border-radius:12px}
.story .logo::after{inset:12px 11px}
.story .brand{font-size:25px;letter-spacing:4px}
.story .tag{padding:13px 22px;font-size:17px;letter-spacing:2.4px;max-width:660px}
.story .main{left:80px;right:80px;top:auto;bottom:520px;width:auto}
.story .headline{max-height:460px}
.story .h-xl{font-size:118px}.story .h-l{font-size:100px}.story .h-m{font-size:84px}.story .h-s{font-size:70px}
.story .sub{font-size:28px;max-width:740px;margin-top:26px}
.story .bullets li{font-size:24px}
.story .art{top:130px;left:0;right:0;height:980px}
.story .art.art-photo{top:0;height:1920px}
.story .art.art-photo .photo-frame{display:none}
.story .art.art-photo .photo-scrim{background:
  linear-gradient(180deg,rgba(5,10,19,.66) 0%,rgba(5,10,19,.06) 16%,rgba(5,10,19,.12) 40%,
    rgba(5,10,19,.74) 52%,#04070d 61%,#04070d 100%),
  radial-gradient(ellipse at 60% 30%,transparent 30%,rgba(5,10,19,.35) 100%)}
.story .art-inner{transform:scale(1.5)}
.story .art-terminal .art-inner{transform:scale(1.55)}
.story .art-chart .art-inner{transform:scale(1.45)}
.story .art-network .art-inner{transform:scale(1.45)}
.story .art-grid .art-inner{transform:scale(1.45)}
.story .cta{position:absolute;z-index:4;left:80px;right:80px;bottom:186px;height:116px;
  border-radius:58px;display:flex;align-items:center;gap:18px;padding:0 22px 0 38px;
  background:linear-gradient(100deg,var(--a1),var(--a2));color:#05121f;
  font-size:29px;font-weight:700;letter-spacing:.2px;
  box-shadow:0 26px 70px var(--glow1)}
.story .cta .arr{margin-left:auto;width:72px;height:72px;border-radius:50%;
  background:rgba(4,16,28,.16);display:grid;place-items:center;flex:none}
.story .cta .arr svg{width:28px;height:28px}
.story .foot{bottom:64px}

"""

BASE_HTML = Template("""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<style>
$font_css
$css
:root{--a1:#38e1ff;--a2:#2563eb;--a3:#7c3aed;--glow1:rgba(56,225,255,.34);--glow2:rgba(37,99,235,.26)}
$theme_css
</style></head><body>
<div class="card theme-$theme layout-$layout$story_cls">
  <div class="bg"></div>
  <div class="art art-$art_kind"><div class="art-inner">$art</div></div>
  <div class="top"><div class="logo"></div><div class="brand">ONE CLICK</div><div class="tag">$tag</div></div>
  <div class="main">
    ${rule}${kicker}<div class="headline $hsize" data-fit>$headline</div>$sub$bullets
  </div>
  $metrics
  $cta
  <div class="foot"><div>$footer_left</div><div class="site">$footer_right<span class="arr">$arrow</span></div></div>
</div>
</body></html>""")


THEME_CSS = {
    "cyan": ".theme-cyan{--a1:#38e1ff;--a2:#2563eb;--a3:#7c3aed;"
            "--glow1:rgba(56,225,255,.34);--glow2:rgba(37,99,235,.26)}",
    "violet": ".theme-violet{--a1:#b794ff;--a2:#7c3aed;--a3:#22d3ee;"
              "--glow1:rgba(167,139,250,.38);--glow2:rgba(124,58,237,.3)}",
    "emerald": ".theme-emerald{--a1:#4ade80;--a2:#0ea5e9;--a3:#22d3ee;"
               "--glow1:rgba(74,222,128,.32);--glow2:rgba(14,165,233,.26)}",
    "amber": ".theme-amber{--a1:#ffb454;--a2:#f97316;--a3:#ef4444;"
             "--glow1:rgba(255,180,84,.32);--glow2:rgba(249,115,22,.26)}",
}


def _bullets_html(items: list[str]) -> str:
    items = [x for x in (items or []) if str(x).strip()][:3]
    if not items:
        return ""
    rows = "".join(
        '<li><span class="n">%02d</span><span>%s</span></li>' % (i + 1, _rich_text(x, 1))
        for i, x in enumerate(items)
    )
    return '<ul class="bullets">%s</ul>' % rows


def _metrics_html(items: list[Metric]) -> str:
    items = [m for m in (items or []) if str(m.value).strip() or str(m.label).strip()][:3]
    if not items:
        return ""
    cards = "".join(
        '<div class="metric"><b>%s</b><span>%s</span></div>'
        % (_esc(m.value), _esc(m.label))
        for m in items
    )
    return '<div class="metrics">%s</div>' % cards


def build_html(c: Card, story: bool = False) -> str:
    art = _pick_art(c)
    theme = _pick_theme(c)
    layout = _pick_layout(c)
    if story:
        layout = "story"

    headline = (c.headline or "One Click").strip()
    arrow = _icon("arrow")

    cta = ""
    if story:
        cta = '<div class="cta">%s<span class="arr">%s</span></div>' % (_esc(c.cta), arrow)

    width, height = (1080, 1920) if story else (1080, 1080)
    css = BASE_CSS.replace("__W__", str(width)).replace("__H__", str(height))

    return BASE_HTML.safe_substitute(
        font_css=FONT_CSS,
        css=css,
        theme_css=THEME_CSS.get(theme, THEME_CSS["cyan"]),
        theme=theme,
        layout=layout,
        story_cls=" story" if story else "",
        art=_render_art(c, art),
        art_kind=art,
        tag=_esc(c.tag),
        rule='<div class="rule"></div>' if not c.kicker else "",
        kicker='<div class="kicker">%s</div>' % _esc(c.kicker) if c.kicker else "",
        hsize=_headline_size(headline, story),
        headline=_rich_text(headline),
        sub='<div class="sub">%s</div>' % _rich_text(c.subtitle, 1) if c.subtitle else "",
        bullets=_bullets_html(c.bullets),
        metrics="" if story else _metrics_html(c.metrics),
        cta=cta,
        footer_left=_esc(c.footer_left),
        footer_right=_esc(c.footer_right),
        arrow=arrow,
    )


# --------------------------------------------------------------------------- #
#  Рендер
# --------------------------------------------------------------------------- #
FIT_JS = """
async () => {
  await document.fonts.ready;
  const el = document.querySelector('[data-fit]');
  if (!el) return;
  const max = parseFloat(getComputedStyle(el).maxHeight);
  if (!isFinite(max)) return;
  let size = parseFloat(getComputedStyle(el).fontSize);
  let guard = 0;
  while ((el.scrollHeight > max + 1 || el.scrollWidth > el.clientWidth + 1) && size > 30 && guard < 80) {
    size -= 2;
    el.style.fontSize = size + 'px';
    guard += 1;
  }
  if (el.scrollWidth > el.clientWidth + 1) el.classList.add('brk');
  const main = document.querySelector('.layout-metrics .main');
  const met = document.querySelector('.layout-metrics .metrics');
  if (main && met) {
    const mt = main.getBoundingClientRect().top;
    const top = Math.max(160, mt + (main.offsetHeight - met.offsetHeight) / 2);
    met.style.top = top + 'px';
    met.style.transform = 'none';
  }
}
"""


PHOTO_CHECK_JS = """
async () => {
  const el = document.querySelector('.photo');
  if (!el) return true;
  const bg = getComputedStyle(el).backgroundImage;
  const m = bg.match(/url\\(["']?(.*?)["']?\\)/);
  if (!m) return false;
  try {
    const img = new Image();
    img.src = m[1];
    await Promise.race([
      img.decode(),
      new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 8000))
    ]);
    return img.naturalWidth > 0;
  } catch (e) {
    return false;
  }
}
"""


async def _render_once(c: Card, story: bool) -> tuple[bytes | None, bool]:
    width, height = (1080, 1920) if story else (1080, 1080)
    page = await _browser.new_page(
        viewport={"width": width, "height": height},
        device_scale_factor=1,
    )
    try:
        try:
            await page.set_content(
                build_html(c, story=story),
                wait_until="domcontentloaded",
                timeout=15000,
            )
            photo_ok = bool(await page.evaluate(PHOTO_CHECK_JS))
        except PlaywrightTimeoutError:
            return None, False
        if not photo_ok:
            return None, False
        await page.evaluate(FIT_JS)
        await page.wait_for_timeout(100)
        return await page.screenshot(type="png"), True
    finally:
        await page.close()


async def _render(c: Card, story: bool = False) -> bytes:
    png, photo_ok = await _render_once(c, story)
    if png is not None:
        return png

    # Картинка по URL не загрузилась — откатываемся на процедурный арт,
    # чтобы не отдавать в канал пустую карточку.
    fallback_art = "" if c.art == "photo" else c.art
    fallback = c.model_copy(update={"image_url": "", "art": fallback_art})
    png, _ = await _render_once(fallback, story)
    if png is None:
        raise RuntimeError("card render failed")
    return png


async def render_card(c: Card) -> bytes:
    return await _render(c, story=False)


async def render_story(s: Story) -> bytes:
    return await _render(s, story=True)


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #
@app.on_event("startup")
async def _startup():
    global _browser
    pw = await async_playwright().start()
    _browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])


@app.get("/health")
async def health():
    return {"ok": True, "fonts": len(FONT_CSS) > 0}


@app.get("/variants")
async def variants():
    return {
        "art": list(ARTS) + ["photo"],
        "theme": list(THEMES),
        "layout": list(CARD_LAYOUTS),
    }


@app.post("/card")
async def card(c: Card):
    png = await render_card(c)
    return Response(content=png, media_type="image/png")


@app.post("/story_card")
async def story_card(s: Story):
    png = await render_story(s)
    return Response(content=png, media_type="image/png")


@app.post("/max_photo")
async def max_photo(c: MaxPhoto):
    png = await render_card(c)

    req = urllib.request.Request(
        "https://botapi.max.ru/uploads?type=image",
        data=b"",
        method="POST",
        headers={"Authorization": c.max_token},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        up = jsonlib.loads(r.read())

    boundary = uuid.uuid4().hex
    body = (
        (
            '--%s\r\nContent-Disposition: form-data; name="data"; filename="card.png"\r\n'
            "Content-Type: image/png\r\n\r\n" % boundary
        ).encode()
        + png
        + b"\r\n"
        + ("--%s--\r\n" % boundary).encode()
    )
    req = urllib.request.Request(
        up["url"],
        data=body,
        method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        photos = jsonlib.loads(r.read())

    return {"token": photos["photos"]["0"]["token"]}
