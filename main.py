import asyncio
import html
import logging
import os
import re
from datetime import time
from html.parser import HTMLParser
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from briefing import build_briefing, collect_articles

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("milintel")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Prague"))
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]


def _parse_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    result = []
    for item in re.split(r"[,;\s]+", raw.strip()):
        item = item.strip()
        if item and item not in result:
            result.append(item)
    return result


def recipient_chat_ids() -> list[int]:
    # Prefer the plural variable; preserve compatibility with the old single-ID variable.
    raw = os.getenv("TELEGRAM_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID", "")
    ids = []
    for item in _parse_ids(raw):
        try:
            ids.append(int(item))
        except ValueError:
            log.error("Ignoring invalid Telegram chat ID: %s", item)
    return ids


async def authorized(update: Update) -> bool:
    allowed = _parse_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS"))
    if not allowed:
        allowed = _parse_ids(os.getenv("TELEGRAM_ALLOWED_USER_ID"))
    if not allowed:
        return True
    user = update.effective_user
    return bool(user and str(user.id) in allowed)


class TelegramHTMLSanitizer(HTMLParser):
    ALLOWED = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []

    def _newline(self):
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag in {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._newline()
            return
        if tag in {"br", "hr"}:
            self._newline()
            return
        if tag == "li":
            self._newline()
            self.parts.append("• ")
            return
        if tag == "a":
            href = html.unescape(attrs.get("href", "") or "")
            parsed = urlparse(href)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                self.parts.append(f'<a href="{html.escape(href, quote=True)}">')
                self.stack.append("a")
            return
        if tag in self.ALLOWED:
            self.parts.append(f"<{tag}>")
            self.stack.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "li"}:
            self._newline()
            return
        if tag in ({"a"} | self.ALLOWED) and tag in self.stack:
            while self.stack:
                open_tag = self.stack.pop()
                self.parts.append(f"</{open_tag}>")
                if open_tag == tag:
                    break

    def handle_data(self, data):
        self.parts.append(html.escape(data))

    def result(self):
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def sanitize_telegram_html(value: str) -> str:
    parser = TelegramHTMLSanitizer()
    parser.feed(value or "")
    parser.close()
    return parser.result()


async def generate_report(urgent: bool = False) -> str:
    articles = await asyncio.to_thread(collect_articles, urgent=urgent)
    if not articles:
        return "Nenašel jsem dostatek nových zpráv z nastavených zdrojů."
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    report = await build_briefing(client, articles, urgent=urgent)
    return sanitize_telegram_html(report)


async def deliver_report(chat_id: int, context: ContextTypes.DEFAULT_TYPE, report: str):
    chunks, current = [], ""
    for paragraph in report.split("\n\n"):
        candidate = (current + "\n\n" + paragraph).strip()
        if len(candidate) > 3900 and current:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    for chunk in chunks:
        await context.bot.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


async def send_report(chat_id: int, context: ContextTypes.DEFAULT_TYPE, urgent=False):
    report = await generate_report(urgent=urgent)
    await deliver_report(chat_id, context, report)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        await update.message.reply_text(
            "Vítej v Military Intelligence. Tento účet zatím není povolen pro ovládání bota.\n"
            "Pro přidání mezi příjemce pošli /id a předej zobrazená ID správci bota."
        )
        return
    await update.message.reply_text(
        "🛰 <b>Military Intelligence</b>\n\n"
        "Příkazy:\n"
        "/report – aktuální přehled\n"
        "/urgent – mimořádný přehled\n"
        "/sources – seznam sledovaných zdrojů\n"
        "/id – zobrazí Telegram ID\n"
        "/help – nápověda\n\n"
        "Automatický přehled je plánován každý den v 19:00 (Europe/Prague).",
        parse_mode=ParseMode.HTML,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return
    await update.message.reply_text("🔎 Prověřuji zdroje a připravuji přehled…")
    try:
        await send_report(update.effective_chat.id, context)
    except Exception:
        log.exception("Manual report failed")
        await update.message.reply_text("Při sestavení nebo odeslání přehledu nastala chyba. Zkontroluji ji v Railway logu.")


async def urgent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return
    await update.message.reply_text("🚨 Prověřuji poslední mimořádné události…")
    try:
        await send_report(update.effective_chat.id, context, urgent=True)
    except Exception:
        log.exception("Urgent report failed")
        await update.message.reply_text("Při sestavení nebo odeslání mimořádného přehledu nastala chyba. Zkontroluji ji v Railway logu.")


async def sources_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return
    await update.message.reply_text(
        "Sledované zdroje (RSS): BBC World, BBC Europe, BBC Middle East, "
        "Institute for the Study of War (ISW), NATO News a UN News. "
        "Zdroje se mohou měnit podle dostupnosti jejich RSS kanálů."
    )


async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Deliberately available to new recipients: it reveals only the caller's own IDs.
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else "neznámé"
    await update.message.reply_text(
        f"TELEGRAM_CHAT_ID: {chat_id}\n"
        f"TELEGRAM_USER_ID: {user_id}\n\n"
        "Předej tato čísla správci bota, aby tě mohl přidat mezi příjemce."
    )


async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    chat_ids = recipient_chat_ids()
    if not chat_ids:
        log.error("No recipient Telegram chat IDs configured. Set TELEGRAM_CHAT_IDS.")
        return
    try:
        # Build once; distribute the same report to all recipients to avoid duplicate API usage.
        report = await generate_report()
    except Exception:
        log.exception("Daily briefing generation failed")
        return
    for chat_id in chat_ids:
        try:
            await deliver_report(chat_id, context, report)
            log.info("Daily briefing sent to chat ID %s", chat_id)
        except Exception:
            log.exception("Daily briefing delivery failed for chat ID %s", chat_id)


async def post_init(app: Application):
    app.job_queue.run_daily(daily_job, time=time(hour=19, minute=0, tzinfo=TZ), name="daily-briefing")
    log.info("Daily briefing scheduled for 19:00 %s", TZ)


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("urgent", urgent_cmd))
    app.add_handler(CommandHandler("sources", sources_cmd))
    app.add_handler(CommandHandler("id", id_cmd))
    log.info("Military Intelligence Telegram bot is starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
