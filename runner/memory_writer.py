"""
Writes LLM output back to the business instance memory files.
All writes are additive — nothing is deleted.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def update_memory(instance_path: str, result: dict):
    base = Path(instance_path) / "memory"
    base.mkdir(parents=True, exist_ok=True)

    _update_experiments(base / "experiments.json", result)
    _update_learnings(base / "learnings.json", result)


def append_activity_log(instance_path: str, event: dict):
    path = Path(instance_path) / "memory" / "activity_log.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _update_experiments(path: Path, result: dict):
    data = _load_json(path, default={"schema_version": "1.0", "experiments": []})
    existing = {e["id"]: e for e in data["experiments"]}

    # add new experiments
    for exp in result.get("new_experiments", []):
        if exp["id"] not in existing:
            exp["status"] = "suggested"
            exp["created_at"] = _now()
            existing[exp["id"]] = exp

    # apply monitoring updates
    for update in result.get("monitoring_updates", []):
        eid = update["experiment_id"]
        if eid in existing:
            existing[eid]["last_monitored"] = _now()
            existing[eid]["current_metrics"] = update.get("current_metrics", {})
            existing[eid]["trending"] = update.get("trending")
            if update.get("conclusion") in ("success", "failure"):
                existing[eid]["status"] = update["conclusion"]
                existing[eid]["closed_at"] = _now()
                existing[eid]["outcome_note"] = update.get("next_action", "")

    data["experiments"] = list(existing.values())
    _save_json(path, data)


def _update_learnings(path: Path, result: dict):
    data = _load_json(path, default={"schema_version": "1.0", "learnings": []})
    existing_texts = {l["learning"] for l in data["learnings"]}

    for item in result.get("learnings_update", []):
        if item["learning"] not in existing_texts:
            item["added_at"] = _now()
            data["learnings"].append(item)
            existing_texts.add(item["learning"])

    _save_json(path, data)


def _load_json(path: Path, default=None):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else {}


def _save_json(path: Path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
