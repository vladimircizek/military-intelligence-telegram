import asyncio
import logging
import os
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import feedparser
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
from openai import AsyncOpenAI

from briefing import collect_articles, build_briefing

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("milintel")

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Prague"))
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MODEL = os.getenv("OPENAI_MODEL", "gpt-6-mini")


async def authorized(update: Update) -> bool:
    allowed = os.getenv("TELEGRAM_ALLOWED_USER_ID")
    if not allowed:
        return True
    user = update.effective_user
    return bool(user and str(user.id) == allowed)


async def send_report(chat_id: int, context: ContextTypes.DEFAULT_TYPE, urgent=False):
    articles = await asyncio.to_thread(collect_articles, urgent=urgent)
    if not articles:
        await context.bot.send_message(chat_id=chat_id, text="Nenašel jsem dostatek nových zpráv z nastavených zdrojů.")
        return
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    report = await build_briefing(client, articles, urgent=urgent)
    # Telegram limit is 4096 chars; split at paragraph boundaries.
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
            chat_id=chat_id, text=chunk, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return
    await update.message.reply_text(
        "🛰 <b>Military Intelligence</b>\n\n"
        "Příkazy:\n"
        "/report – aktuální denní přehled\n"
        "/urgent – mimořádný přehled posledních událostí\n"
        "/sources – seznam sledovaných zdrojů\n"
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
    await send_report(update.effective_chat.id, context)


async def urgent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return
    await update.message.reply_text("🚨 Prověřuji poslední mimořádné události…")
    await send_report(update.effective_chat.id, context, urgent=True)


async def sources_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return
    await update.message.reply_text(
        "Sledované zdroje (RSS): BBC World, BBC Europe, BBC Middle East, "
        "Institute for the Study of War (ISW), NATO News a UN News. "
        "Zdroje se mohou měnit podle dostupnosti jejich RSS kanálů."
    )


async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return the chat and user IDs to the user in the private bot chat."""
    if not await authorized(update):
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else "neznámé"
    await update.message.reply_text(
        f"TELEGRAM_CHAT_ID: {chat_id}\\n"
        f"TELEGRAM_ALLOWED_USER_ID: {user_id}\\n\\n"
        "Tyto hodnoty vlož do Railway → worker → Variables."
    )


async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        log.error("TELEGRAM_CHAT_ID is missing; daily report not sent.")
        return
    try:
        await send_report(int(CHAT_ID), context)
    except Exception:
        log.exception("Daily briefing failed")


async def post_init(app: Application):
    # Run every day at 19:00 local Prague time.
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
