import html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import feedparser

log = logging.getLogger("milintel.briefing")

FEEDS = [
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Europe", "https://feeds.bbci.co.uk/news/world/europe/rss.xml"),
    ("BBC Middle East", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml"),
    ("Al Jazeera English", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("France 24 English", "https://www.france24.com/en/rss"),
    ("Deutsche Welle", "https://rss.dw.com/xml/rss-en-all"),
    ("The Guardian World", "https://www.theguardian.com/world/rss"),
    ("Sky News World", "https://feeds.skynews.com/feeds/rss/world.xml"),
    ("NPR News", "https://feeds.npr.org/1001/rss.xml"),
    ("Institute for the Study of War (ISW)", "https://www.understandingwar.org/rss.xml"),
    ("RUSI Latest Publications", "https://www.rusi.org/rss/latest-publications.xml"),
    ("RUSI Latest Commentary", "https://www.rusi.org/rss/latest-commentary.xml"),
    ("Defense News", "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml"),
    ("Breaking Defense", "https://breakingdefense.com/feed/"),
    ("NATO News", "https://www.nato.int/cps/en/natohq/news_rss.xml"),
    ("UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
    ("US Department of Defense", "https://www.defense.gov/News/RSS/"),
    ("The Diplomat", "https://thediplomat.com/feed/"),
    ("Times of Israel", "https://www.timesofisrael.com/feed/"),
    ("South China Morning Post", "https://www.scmp.com/rss/91/feed"),
]

KEYWORDS = (
    "military", "war", "attack", "strike", "missile", "drone", "troops", "army",
    "defence", "defense", "nato", "ukraine", "russia", "iran", "israel", "gaza",
    "lebanon", "syria", "yemen", "hezbollah", "hamas", "mobilisation", "mobilization",
    "weapon", "arms", "air force", "navy", "frontline", "offensive", "airstrike",
    "intercept", "explosion", "artillery", "ammunition", "warship", "submarine",
    "fighter", "aircraft", "sanctions", "cyberattack", "military aid", "security",
    "ceasefire", "invasion", "ballistic", "hypersonic", "deployment", "exercise",
)

def _clean(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()

def _date(entry):
    for field in ("published", "updated"):
        try:
            value = getattr(entry, field, None)
            if value:
                dt = parsedate_to_datetime(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
        except Exception:
            pass
    for field in ("published_parsed", "updated_parsed"):
        value = getattr(entry, field, None)
        if value:
            try:
                from calendar import timegm
                return datetime.fromtimestamp(timegm(value), timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc) - timedelta(days=1)

def _valid_url(value):
    p = urlparse(value or "")
    return value if p.scheme in ("http", "https") and p.netloc else ""

def _image(entry):
    for field in ("media_content", "media_thumbnail", "enclosures"):
        for item in getattr(entry, field, []) or []:
            if isinstance(item, dict):
                url = _valid_url(item.get("url") or item.get("href"))
                mime = item.get("type", "")
                if url and (not mime or mime.startswith("image/")):
                    return url
    image_obj = getattr(entry, "image", None)
    if isinstance(image_obj, dict):
        url = _valid_url(image_obj.get("href") or image_obj.get("url"))
        if url:
            return url
    bodies = [getattr(entry, "summary", "") or ""]
    bodies += [x.get("value", "") for x in (getattr(entry, "content", []) or []) if isinstance(x, dict)]
    for body in bodies:
        m = re.search(r"<img[^>]+src=['\"]([^'\"]+)['\"]", body, re.I)
        if m:
            url = _valid_url(html.unescape(m.group(1)))
            if url:
                return url
    return ""

def collect_articles(urgent=False):
    now = datetime.now(timezone.utc)
    horizon = timedelta(hours=24 if urgent else 36)
    items = []
    for source, url in FEEDS:
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": "MilitaryIntelligenceBriefing/1.0"})
            if getattr(feed, "bozo", False):
                log.warning("RSS warning %s: %s", source, getattr(feed, "bozo_exception", "unknown"))
            if not feed.entries:
                log.warning("RSS feed empty/unavailable: %s", source)
            for entry in feed.entries[:40]:
                title = _clean(getattr(entry, "title", ""))
                summary = _clean(getattr(entry, "summary", ""))
                if not title or not any(k in f"{title} {summary}".lower() for k in KEYWORDS):
                    continue
                published = _date(entry)
                if now - published > horizon or published > now + timedelta(hours=2):
                    continue
                link = _valid_url(getattr(entry, "link", ""))
                if link:
                    items.append({
                        "source": source, "title": title, "summary": summary[:1600],
                        "link": link, "published": published.isoformat(), "image": _image(entry)
                    })
        except Exception:
            log.exception("Could not read RSS feed: %s", source)

    unique = {}
    for item in sorted(items, key=lambda x: x["published"], reverse=True):
        key = re.sub(r"[^a-z0-9]+", "", item["title"].lower())
        if key not in unique:
            unique[key] = item
        elif item["image"] and not unique[key].get("image"):
            unique[key]["image"] = item["image"]
    result = list(unique.values())
    log.info("Collected %s relevant articles from %s configured RSS feeds", len(result), len(FEEDS))
    return result[:55]

async def build_briefing(client, articles, urgent=False):
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    payload = [{k: a[k] for k in ("source", "title", "summary", "published", "link")} for a in articles]
    prompt = f"""
Jsi analytik OSINT. Vytvoř česky věcný vojensko-bezpečnostní přehled.
Čas: {now}. Režim: {"MIMOŘÁDNÝ" if urgent else "DENNÍ"}.
Priority: 1) Evropa a válka Ruska proti Ukrajině, 2) Blízký východ,
3) NATO a evropská bezpečnost, 4) Indo-Pacifik a ostatní svět.

Sluč články o stejné události, ale vypiš relevantní zdroje. Více článků samo o sobě
neznamená nezávislé potvrzení. Rozlišuj zpravodajství, oficiální vyjádření a analýzy.
Reuters a AP nejsou v tomto seznamu přímo zastoupeny; nikdy netvrď, že byly použity.

Vyber maximálně 8 důležitých událostí. U každé uveď:
- čas a místo pouze pokud je zdroj podporuje,
- co se stalo a co je skutečně potvrzeno,
- vojenský/strategický význam a možné dopady,
- stav: POTVRZENO VÍCE ZDROJI / POTVRZENO OFICIÁLNÍM ZDROJEM /
  TVRZENÍ STRANY / NEOVĚŘENO,
- odkazy na 1–3 původní články.
Tvrzení bojující strany nikdy nepřeváděj na potvrzený fakt. Rozpory výslovně uveď.
Analýzu označ jako hodnocení. Nevymýšlej fakta, počty, čas ani geografii.
Na konci přidej „Hodnocení situace“ a seznam skutečně použitých zdrojů.
Vrať HTML kompatibilní s Telegramem: pouze <b>, <i>, <a href="..."> a běžné odstavce.
Bez Markdownu a tabulek.

Články:
{json.dumps(payload, ensure_ascii=False)}
"""
    response = await client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
        input=prompt,
    )
    return response.output_text
