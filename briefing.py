import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser

FEEDS = [
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Europe", "https://feeds.bbci.co.uk/news/world/europe/rss.xml"),
    ("BBC Middle East", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml"),
    ("Institute for the Study of War (ISW)", "https://www.understandingwar.org/rss.xml"),
    ("NATO News", "https://www.nato.int/cps/en/natohq/news_rss.xml"),
    ("UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
]

KEYWORDS = (
    "military", "war", "attack", "strike", "missile", "drone", "troops",
    "army", "defence", "defense", "nato", "ukraine", "russia", "iran",
    "israel", "gaza", "lebanon", "syria", "yemen", "hezbollah", "hamas",
    "mobilisation", "mobilization", "weapon", "arms", "air force", "navy",
    "frontline", "offensive", "airstrike", "intercept", "explosion",
)

def _date(entry):
    try:
        if getattr(entry, "published", None):
            dt = parsedate_to_datetime(entry.published)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception:
        pass
    return datetime.now(timezone.utc) - timedelta(days=1)

def collect_articles(urgent=False):
    now = datetime.now(timezone.utc)
    horizon = timedelta(hours=24 if urgent else 36)
    items = []
    for source, url in FEEDS:
        try:
            parsed = feedparser.parse(url)
            for e in parsed.entries[:30]:
                title = (getattr(e, "title", "") or "").strip()
                summary = re.sub("<[^>]+>", " ", getattr(e, "summary", "") or "")
                text = f"{title} {summary}".lower()
                if not any(k in text for k in KEYWORDS):
                    continue
                published = _date(e)
                if now - published > horizon or published > now + timedelta(hours=2):
                    continue
                link = getattr(e, "link", "")
                if not title or not link:
                    continue
                items.append({
                    "source": source, "title": title, "summary": summary[:1200],
                    "link": link, "published": published.isoformat()
                })
        except Exception:
            continue
    # Deduplicate by normalized headline
    unique = {}
    for item in sorted(items, key=lambda x: x["published"], reverse=True):
        key = re.sub(r"[^a-z0-9]+", "", item["title"].lower())
        unique.setdefault(key, item)
    return list(unique.values())[:35]


async def build_briefing(client, articles, urgent=False):
    now = datetime.now().astimezone().strftime("%d.%m.%Y %H:%M")
    prompt = f"""
Jsi analytik otevřených zdrojů (OSINT). Vytvoř česky stručný, věcný vojensko-bezpečnostní přehled.
Čas sestavení: {now}. Režim: {"MIMOŘÁDNÝ" if urgent else "DENNÍ"}.
Priorita: Evropa a válka Ruska proti Ukrajině, Blízký východ; dále významné události globálně.
Zahrň pouze podstatné události. U každé uveď:
- čas a místo (jen pokud je zdroj podporuje),
- co se stalo a co je potvrzeno,
- proč je to důležité / možné dopady,
- stav ověření: POTVRZENO / TVRZENÍ STRANY / NEOVĚŘENO.
Nikdy nepřeváděj tvrzení bojující strany na potvrzený fakt. Pokud se zdroje rozcházejí, výslovně to napiš.
Nevymýšlej detaily, počty, čas ani geografii. Když není dost významných zpráv, napiš to.
Na konci přidej krátké „Hodnocení situace“ a seznam zdrojů s odkazy.
Vrať HTML kompatibilní s Telegramem: používej pouze <b>, <i>, <a href="..."> a běžné odstavce.
Nepoužívej Markdown ani tabulky.

Dostupné články (považuj je za vstupní zdroje, nikoli za automaticky ověřené skutečnosti):
{articles}
"""
    response = await client.responses.create(
        model=__import__("os").getenv("OPENAI_MODEL", "gpt-6-mini"),
        input=prompt,
    )
    return response.output_text
