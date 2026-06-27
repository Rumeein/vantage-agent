"""
Publishes eval_log.jsonl as a live HTML page grouped by run (date + round).

Each run is a collapsible section. Within each run, questions are collapsible.
Human "judge-the-judge" classifications are read from annotations.json and shown
as a third panel below the judge verdict.

Usage:
  python eval/publish_eval.py            # build + push
  python eval/publish_eval.py --no-push  # build only

Live at: https://rumeein.github.io/vantage-agent/eval_report.html
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REPO_ROOT     = Path(__file__).parent.parent
EVAL_DIR      = Path(__file__).parent
LOG_PATH      = EVAL_DIR / 'eval_log.jsonl'
ANNOT_PATH    = EVAL_DIR / 'annotations.json'
DOCS_DIR      = REPO_ROOT / 'docs'
OUT_HTML      = DOCS_DIR / 'eval_report.html'

SCORE_COLOR = {
    'pass':    ('#1a7f37', '#dafbe1'),
    'partial': ('#9a6700', '#fff8c5'),
    'fail':    ('#cf222e', '#ffebe9'),
}
CLASS_COLOR = {
    'JUDGE ERROR':  ('#9a6700', '#fff8c5'),
    'STALE RUBRIC': ('#0969da', '#ddf4ff'),
    'GENUINE':      ('#cf222e', '#ffebe9'),
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_rows() -> list:
    """Load all rows from eval_log.jsonl and any archived eval_log_*.jsonl files."""
    log_files = sorted(EVAL_DIR.glob('eval_log*.jsonl'))
    rows = []
    for path in log_files:
        for line in path.open(encoding='utf-8'):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if not r.get('vantage_model'):
                continue
            rows.append(r)
    return rows


def _load_annotations() -> dict:
    """Return {(id, campaign_round): annotation} for quick lookup."""
    if not ANNOT_PATH.exists():
        return {}
    try:
        items = json.loads(ANNOT_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}
    # annotations.json uses date+round; we map to campaign_round at render time
    # store as list and look up by (id, date, round) → fallback key
    return {(a['id'], a['date'], a['round']): a for a in items}


def _latest_per_question(rows: list) -> dict:
    latest = {}
    for r in rows:
        qid = r['id']
        if qid not in latest or r['ts'] >= latest[qid]['ts']:
            latest[qid] = r
    return latest


def _ts_add_minutes(ts: str, minutes: int) -> str:
    """Return ISO timestamp string shifted by +minutes."""
    from datetime import timedelta
    dt = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    return (dt + timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _group_by_run(rows: list) -> list:
    """
    Group rows into distinct invocation runs.

    New rows (2026-06-27 onward) carry a run_id (ISO timestamp of invocation start).
    Old rows without run_id are clustered by time gap: a gap > 10 min = new run.
    Returns list of run dicts sorted newest-first, with a global campaign_round number.
    """
    # Split legacy rows (no run_id) into runs using a 30-min gap heuristic
    legacy = sorted([r for r in rows if not r.get('run_id')], key=lambda x: x['ts'])
    legacy_runs = []
    current = []
    for r in legacy:
        if current and r['ts'] > _ts_add_minutes(current[-1]['ts'], 30):
            legacy_runs.append(current)
            current = []
        current.append(r)
    if current:
        legacy_runs.append(current)

    buckets = defaultdict(list)
    for run_rows in legacy_runs:
        key = f"legacy_{run_rows[0]['ts']}"
        buckets[key].extend(run_rows)
    for r in rows:
        if r.get('run_id'):
            buckets[r['run_id']].append(r)

    runs = []
    for key, rrows in buckets.items():
        rrows = sorted(rrows, key=lambda x: x['ts'])
        first_ts = rrows[0]['ts']
        date = first_ts[:10]
        p  = sum(1 for r in rrows if r['score'] == 'pass')
        pa = sum(1 for r in rrows if r['score'] == 'partial')
        f  = sum(1 for r in rrows if r['score'] == 'fail')
        runs.append(dict(
            key=key, date=date, first_ts=first_ts,
            rows=sorted(rrows, key=lambda x: x['id']),
            passes=p, partials=pa, fails=f,
            model=rrows[0].get('vantage_model', '?'),
            judge=rrows[0].get('judge_model', '?'),
        ))

    # Sort chronologically, then assign global campaign round numbers
    runs = sorted(runs, key=lambda x: x['first_ts'])
    for i, run in enumerate(runs, 1):
        run['campaign_round'] = i
        run['label'] = f"{run['date']}  ·  Campaign Round {i}"

    return list(reversed(runs))


# ---------------------------------------------------------------------------
# Minimal markdown → HTML
# ---------------------------------------------------------------------------

def _md(text: str) -> str:
    """Convert a subset of markdown to HTML for display in the report."""
    lines = text.split('\n')
    out   = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Pipe table: collect consecutive table lines
        if re.match(r'\s*\|', line):
            table_lines = []
            while i < len(lines) and re.match(r'\s*\|', lines[i]):
                table_lines.append(lines[i])
                i += 1
            out.append(_md_table(table_lines))
            continue

        # Blank line → paragraph break
        if not line.strip():
            out.append('<div class="gap"></div>')
            i += 1
            continue

        # Bullet list items
        if re.match(r'\s*[-*] ', line):
            out.append('<ul>')
            while i < len(lines) and re.match(r'\s*[-*] ', lines[i]):
                item = re.sub(r'^\s*[-*] ', '', lines[i])
                out.append(f'  <li>{_md_inline(item)}</li>')
                i += 1
            out.append('</ul>')
            continue

        # Normal line
        out.append(f'<p>{_md_inline(line)}</p>')
        i += 1

    return '\n'.join(out)


def _md_inline(text: str) -> str:
    """Apply inline markdown: bold, italic, backtick code."""
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',         text)
    text = re.sub(r'`(.+?)`',       r'<code>\1</code>',     text)
    return text


def _md_table(lines: list) -> str:
    """Convert pipe-table lines to an HTML table."""
    rows = []
    for line in lines:
        # separator row (---|---) → skip
        if re.match(r'[\s|:-]+$', line.replace('|', '').replace('-', '').replace(':', '')):
            if re.search(r'-{2,}', line):
                continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        rows.append(cells)

    if not rows:
        return ''

    html = ['<div class="tbl-wrap"><table>']
    for ri, row in enumerate(rows):
        tag = 'th' if ri == 0 else 'td'
        html.append('<tr>' + ''.join(f'<{tag}>{_md_inline(c)}</{tag}>' for c in row) + '</tr>')
    html.append('</table></div>')
    return '\n'.join(html)


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _badge(label: str, color_map: dict, default=('#6e7781', '#f6f8fa')) -> str:
    c, bg = color_map.get(label, default)
    return f'<span class="badge" style="color:{c};background:{bg}">{_esc(label)}</span>'


def _q_card(r: dict, annot: dict | None) -> str:
    score_badge = _badge(r['score'], SCORE_COLOR)

    # annotation panel
    annot_html = ''
    if annot:
        cls = annot.get('classification', '')
        cl_badge = _badge(cls, CLASS_COLOR)
        annot_html = f"""<div class="annot">
  <div class="panel-label">JUDGE-THE-JUDGE {cl_badge}</div>
  <div class="annot-note">{_esc(annot.get('note', ''))}</div>
</div>"""
    else:
        annot_html = '<div class="annot annot-none"><span class="panel-label">JUDGE-THE-JUDGE</span> <span class="muted">No override — judge verdict accepted as-is.</span></div>'

    return f"""<details class="q">
  <summary>
    <span class="arrow">&#9654;</span>
    <span class="qid">{_esc(r['id'])}</span>
    <span class="qtext">{_esc(r['question'])}</span>
    {score_badge}
  </summary>
  <div class="answer">{_md(r.get('vantage_answer', '(no answer)'))}</div>
  <div class="verdict">
    <div class="panel-label">JUDGE ({_esc(r.get('judge_model', ''))})</div>
    <div class="verdict-text">{_esc(r.get('reason', ''))}</div>
  </div>
  {annot_html}
</details>"""


def _run_section(run: dict, annotations: dict, idx: int) -> str:
    total    = len(run['rows'])
    open_attr = 'open' if idx == 0 else ''
    meta_parts = [f"{run['passes']}/{total} pass"]
    if run['partials']: meta_parts.append(f"{run['partials']} partial")
    if run['fails']:    meta_parts.append(f"{run['fails']} fail")
    meta_str = ' · '.join(meta_parts)

    cards = []
    for r in run['rows']:
        annot_key = (r['id'], run['date'], r.get('round', 1))
        annot = annotations.get(annot_key)
        cards.append(_q_card(r, annot))

    return f"""<details class="run" {open_attr}>
  <summary>
    <span class="arrow">&#9654;</span>
    <span class="run-label">{_esc(run['label'])}</span>
    <span class="run-meta">{meta_str} <span class="muted">· {_esc(run['model'])}</span></span>
  </summary>
  <div class="run-body">{''.join(cards)}</div>
</details>"""


# ---------------------------------------------------------------------------
# Full HTML
# ---------------------------------------------------------------------------

CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  margin: 0; padding: 0; color: #1f2328; background: #f6f8fa;
  font-size: 15px; line-height: 1.5;
}
/* Header */
.site-header {
  background: #24292f; color: #fff;
  padding: 14px 32px; display: flex; align-items: center; gap: 14px;
}
.site-header h1 { margin: 0; font-size: 1.1em; font-weight: 600; color: #fff; }
.site-header .tagline { font-size: 0.82em; color: #8d96a0; margin-left: auto; }
/* Main content */
.main { max-width: 1200px; margin: 0 auto; padding: 28px 32px; }
/* Summary bar */
.summary { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 28px; }
.stat {
  background: #fff; border: 1px solid #d0d7de; border-radius: 8px;
  padding: 12px 20px; min-width: 120px;
}
.stat b { font-size: 1.6em; display: block; }
.stat span { color: #656d76; font-size: 0.82em; }
.muted { color: #656d76; font-size: 0.85em; }
/* Run (top-level) */
details.run {
  background: #fff; border: 1px solid #d0d7de; border-radius: 10px;
  margin-bottom: 14px; overflow: hidden;
}
details.run > summary {
  padding: 14px 18px; font-weight: 600; background: #f6f8fa;
  cursor: pointer; list-style: none; display: flex; align-items: center; gap: 10px;
  border-bottom: 1px solid transparent;
}
details.run[open] > summary { border-bottom-color: #d0d7de; }
details.run > summary::-webkit-details-marker { display: none; }
.run-label { font-size: 1em; }
.run-meta  { font-size: 0.85em; color: #444; font-weight: 400; }
.run-body  { padding: 14px 16px; }
/* Question card */
details.q {
  border: 1px solid #d0d7de; border-radius: 7px; margin-bottom: 10px; background: #fff;
}
details.q > summary {
  padding: 10px 14px; cursor: pointer; list-style: none;
  display: flex; align-items: center; gap: 8px; font-size: 0.92em;
}
details.q > summary::-webkit-details-marker { display: none; }
details.q[open] > summary { border-bottom: 1px solid #d0d7de; }
.qid  { font-weight: 700; color: #656d76; font-size: 0.82em; flex-shrink: 0; min-width: 38px; }
.qtext { flex: 1; }
.badge {
  display: inline-block; padding: 2px 10px; border-radius: 20px;
  font-size: 0.74em; font-weight: 700; text-transform: uppercase; flex-shrink: 0;
}
/* Expand arrow */
.arrow {
  font-size: 0.6em; color: #656d76; flex-shrink: 0;
  display: inline-block; transition: transform 0.15s;
}
details[open] > summary .arrow { transform: rotate(90deg); }
/* Answer panel */
.answer {
  padding: 14px 18px; background: #f6f8fa; border-bottom: 1px solid #d0d7de;
  font-size: 0.87em; max-height: 320px; overflow-y: auto;
}
.answer p  { margin: 0 0 6px; }
.answer ul { margin: 4px 0 8px 18px; padding: 0; }
.answer li { margin-bottom: 3px; }
.answer .gap { margin-bottom: 6px; }
.answer table {
  border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 0.95em;
}
.answer th, .answer td {
  border: 1px solid #d0d7de; padding: 5px 10px; text-align: left;
}
.answer th { background: #eaf0f6; font-weight: 600; }
.answer code {
  background: #eaf0f6; padding: 1px 5px; border-radius: 3px;
  font-family: "SFMono-Regular", Consolas, monospace; font-size: 0.9em;
}
/* Judge verdict */
.verdict {
  padding: 10px 18px; font-size: 0.85em; border-bottom: 1px solid #d0d7de;
  background: #fff;
}
.verdict-text { color: #444; margin-top: 3px; }
/* Annotation (judge-the-judge) */
.annot {
  padding: 10px 18px; font-size: 0.85em; background: #fffef0;
  border-bottom: 1px solid #d0d7de;
}
.annot-none { background: #fff; color: #656d76; }
.annot-note { color: #333; margin-top: 3px; }
.panel-label {
  font-size: 0.75em; font-weight: 700; text-transform: uppercase;
  color: #656d76; letter-spacing: 0.04em; margin-bottom: 2px;
}
/* Footer */
.site-footer {
  text-align: center; padding: 24px 32px; color: #656d76;
  font-size: 0.82em; border-top: 1px solid #d0d7de; margin-top: 28px;
  background: #f6f8fa;
}
/* Table scroll wrapper (prevents overflow on narrow screens) */
.answer .tbl-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
/* Mobile */
@media (max-width: 600px) {
  .site-header { flex-wrap: wrap; padding: 12px 16px; gap: 6px; }
  .site-header .tagline { margin-left: 0; font-size: 0.78em; }
  .main { padding: 16px 12px; }
  .stat { min-width: 0; flex: 1 1 calc(50% - 6px); padding: 10px 12px; }
  .stat b { font-size: 1.3em; }
  details.run > summary { padding: 12px 12px; font-size: 0.93em; flex-wrap: wrap; }
  .run-meta { width: 100%; margin-left: 22px; }
  details.q > summary { padding: 10px 10px; font-size: 0.88em; flex-wrap: wrap; }
  .qtext { width: 100%; margin-left: 22px; order: 3; }
  .badge { order: 4; margin-left: 22px; margin-top: 2px; }
  .answer { padding: 10px 12px; font-size: 0.84em; max-height: 260px; }
  .verdict, .annot { padding: 8px 12px; }
  .run-body { padding: 10px 8px; }
  .site-footer { padding: 16px; }
}
"""


def _build_html(rows: list, annotations: dict) -> str:
    now   = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    runs  = _group_by_run(rows)
    latest = _latest_per_question(rows)

    total    = len(latest)
    passes   = sum(1 for r in latest.values() if r['score'] == 'pass')
    partials = sum(1 for r in latest.values() if r['score'] == 'partial')
    fails    = sum(1 for r in latest.values() if r['score'] == 'fail')
    spend    = max((r.get('spend_inr_so_far', 0) for r in rows), default=0)

    run_sections = '\n'.join(_run_section(run, annotations, i) for i, run in enumerate(runs))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>Vantage — Eval Report</title>
  <style>{CSS}</style>
</head>
<body>

<header class="site-header">
  <h1>Vantage &mdash; Eval Report</h1>
  <span class="tagline">Generated {now} &nbsp;·&nbsp; Spend &#8377;{spend:.0f} of &#8377;200 &nbsp;·&nbsp; Latest result per question</span>
</header>

<main class="main">
  <div class="summary">
    <div class="stat"><b>{passes}/{total}</b><span>pass ({passes/total*100:.0f}%)</span></div>
    <div class="stat"><b>{partials}</b><span>partial</span></div>
    <div class="stat"><b>{fails}</b><span>fail</span></div>
    <div class="stat"><b>{len(runs)}</b><span>rounds run</span></div>
    <div class="stat"><b>&#8377;{spend:.0f}</b><span>spent</span></div>
  </div>

  {run_sections}
</main>

<footer class="site-footer">
  Vantage Eval &nbsp;·&nbsp; {now} &nbsp;·&nbsp; noindex &mdash; not indexed by search engines
</footer>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Git push
# ---------------------------------------------------------------------------

def _git_push():
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    for cmd in (
        ['git', 'add', 'docs/eval_report.html', 'eval/annotations.json'],
        ['git', 'commit', '-m', f'eval: report update {now}'],
        ['git', 'push'],
    ):
        res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if res.returncode != 0 and res.stderr.strip():
            print(f"  git: {res.stderr.strip()}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-push', action='store_true')
    args = ap.parse_args()

    rows = _load_rows()
    if not rows:
        print("No eval results found. Run run_eval.py first.")
        sys.exit(1)

    annotations = _load_annotations()
    DOCS_DIR.mkdir(exist_ok=True)
    html = _build_html(rows, annotations)
    OUT_HTML.write_text(html, encoding='utf-8')

    runs   = _group_by_run(rows)
    unique = _latest_per_question(rows)
    print(f"HTML written: {OUT_HTML} ({len(runs)} rounds, {len(unique)} unique questions, {len(annotations)} annotations)")

    if args.no_push:
        print("--no-push set. Done.")
        return
    _git_push()
    print("\nLive at: https://rumeein.github.io/vantage-agent/eval_report.html")


if __name__ == '__main__':
    main()
