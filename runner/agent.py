"""
Vantage nightly runner.

Usage:
  python agent.py --instance-path "D:/Claude RuMee Dashbord/vantage"

What it does:
  1. Loads system prompt + business context
  2. Calls the LLM
  3. Parses response JSON
  4. Updates experiments.json and learnings.json
  5. Appends to activity_log.jsonl
  6. Prints summary to stdout (cron logs capture this)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from context_builder import build_context
from llm_client import call_llm
from memory_writer import update_memory, append_activity_log

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "system_prompt.md"


def run(instance_path: str):
    load_dotenv(Path(instance_path) / ".env")

    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    context, profile = build_context(instance_path, mode="nightly")

    print(f"[{_now()}] Running Vantage nightly for: {profile.get('business', {}).get('name', 'unknown')}")

    raw_response = call_llm(system_prompt, context, profile)

    try:
        result = _parse_json_response(raw_response)
    except Exception as e:
        _log_error(instance_path, f"Failed to parse LLM response: {e}\n\nRaw:\n{raw_response}")
        sys.exit(1)

    update_memory(instance_path, result)

    append_activity_log(instance_path, {
        "ts": _now(),
        "event": "nightly_run",
        "summary": result.get("summary", ""),
        "alerts_count": len(result.get("alerts", [])),
        "new_experiments_count": len(result.get("new_experiments", [])),
        "monitoring_updates_count": len(result.get("monitoring_updates", [])),
        "learnings_added": len(result.get("learnings_update", []))
    })

    print(f"[{_now()}] Done. Summary: {result.get('summary', '')}")
    print(f"  Alerts: {len(result.get('alerts', []))}")
    print(f"  New experiments: {len(result.get('new_experiments', []))}")
    print(f"  Monitoring updates: {len(result.get('monitoring_updates', []))}")


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
    return json.loads(raw)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_error(instance_path: str, message: str):
    path = Path(instance_path) / "memory" / "activity_log.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now(), "event": "error", "message": message}) + "\n")
    print(f"ERROR: {message}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vantage nightly agent runner")
    parser.add_argument("--instance-path", required=True, help="Path to the business vantage/ folder")
    args = parser.parse_args()
    run(args.instance_path)
