"""Build a Dzen-ready mini-site + RSS feed from content_drafts.

Run on the server (host): python3 services/dzen/build.py
Requires: docker compose stack running, cardrender port 127.0.0.1:8100.
"""
import html
import json
import os
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

OUT = os.environ.get("DZEN_DIR", "/opt/marketing/dzen")
BASE = os.environ.get("DZEN_PUBLIC_BASE", "https://dzen.saoneclick.ru").rstrip("/")
SITE_TITLE = "One Click — автоматизация бизнеса"
SITE_DESC = "Битрикс24, AI-автоматизация, дашборды, боты и интеграции."
TZ = timezone(timedelta(hours=5))
COMPOSE = os.environ.get("COMPOSE_DIR", "/opt/marketing")
DB_USER = os.environ.get("POSTGRES_USER", "n8n")
DB_NAME = os.environ.get("POSTGRES_DB", "n8n")
CARD_RENDER_URL = os.environ.get("CARD_RENDER_URL", "http://127.0.0.1:8100")

TRANS = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def slugify(text, limit=70):
    out = []
    for ch in (text or "").lower():
        if ch in TRANS:
            out.append(TRANS[ch])
        elif ch.isalnum():
            out.append(ch)
        else:
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:limit].strip("-") or "post"


def db_rows():
    sql = ("SELECT json_agg(t) FROM (SELECT id, COALESCE(NULLIF(headline,''), topic) AS headline, "
           "COALESCE(NULLIF(body,''), tg_text) AS text, created_at FROM content_drafts "
           "WHERE COALESCE(NULLIF(body,''), tg_text) <> '' "
           "AND status NOT IN ('rejected','test','smm_error') ORDER BY id) t;")
    out = subprocess.check_output(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", DB_USER, "-d", DB_NAME, "-t", "-A", "-c", sql],
        cwd=COMPOSE,
    ).decode("utf-8").strip()
    return json.loads(out) if out else []


def render_card(headline, path):
    body = json.dumps({"headline": headline, "tag": "АВТОМАТИЗАЦИЯ"}).encode()
    req = urllib.request.Request(f"{CARD_RENDER_URL}/card", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    with open(path, "wb") as f:
        f.write(data)


def paragraphs(text):
    parts = [p.strip() for p in text.replace("\r", "").split("\n\n") if p.strip()]
    if len(parts) <= 1:
        parts = [p.strip() for p in text.split("\n") if p.strip()]
    return "".join("<p>%s</p>" % html.escape(p) for p in parts)


def main():
    os.makedirs(OUT + "/articles", exist_ok=True)
    os.makedirs(OUT + "/cards", exist_ok=True)

    rows = db_rows()
    items = []
    for r in rows:
        pid = r["id"]
        headline = (r["headline"] or "").strip() or ("Пост %d" % pid)
        headline = headline[:110]
        text = r["text"] or ""
        created = r["created_at"]
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(TZ)
        except Exception:
            dt = datetime.now(TZ)

        card_path = "%s/cards/%d.png" % (OUT, pid)
        if not os.path.exists(card_path):
            try:
                render_card(headline, card_path)
            except Exception as e:
                print("card fail", pid, e)

        fname = "%d-%s.html" % (pid, slugify(headline))
        url = "%s/articles/%s" % (BASE, fname)
        inner = ('<figure><img src="%s/cards/%d.png"><figcaption>%s</figcaption></figure>'
                 % (BASE, pid, html.escape(headline))) + paragraphs(text)

        page = ("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
                "<title>%s</title><link rel=\"canonical\" href=\"%s\"></head><body>"
                "<p><a href=\"/\">One Click</a></p><h1>%s</h1>%s</body></html>"
                % (html.escape(headline), url, html.escape(headline),
                   ('<p><img src="/cards/%d.png" style="max-width:100%%"></p>' % pid) + paragraphs(text)))
        with open("%s/articles/%s" % (OUT, fname), "w", encoding="utf-8") as f:
            f.write(page)

        items.append({"id": pid, "headline": headline, "url": url, "fname": fname, "dt": dt,
                      "text": text, "inner": inner, "card": "%s/cards/%d.png" % (BASE, pid)})

    # index
    links = "".join('<li><a href="/articles/%s">%s</a></li>' % (i["fname"], html.escape(i["headline"]))
                    for i in sorted(items, key=lambda x: -x["id"]))
    index = ("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
             "<title>%s</title><meta name=\"description\" content=\"%s\">"
             "<!-- ZEN_VERIFICATION_META --></head><body><h1>%s</h1><p>%s</p><ul>%s</ul></body></html>"
             % (SITE_TITLE, SITE_DESC, SITE_TITLE, SITE_DESC, links))
    with open(OUT + "/index.html", "w", encoding="utf-8") as f:
        f.write(index)

    with open(OUT + "/robots.txt", "w", encoding="utf-8") as f:
        f.write("User-agent: *\nAllow: /\nAllow: /feed.xml\nSitemap: %s/feed.xml\n" % BASE)

    # feed
    channel = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
               "<rss version=\"2.0\" xmlns:content=\"http://purl.org/rss/1.0/modules/content/\" "
               "xmlns:dc=\"http://purl.org/dc/elements/1.1/\" "
               "xmlns:media=\"http://search.yahoo.com/mrss/\">"
               "<channel><title>%s</title><link>%s/</link><description>%s</description><language>ru</language>"
               % (html.escape(SITE_TITLE), BASE, html.escape(SITE_DESC)))
    parts = [channel]
    for i in sorted(items, key=lambda x: x["dt"], reverse=True):
        parts.append(
            "<item><title>%s</title><link>%s</link><guid isPermaLink=\"true\">%s</guid>"
            "<pubDate>%s</pubDate>"
            "<description>%s</description>"
            "<category>native-draft</category><category>format-article</category>"
            "<category>index</category><category>comment-all</category>"
            "<enclosure url=\"%s\" type=\"image/png\"/>"
            "<content:encoded><![CDATA[%s]]></content:encoded></item>"
            % (html.escape(i["headline"]), i["url"], i["url"], format_datetime(i["dt"]),
               html.escape(i["text"][:200]), i["card"], i["inner"])
        )
    parts.append("</channel></rss>")
    with open(OUT + "/feed.xml", "w", encoding="utf-8") as f:
        f.write("".join(parts))

    print("built", len(items), "items")


if __name__ == "__main__":
    main()
