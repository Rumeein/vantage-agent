"""
Writes LLM output back to the business instance memory files.
All writes are additive — nothing is deleted.
After every write, memory files are committed and pushed to the GitHub repo.
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def update_memory(instance_path: str, result: dict):
    base = Path(instance_path) / "memory"
    base.mkdir(parents=True, exist_ok=True)

    _update_experiments(base / "experiments.json", result)
    _update_learnings(base / "learnings.json", result)
    _commit_and_push(instance_path, "auto: vantage memory update")


def append_activity_log(instance_path: str, event: dict):
    path = Path(instance_path) / "memory" / "activity_log.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    _commit_and_push(instance_path, f"auto: vantage activity log — {event.get('event', 'event')}")


def _commit_and_push(instance_path: str, message: str):
    """Commit memory files and push to GitHub. Logs warnings but never crashes the caller."""
    repo_root = str(Path(instance_path).parent)
    memory_dir = str(Path(instance_path) / "memory")

    try:
        subprocess.run(
            ["git", "add", memory_dir],
            cwd=repo_root, check=True, capture_output=True
        )
        # Only commit if something is actually staged
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_root, capture_output=True
        )
        if diff.returncode == 0:
            return  # nothing to commit
        subprocess.run(
            ["git", "commit", "-m", message,
             "--author=Vantage <rumeein@gmail.com>"],
            cwd=repo_root, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=repo_root, check=True, capture_output=True
        )
        print(f"[memory_writer] pushed: {message}")
    except subprocess.CalledProcessError as e:
        print(f"[memory_writer] git push failed (continuing): {e.stderr.decode().strip()}")
    except Exception as e:
        print(f"[memory_writer] git push error (continuing): {e}")


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
