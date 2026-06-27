"""
Reads all round files from eval/rounds/ + current judge_compare_results.json,
generates docs/qa_report.html with collapsible rounds and collapsible questions,
then commits and pushes to GitHub Pages.

Usage:
  python eval/publish_qa.py
  python eval/publish_qa.py --no-push
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

REPO_ROOT    = Path(__file__).parent.parent
EVAL_DIR     = Path(__file__).parent
ROUNDS_DIR   = EVAL_DIR / "rounds"
CURRENT_FILE = EVAL_DIR / "judge_compare_results.json"
DOCS_DIR     = REPO_ROOT / "docs"
OUT_HTML     = DOCS_DIR / "qa_report.html"

SCORE_COLOR = {
    "pass":    ("#1a7f37", "#dafbe1"),
    "partial": ("#9a6700", "#fff8c5"),
    "fail":    ("#cf222e", "#ffebe9"),
    "error":   ("#6e7781", "#f6f8fa"),
    "?":       ("#6e7781", "#f6f8fa"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    rounds = _load_all_rounds()
    if not rounds:
        print("No results found. Run judge_compare.py first.")
        sys.exit(1)

    DOCS_DIR.mkdir(exist_ok=True)
    html = _build_html(rounds)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"HTML written: {OUT_HTML}  ({len(rounds)} round(s), {sum(len(r['results']) for r in rounds)} questions)")

    if args.no_push:
        print("--no-push set. Done.")
        return

    _git_push()
    print(f"\nLive at: https://rumeein.github.io/vantage-agent/qa_report.html")


def _load_all_rounds() -> list:
    """Load all round files (newest first) + current results if not already archived."""
    rounds = []

    # Archived rounds
    if ROUNDS_DIR.exists():
        for f in sorted(ROUNDS_DIR.glob("round_*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                data["_source"] = f.name
                rounds.append(data)
            except Exception:
                pass

    # Current working file — include if not already in rounds
    if CURRENT_FILE.exists():
        try:
            data = json.loads(CURRENT_FILE.read_text(encoding="utf-8"))
            current_date = data.get("run_date", "")
            already_archived = any(r.get("run_date") == current_date for r in rounds)
            if not already_archived and data.get("results"):
                data["_source"] = "current"
                rounds.insert(0, data)
        except Exception:
            pass

    return rounds


def _build_html(rounds: list) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_q = sum(len(r["results"]) for r in rounds)

    round_blocks = "\n".join(_round_block(r, i + 1) for i, r in enumerate(rounds))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vantage QA Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 960px; margin: 40px auto; padding: 0 20px; color: #1f2328; background: #fff; }}
  h1   {{ font-size: 1.4em; border-bottom: 1px solid #d0d7de; padding-bottom: 10px; }}
  .meta {{ color: #656d76; font-size: 0.85em; margin-bottom: 30px; }}

  /* Round */
  details.round {{ border: 1px solid #d0d7de; border-radius: 8px; margin-bottom: 16px; }}
  details.round > summary {{
    padding: 14px 18px; font-weight: 600; font-size: 1em;
    background: #f6f8fa; border-radius: 8px; cursor: pointer; list-style: none;
    display: flex; align-items: center; gap: 10px;
  }}
  details.round > summary::-webkit-details-marker {{ display: none; }}
  details.round > summary::before {{ content: "▶"; font-size: 0.7em; color: #656d76; transition: transform 0.2s; }}
  details.round[open] > summary::before {{ transform: rotate(90deg); }}
  details.round[open] > summary {{ border-radius: 8px 8px 0 0; border-bottom: 1px solid #d0d7de; }}
  .round-body {{ padding: 14px; }}
  .round-summary {{ font-size: 0.85em; color: #656d76; margin-bottom: 12px; }}

  /* Question */
  details.question {{ border: 1px solid #d0d7de; border-radius: 6px; margin-bottom: 10px; }}
  details.question > summary {{
    padding: 10px 14px; cursor: pointer; list-style: none;
    display: flex; align-items: center; gap: 8px; font-size: 0.92em;
  }}
  details.question > summary::-webkit-details-marker {{ display: none; }}
  details.question > summary::before {{ content: "▶"; font-size: 0.65em; color: #656d76; transition: transform 0.2s; flex-shrink: 0; }}
  details.question[open] > summary::before {{ transform: rotate(90deg); }}
  details.question[open] > summary {{ border-bottom: 1px solid #d0d7de; }}
  details.question.split {{ border-color: #d4a72c; }}
  details.question.split > summary {{ background: #fff8c5; border-radius: 6px; }}
  details.question[open].split > summary {{ border-radius: 6px 6px 0 0; }}

  .q-id   {{ font-weight: 700; color: #656d76; font-size: 0.85em; flex-shrink: 0; }}
  .q-text {{ flex: 1; }}
  .badges {{ display: flex; gap: 5px; flex-shrink: 0; }}

  /* Answer + judges */
  .answer {{
    padding: 12px 14px; background: #f6f8fa; border-bottom: 1px solid #d0d7de;
    white-space: pre-wrap; font-size: 0.85em; max-height: 240px; overflow-y: auto;
  }}
  .judges {{ display: grid; grid-template-columns: 1fr 1fr 1fr; }}
  .judge  {{ padding: 10px 14px; font-size: 0.85em; }}
  .judge + .judge {{ border-left: 1px solid #d0d7de; }}
  .judge-name {{ font-size: 0.75em; color: #656d76; margin-bottom: 4px; }}
  .badge {{
    display: inline-block; padding: 1px 8px; border-radius: 20px;
    font-size: 0.78em; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;
  }}
  .reason {{ color: #444; line-height: 1.4; }}
</style>
</head>
<body>
<h1>Vantage — Judge Comparison Report</h1>
<p class="meta">Generated: {now} &nbsp;|&nbsp; {len(rounds)} round(s), {total_q} questions total</p>

{round_blocks}
</body>
</html>"""


def _round_block(r: dict, round_num: int) -> str:
    results  = r.get("results", [])
    run_date = r.get("run_date", "?")
    source   = r.get("_source", "")

    # Score summary across all judges
    all_scores = []
    for row in results:
        for jd in row.get("judges", {}).values():
            s = jd.get("score", "?")
            if s not in ("error", "?"):
                all_scores.append(s)
    passes  = all_scores.count("pass")
    total_v = len(all_scores)
    pct     = f"{passes/total_v*100:.0f}%" if total_v else "—"

    label = "current" if source == "current" else f"archived"
    q_cards = "\n".join(_question_card(row) for row in results)

    return f"""<details class="round">
  <summary>Round {round_num} &nbsp;<span style="font-weight:normal;color:#656d76;font-size:0.85em">{run_date} — {len(results)} questions — {passes}/{total_v} judge passes ({pct})</span></summary>
  <div class="round-body">
{q_cards}
  </div>
</details>"""


def _question_card(r: dict) -> str:
    qid     = r["id"]
    question = r["question"]
    answer  = r.get("vantage_answer", "(no answer)")
    judges  = r.get("judges", {})

    score_map = {k: judges.get(k, {}).get("score", "?")
                 for k in ("gemini_flash", "qwen3_32b", "llama_8b")}
    is_split = len({s for s in score_map.values() if s not in ("error","?")}) > 1
    split_class = " split" if is_split else ""

    # Mini badges for summary line
    badge_html = ""
    for key, label in [("gemini_flash","G"), ("qwen3_32b","Q"), ("llama_8b","L")]:
        s = score_map.get(key, "?")
        c, bg = SCORE_COLOR.get(s, SCORE_COLOR["?"])
        badge_html += f'<span class="badge" style="color:{c};background:{bg}">{label}:{s}</span>'

    # Judge detail columns
    judge_cols = ""
    for key, label in [("gemini_flash","Gemini 2.5 Flash Lite"),
                        ("qwen3_32b",  "Qwen3-32b"),
                        ("llama_8b",   "Llama 3.1 8b")]:
        jd     = judges.get(key, {})
        score  = jd.get("score", "?")
        reason = jd.get("reason", "")
        c, bg  = SCORE_COLOR.get(score, SCORE_COLOR["?"])
        judge_cols += (
            f'<div class="judge">'
            f'<div class="judge-name">{label}</div>'
            f'<span class="badge" style="color:{c};background:{bg}">{score}</span>'
            f'<div class="reason">{reason}</div>'
            f'</div>'
        )

    answer_esc = answer.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    return f"""  <details class="question{split_class}">
    <summary><span class="q-id">{qid}</span><span class="q-text">{question}</span><span class="badges">{badge_html}</span></summary>
    <div class="answer">{answer_esc}</div>
    <div class="judges">{judge_cols}</div>
  </details>"""


def _git_push():
    def run(cmd):
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode != 0 and result.stderr.strip():
            print(f"  git: {result.stderr.strip()}")
        return result.returncode == 0

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("Pushing to GitHub...")
    run(["git", "add", "docs/qa_report.html"])
    run(["git", "commit", "-m", f"qa: report update {now}"])
    run(["git", "push"])


if __name__ == "__main__":
    main()
