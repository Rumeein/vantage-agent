"""
Vantage Telegram bot — interactive layer.

Setup:
  1. Create a bot via @BotFather on Telegram → get TELEGRAM_BOT_TOKEN
  2. Find your chat ID: message @userinfobot → get TELEGRAM_ALLOWED_CHAT_IDS
  3. Add both to your .env file in the business vantage/ folder

Usage:
  python telegram_bot.py --instance-path "D:/vantage-rumee"

What it does:
  - Polls for new messages
  - For each allowed message: assembles full business context + user message → calls LLM → replies
  - Logs every exchange to activity_log.jsonl
  - Supports commands:
      /status  — summary of active experiments
      /alerts  — any current urgent alerts (re-runs nightly analysis if stale)
      /exp <id> — detail on a specific experiment
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "system_prompt.md"
CONVERSATION_HISTORY_LIMIT = 10  # exchanges kept in context per session


def run(instance_path: str):
    load_dotenv(Path(instance_path) / ".env")

    try:
        from telegram import Update
        from telegram.ext import Application, MessageHandler, CommandHandler, filters
    except ImportError:
        print("Install python-telegram-bot: pip install python-telegram-bot")
        sys.exit(1)

    from context_builder import build_context
    from llm_client import call_llm
    from memory_writer import append_activity_log

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    allowed_ids_raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
    allowed_ids = set(int(x.strip()) for x in allowed_ids_raw.split(",") if x.strip())

    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    session_histories: dict[int, list] = {}

    async def handle_message(update: Update, context):
        chat_id = update.effective_chat.id
        if allowed_ids and chat_id not in allowed_ids:
            await update.message.reply_text("Unauthorized.")
            return

        user_text = update.message.text.strip()
        logger.info(f"Message from {chat_id}: {user_text}")

        # build base context
        base_context, profile = build_context(instance_path, mode="telegram")

        # build conversation history
        history = session_histories.get(chat_id, [])
        history.append({"role": "user", "content": user_text})
        if len(history) > CONVERSATION_HISTORY_LIMIT * 2:
            history = history[-(CONVERSATION_HISTORY_LIMIT * 2):]

        # assemble user message: base context + conversation
        full_user_message = base_context + "\n\n---\n\n## CONVERSATION\n"
        for turn in history[:-1]:
            prefix = "You asked" if turn["role"] == "user" else "Vantage replied"
            full_user_message += f"\n{prefix}: {turn['content']}"
        full_user_message += f"\n\nCurrent question: {user_text}"

        try:
            reply = call_llm(system_prompt, full_user_message, profile)
        except Exception as e:
            reply = f"Error calling LLM: {e}"
            logger.error(reply)

        history.append({"role": "assistant", "content": reply})
        session_histories[chat_id] = history

        await update.message.reply_text(reply)

        append_activity_log(instance_path, {
            "ts": _now(),
            "event": "telegram_message",
            "chat_id": chat_id,
            "user_message": user_text,
            "reply_length": len(reply)
        })

    async def handle_status(update: Update, context):
        chat_id = update.effective_chat.id
        if allowed_ids and chat_id not in allowed_ids:
            return

        exps_path = Path(instance_path) / "memory" / "experiments.json"
        if not exps_path.exists():
            await update.message.reply_text("No experiments yet.")
            return

        with open(exps_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        exps = data.get("experiments", [])
        active = [e for e in exps if e.get("status") == "monitoring"]
        suggested = [e for e in exps if e.get("status") == "suggested"]
        success = [e for e in exps if e.get("status") == "success"]
        failed = [e for e in exps if e.get("status") == "failure"]

        lines = [
            f"Vantage Status",
            f"Suggested (not yet implemented): {len(suggested)}",
            f"Monitoring: {len(active)}",
            f"Succeeded: {len(success)}",
            f"Failed: {len(failed)}",
        ]
        if suggested:
            lines.append("\nTop suggestion:")
            top = suggested[0]
            lines.append(f"  [{top['id']}] {top['catalog']} — {top['change_required']}")

        await update.message.reply_text("\n".join(lines))

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info(f"Vantage bot started. Watching instance: {instance_path}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vantage Telegram bot")
    parser.add_argument("--instance-path", required=True, help="Path to the business vantage/ folder")
    args = parser.parse_args()
    run(args.instance_path)
