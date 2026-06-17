"""
Assembles context from the business instance folder into a single string
that gets passed to the LLM alongside the system prompt.

Instance folder layout (in the business repo):
  vantage/
    business_profile.json
    memory/
      experiments.json
      learnings.json
      activity_log.jsonl     (append-only, newest at end)
    data/latest/
      catalog_metrics.csv    (per-catalog/SKU metrics)
      platform_summary.csv   (platform totals)
"""

import json
import os
import csv
from pathlib import Path
from datetime import datetime


ACTIVITY_LOG_TAIL = 50  # last N events loaded into context


def build_context(instance_path: str, mode: str = "nightly") -> tuple[str, dict]:
    """
    Returns (context_string, business_profile_dict).
    context_string is injected as the user message to the LLM.
    """
    base = Path(instance_path)
    memory = base / "memory"
    data = base / "data" / "latest"

    profile = _load_json(base / "business_profile.json")
    experiments = _load_json(memory / "experiments.json")
    learnings = _load_json(memory / "learnings.json")
    activity = _load_activity_log(memory / "activity_log.jsonl", tail=ACTIVITY_LOG_TAIL)
    catalog_data = _load_csv(data / "catalog_metrics.csv")
    platform_data = _load_csv(data / "platform_summary.csv")

    context = f"""
## CONTEXT LOADED: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
## MODE: {mode}

---

## BUSINESS PROFILE
{json.dumps(profile, indent=2)}

---

## CATALOG / SKU DATA (latest)
{catalog_data}

---

## PLATFORM SUMMARY
{platform_data}

---

## ACTIVE & RECENT EXPERIMENTS
{json.dumps(experiments, indent=2)}

---

## ACCUMULATED LEARNINGS
{json.dumps(learnings, indent=2)}

---

## RECENT ACTIVITY LOG (last {ACTIVITY_LOG_TAIL} events)
{activity}

---

{"## TASK: Perform the nightly analysis. Return structured JSON only as per output schema." if mode == "nightly" else ""}
""".strip()

    return context, profile


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_activity_log(path: Path, tail: int = 50) -> str:
    if not path.exists():
        return "(no activity yet)"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    recent = lines[-tail:] if len(lines) > tail else lines
    return "".join(recent).strip() or "(no activity yet)"


def _load_csv(path: Path) -> str:
    if not path.exists():
        return "(no data file found)"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    return content if content else "(empty data file)"
