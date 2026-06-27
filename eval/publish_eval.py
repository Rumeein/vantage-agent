"""
Publishes the latest run_eval.py results (eval_log.jsonl) as a live HTML page.
Dedupes to the most recent verdict per question, groups by scenario/category,
writes docs/eval_report.html, and pushes to GitHub Pages.

Usage:
  python eval/publish_eval.py            # build + push
  python eval/publish_eval.py --no-push  # build only

Live at: https://rumeein.github.io/vantage-agent/eval_report.html
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REPO_ROOT = Path(__file__).parent.parent
LOG_PATH = Path(__file__).parent / 'eval_log.jsonl'
DOCS_DIR = REPO_ROOT / 'docs'
OUT_HTML = DOCS_DIR / 'eval_report.html'

COLOR = {
    'pass':    ('#1a7f37', '#dafbe1'),
    'partial': ('#9a6700', '#fff8c5'),
    'fail':    ('#cf222e', '#ffebe9'),
}


def _latest_per_question() -> list:
    """Read eval_log.jsonl (new format only), keep latest verdict per question id."""
    latest = {}
    if not LOG_PATH.exists():
        return []
    for line in LOG_PATH.open(encoding='utf-8'):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not r.get('vantage_model'):   # skip old Groq-format rows
            continue
        if r['id'] not in latest or r['ts'] >= latest[r['id']]['ts']:
            latest[r['id']] = r
    return sorted(latest.values(), key=lambda r: r['id'])


def _esc(s) -> str:
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _build_html(rows: list) -> str:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    total = len(rows)
    passes = sum(1 for r in rows if r['score'] == 'pass')
    partials = sum(1 for r in rows if r['score'] == 'partial')
    fails = sum(1 for r in rows if r['score'] == 'fail')
    model = rows[0].get('vantage_model', '?') if rows else '?'
    judge = rows[0].get('judge_model', '?') if rows else '?'
    spend = max((r.get('spend_inr_so_far', 0) for r in rows), default=0)

    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r['category']].append(r)

    sections = []
    for cat in sorted(by_cat):
        items = by_cat[cat]
        cp = sum(1 for r in items if r['score'] == 'pass')
        cards = '\n'.join(_card(r) for r in items)
        sections.append(f"""<details class="cat" open>
  <summary>{_esc(cat)} <span class="muted">— {cp}/{len(items)} pass</span></summary>
  <div class="cat-body">{cards}</div>
</details>""")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Vantage — Eval Report</title>
<style>
 body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; max-width:960px;
        margin:40px auto; padding:0 20px; color:#1f2328; background:#fff; }}
 h1 {{ font-size:1.4em; border-bottom:1px solid #d0d7de; padding-bottom:10px; }}
 .meta {{ color:#656d76; font-size:0.85em; margin-bottom:8px; }}
 .summary {{ display:flex; gap:14px; flex-wrap:wrap; margin:18px 0 28px; }}
 .stat {{ border:1px solid #d0d7de; border-radius:8px; padding:10px 16px; }}
 .stat b {{ font-size:1.5em; display:block; }}
 .muted {{ color:#656d76; font-weight:normal; font-size:0.85em; }}
 details.cat {{ border:1px solid #d0d7de; border-radius:8px; margin-bottom:14px; }}
 details.cat>summary {{ padding:12px 16px; font-weight:600; background:#f6f8fa; border-radius:8px;
        cursor:pointer; list-style:none; }}
 details.cat>summary::-webkit-details-marker {{ display:none; }}
 .cat-body {{ padding:12px; }}
 details.q {{ border:1px solid #d0d7de; border-radius:6px; margin-bottom:9px; }}
 details.q>summary {{ padding:10px 14px; cursor:pointer; list-style:none; display:flex;
        gap:8px; align-items:center; font-size:0.92em; }}
 details.q>summary::-webkit-details-marker {{ display:none; }}
 .qid {{ font-weight:700; color:#656d76; font-size:0.82em; }}
 .qtext {{ flex:1; }}
 .badge {{ display:inline-block; padding:1px 9px; border-radius:20px; font-size:0.75em;
        font-weight:700; text-transform:uppercase; }}
 .answer {{ padding:12px 14px; background:#f6f8fa; border-top:1px solid #d0d7de;
        white-space:pre-wrap; font-size:0.86em; }}
 .verdict {{ padding:10px 14px; font-size:0.84em; border-top:1px solid #d0d7de; }}
 .vlabel {{ color:#656d76; font-size:0.78em; }}
</style></head><body>
<h1>Vantage — Eval Report</h1>
<p class="meta">Generated {now} · Vantage: <b>{_esc(model)}</b> · Judge: {_esc(judge)} · Spend: ₹{spend:.0f}</p>
<div class="summary">
 <div class="stat"><b>{passes}/{total}</b><span class="muted">pass ({passes/total*100:.0f}%)</span></div>
 <div class="stat"><b>{partials}</b><span class="muted">partial</span></div>
 <div class="stat"><b>{fails}</b><span class="muted">fail</span></div>
</div>
{''.join(sections)}
</body></html>"""


def _card(r: dict) -> str:
    c, bg = COLOR.get(r['score'], ('#6e7781', '#f6f8fa'))
    return f"""<details class="q">
  <summary><span class="qid">{_esc(r['id'])}</span><span class="qtext">{_esc(r['question'])}</span>
    <span class="badge" style="color:{c};background:{bg}">{_esc(r['score'])}</span></summary>
  <div class="answer">{_esc(r.get('vantage_answer','(no answer)'))}</div>
  <div class="verdict"><span class="vlabel">JUDGE ({_esc(r.get('judge_model',''))}):</span> {_esc(r.get('reason',''))}</div>
</details>"""


def _git_push():
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    for cmd in (['git', 'add', 'docs/eval_report.html'],
                ['git', 'commit', '-m', f'eval: report update {now}'],
                ['git', 'push']):
        res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if res.returncode != 0 and res.stderr.strip():
            print(f"  git: {res.stderr.strip()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-push', action='store_true')
    args = ap.parse_args()

    rows = _latest_per_question()
    if not rows:
        print("No eval results found. Run run_eval.py first.")
        sys.exit(1)

    DOCS_DIR.mkdir(exist_ok=True)
    OUT_HTML.write_text(_build_html(rows), encoding='utf-8')
    print(f"HTML written: {OUT_HTML} ({len(rows)} questions)")

    if args.no_push:
        print("--no-push set. Done.")
        return
    _git_push()
    print("\nLive at: https://rumeein.github.io/vantage-agent/eval_report.html")


if __name__ == '__main__':
    main()
