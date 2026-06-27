"""
Vantage Discord bot — interactive layer.

Setup:
  1. Create a bot at discord.com/developers → enable Message Content Intent
  2. Add DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID to your vantage/.env
  3. Invite the bot with Send Messages + Read Message History permissions

Usage:
  python discord_bot.py --instance-path "D:/vantage-rumee"

Commands (type in the watched channel):
  !status  — active/suggested experiment counts
  !alerts  — latest Vantage alerts from memory
  anything else — answered by the LLM with full business context
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
CONVERSATION_HISTORY_LIMIT = 10


def run(instance_path: str):
    load_dotenv(Path(instance_path) / ".env")

    try:
        import discord
    except ImportError:
        print("Install discord.py: pip install discord.py")
        sys.exit(1)

    from context_builder import build_context
    from llm_client import call_llm
    from memory_writer import append_activity_log

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("DISCORD_BOT_TOKEN not set in .env")
        sys.exit(1)

    channel_id_raw = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    if not channel_id_raw:
        print("DISCORD_CHANNEL_ID not set in .env")
        sys.exit(1)
    watched_channel_id = int(channel_id_raw)

    system_prompt_full = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    # Groq free tier: 12k TPM — keep system prompt under ~2k tokens
    system_prompt = system_prompt_full[:8000]
    # per-user conversation history within a session
    session_histories: dict[int, list] = {}

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info(f"Vantage bot ready as {client.user} — watching channel {watched_channel_id}")

    @client.event
    async def on_message(message: discord.Message):
        logger.info(f"on_message fired: author={message.author} channel={message.channel.id} content='{message.content[:50]}'")
        if message.author == client.user:
            return
        if message.channel.id != watched_channel_id:
            logger.info(f"Ignoring message — channel {message.channel.id} != watched {watched_channel_id}")
            return

        text = message.content.strip()
        if not text:
            logger.warning("Message content is empty — Message Content Intent may be OFF in Discord Developer Portal")
            return

        user_id = message.author.id

        # --- !status command ---
        if text.lower() == "!status":
            await message.channel.send(_status_text(instance_path))
            return

        # --- !alerts command ---
        if text.lower() == "!alerts":
            await message.channel.send(_alerts_text(instance_path))
            return

        # --- LLM Q&A ---
        async with message.channel.typing():
            base_context, profile = build_context(instance_path, mode="discord")
            # Groq free tier: 12k TPM — cap context to ~2k tokens
            if len(base_context) > 8000:
                base_context = base_context[:8000] + "\n...[context truncated]"

            history = session_histories.get(user_id, [])
            history.append({"role": "user", "content": text})
            if len(history) > CONVERSATION_HISTORY_LIMIT * 2:
                history = history[-(CONVERSATION_HISTORY_LIMIT * 2):]

            full_user_message = base_context + "\n\n---\n\n## CONVERSATION\n"
            for turn in history[:-1]:
                prefix = "You asked" if turn["role"] == "user" else "Vantage replied"
                full_user_message += f"\n{prefix}: {turn['content']}"
            full_user_message += f"\n\nCurrent question: {text}"

            try:
                reply = call_llm(system_prompt, full_user_message, profile)
            except Exception as e:
                reply = f"Error calling LLM: {e}"
                logger.error(reply)

        history.append({"role": "assistant", "content": reply})
        session_histories[user_id] = history

        # Discord message limit is 2000 chars — split if needed
        for chunk in _split(reply, 2000):
            await message.channel.send(chunk)

        append_activity_log(instance_path, {
            "ts": _now(),
            "event": "discord_message",
            "user_id": user_id,
            "user_name": str(message.author),
            "user_message": text,
            "reply_length": len(reply),
        })

    client.run(token)


def _status_text(instance_path: str) -> str:
    exps_path = Path(instance_path) / "memory" / "experiments.json"
    if not exps_path.exists():
        return "No experiments yet."
    with open(exps_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    exps = data.get("experiments", [])
    suggested = [e for e in exps if e.get("status") == "suggested"]
    active = [e for e in exps if e.get("status") == "monitoring"]
    success = [e for e in exps if e.get("status") == "success"]
    failed = [e for e in exps if e.get("status") == "failure"]
    lines = [
        "**Vantage Status**",
        f"Suggested: {len(suggested)}  |  Monitoring: {len(active)}  |  Succeeded: {len(success)}  |  Failed: {len(failed)}",
    ]
    if suggested:
        top = suggested[0]
        lines.append(f"\nTop suggestion: [{top['id']}] {top['catalog']} — {top['change_required']}")
    return "\n".join(lines)


def _alerts_text(instance_path: str) -> str:
    learnings_path = Path(instance_path) / "memory" / "learnings.json"
    if not learnings_path.exists():
        return "No alerts in memory."
    with open(learnings_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    alerts = data.get("alerts", [])
    if not alerts:
        return "No active alerts."
    lines = ["**Vantage Alerts**"]
    for a in alerts:
        lines.append(f"- [{a.get('severity','?').upper()}] {a.get('message', a)}")
    return "\n".join(lines)


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vantage Discord bot")
    parser.add_argument("--instance-path", required=True, help="Path to the business vantage/ folder")
    args = parser.parse_args()
    run(args.instance_path)
