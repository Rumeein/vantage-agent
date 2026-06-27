"""
Publishes shared_learnings.json as a readable HTML page on GitHub Pages.

Usage:
  python eval/publish_learnings.py            # build + push
  python eval/publish_learnings.py --no-push  # build only

Live at: https://rumeein.github.io/vantage-agent/platform_knowledge.html
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REPO_ROOT    = Path(__file__).parent.parent
SRC_JSON     = REPO_ROOT / 'shared_learnings.json'
DOCS_DIR     = REPO_ROOT / 'docs'
OUT_HTML     = DOCS_DIR / 'platform_knowledge.html'

CONFIDENCE_STYLE = {
    'confirmed':          ('#1a7f37', '#dafbe1', 'Confirmed — official source'),
    'community-observed': ('#9a6700', '#fff8c5', 'Community-observed — no official source'),
    'hypothesis':         ('#0969da', '#ddf4ff', 'Hypothesis — untested'),
}

PLATFORM_LABEL = {
    'meesho':   'Meesho',
    'flipkart': 'Flipkart',
    'amazon':   'Amazon',
    'general':  'General',
}


def _esc(s: str) -> str:
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _badge(text: str, color: str, bg: str) -> str:
    return f'<span class="badge" style="color:{color};background:{bg}">{_esc(text)}</span>'


def _confidence_badge(conf: str) -> str:
    color, bg, label = CONFIDENCE_STYLE.get(conf, ('#6e7781', '#f6f8fa', conf))
    return _badge(label, color, bg)


def _platform_badge(platform: str) -> str:
    label = PLATFORM_LABEL.get(platform, platform.title())
    return _badge(label, '#0969da', '#ddf4ff')


def _source_badge(source_type: str) -> str:
    if source_type == 'official':
        return _badge('Official Source', '#1a7f37', '#dafbe1')
    if source_type == 'community':
        return _badge('Community Source', '#9a6700', '#fff8c5')
    return _badge(source_type, '#6e7781', '#f6f8fa')


def _detail_block(detail: dict) -> str:
    if not detail:
        return ''
    rows = []
    for key, val in detail.items():
        label = key.replace('_', ' ').title()
        if isinstance(val, list):
            items = ''.join(f'<li>{_esc(v)}</li>' for v in val)
            cell = f'<ul>{items}</ul>'
        else:
            cell = f'<code>{_esc(str(val))}</code>' if key == 'formula' else _esc(str(val))
        rows.append(f'<tr><td class="detail-key">{_esc(label)}</td><td>{cell}</td></tr>')
    return f'<table class="detail-table">{"".join(rows)}</table>'


def _learning_card(item: dict) -> str:
    conf       = item.get('confidence', '')
    platform   = item.get('platform', '')
    source_t   = item.get('source_type', '')
    title      = item.get('title', '')
    summary    = item.get('summary', '')
    detail     = item.get('detail', {})
    implication = item.get('seller_implication', '')
    not_conf   = item.get('what_is_not_confirmed', '')
    source     = item.get('source', '')
    source_url = item.get('source_url', '')
    added      = item.get('added', '')
    item_id    = item.get('id', '')

    detail_html = _detail_block(detail)
    detail_section = f'<div class="section"><div class="section-label">Technical detail</div>{detail_html}</div>' if detail_html else ''

    implication_section = (
        f'<div class="section implication"><div class="section-label">Seller implication</div>'
        f'<p>{_esc(implication)}</p></div>'
    ) if implication else ''

    not_conf_section = (
        f'<div class="section not-confirmed"><div class="section-label">Not confirmed / caveats</div>'
        f'<p>{_esc(not_conf)}</p></div>'
    ) if not_conf else ''

    return f"""<details class="card" id="{_esc(item_id)}">
  <summary>
    <span class="arrow">&#9654;</span>
    <span class="card-title">{_esc(title)}</span>
    <span class="badges">
      {_platform_badge(platform)}
      {_confidence_badge(conf)}
    </span>
  </summary>
  <div class="card-body">
    <div class="section summary-section">
      <div class="section-label">Summary</div>
      <p>{_esc(summary)}</p>
    </div>
    {detail_section}
    {implication_section}
    {not_conf_section}
    <div class="card-footer">
      {_source_badge(source_t)}
      {'<a class="source-text" href="' + _esc(source_url) + '" target="_blank" rel="noopener">' + _esc(source) + '</a>' if source_url else '<span class="source-text">' + _esc(source) + '</span>'}
      <span class="muted added">Added {_esc(added)}</span>
    </div>
  </div>
</details>"""


def _group_by_platform(items: list) -> dict:
    groups: dict = {}
    for item in items:
        p = item.get('platform', 'general')
        groups.setdefault(p, []).append(item)
    return groups


CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  margin: 0; padding: 0; color: #1f2328; background: #f6f8fa;
  font-size: 15px; line-height: 1.6;
}
.site-header {
  background: #24292f; color: #fff;
  padding: 14px 32px; display: flex; align-items: center; gap: 14px;
}
.site-header h1 { margin: 0; font-size: 1.1em; font-weight: 600; color: #fff; }
.site-header .tagline { font-size: 0.82em; color: #8d96a0; margin-left: auto; }
.main { max-width: 1100px; margin: 0 auto; padding: 28px 32px; }
.intro {
  background: #fff; border: 1px solid #d0d7de; border-radius: 8px;
  padding: 16px 20px; margin-bottom: 28px; font-size: 0.9em; color: #444;
}
.intro strong { color: #1f2328; }
.platform-group { margin-bottom: 36px; }
.platform-heading {
  font-size: 1em; font-weight: 700; color: #24292f;
  border-bottom: 2px solid #d0d7de; padding-bottom: 6px; margin-bottom: 14px;
}
.stats { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }
.stat {
  background: #fff; border: 1px solid #d0d7de; border-radius: 8px;
  padding: 10px 18px; min-width: 110px;
}
.stat b { font-size: 1.5em; display: block; }
.stat span { color: #656d76; font-size: 0.8em; }
details.card {
  background: #fff; border: 1px solid #d0d7de; border-radius: 8px;
  margin-bottom: 12px; overflow: hidden;
}
details.card > summary {
  padding: 13px 16px; cursor: pointer; list-style: none;
  display: flex; align-items: flex-start; gap: 10px;
}
details.card > summary::-webkit-details-marker { display: none; }
details.card[open] > summary { border-bottom: 1px solid #d0d7de; background: #f6f8fa; }
.arrow {
  font-size: 0.6em; color: #656d76; flex-shrink: 0; margin-top: 4px;
  display: inline-block; transition: transform 0.15s;
}
details[open] > summary .arrow { transform: rotate(90deg); }
.card-title { flex: 1; font-weight: 600; font-size: 0.95em; }
.badges { display: flex; gap: 6px; flex-wrap: wrap; flex-shrink: 0; margin-left: auto; }
.badge {
  display: inline-block; padding: 2px 10px; border-radius: 20px;
  font-size: 0.72em; font-weight: 700; white-space: nowrap;
}
.card-body { padding: 0; }
.section { padding: 12px 18px; border-bottom: 1px solid #f0f2f4; }
.section:last-child { border-bottom: none; }
.section p { margin: 4px 0 0; color: #333; font-size: 0.88em; }
.summary-section { background: #fafbfc; }
.implication { background: #f0fff4; }
.not-confirmed { background: #fffef0; }
.section-label {
  font-size: 0.72em; font-weight: 700; text-transform: uppercase;
  color: #656d76; letter-spacing: 0.05em;
}
.detail-table { width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 0.85em; }
.detail-table td { padding: 5px 10px; border: 1px solid #d0d7de; vertical-align: top; }
.detail-table ul { margin: 2px 0 2px 16px; padding: 0; }
.detail-table li { margin-bottom: 2px; }
.detail-key { font-weight: 600; color: #444; white-space: nowrap; width: 140px; }
code {
  background: #eaf0f6; padding: 1px 6px; border-radius: 3px;
  font-family: "SFMono-Regular", Consolas, monospace; font-size: 0.9em;
}
.card-footer {
  padding: 10px 18px; display: flex; align-items: center; gap: 10px;
  flex-wrap: wrap; background: #f6f8fa; font-size: 0.8em;
}
.source-text { color: #444; }
a.source-text { color: #0969da; text-decoration: none; }
a.source-text:hover { text-decoration: underline; }
.muted { color: #656d76; }
.added { margin-left: auto; }
.site-footer {
  text-align: center; padding: 24px; color: #656d76;
  font-size: 0.82em; border-top: 1px solid #d0d7de; margin-top: 28px;
}
@media (max-width: 620px) {
  .site-header { flex-wrap: wrap; padding: 12px 14px; }
  .site-header .tagline { margin-left: 0; }
  .main { padding: 16px 12px; }
  details.card > summary { flex-wrap: wrap; }
  .badges { margin-left: 22px; width: 100%; margin-top: 4px; }
  .detail-key { width: auto; }
}
"""


def _build_html(items: list) -> str:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    groups = _group_by_platform(items)

    conf_counts = {}
    for item in items:
        c = item.get('confidence', 'unknown')
        conf_counts[c] = conf_counts.get(c, 0) + 1

    stats_html = ''.join(
        f'<div class="stat"><b>{count}</b><span>{label}</span></div>'
        for label, count in [
            ('total learnings', len(items)),
            ('confirmed (official)', conf_counts.get('confirmed', 0)),
            ('community-observed', conf_counts.get('community-observed', 0)),
        ]
    )

    groups_html = ''
    for platform, platform_items in sorted(groups.items()):
        label = PLATFORM_LABEL.get(platform, platform.title())
        cards = ''.join(_learning_card(item) for item in platform_items)
        groups_html += f"""<div class="platform-group">
  <div class="platform-heading">{_esc(label)}</div>
  {cards}
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>Vantage — Platform Knowledge</title>
  <style>{CSS}</style>
</head>
<body>

<header class="site-header">
  <h1>Vantage &mdash; Platform Knowledge</h1>
  <span class="tagline">Shared learnings across all Vantage businesses &nbsp;·&nbsp; {now}</span>
</header>

<main class="main">
  <div class="intro">
    <strong>What this is:</strong> Platform-level knowledge that applies to every business running on Vantage.
    These are confirmed findings from official sources, or community-observed patterns labelled clearly.
    Vantage uses these as background context when advising any seller — not business-specific data.
  </div>

  <div class="stats">{stats_html}</div>

  {groups_html}
</main>

<footer class="site-footer">
  Vantage Platform Knowledge &nbsp;·&nbsp; {now} &nbsp;·&nbsp; noindex — not indexed by search engines
</footer>

</body>
</html>"""


def _git_push():
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    for cmd in (
        ['git', 'add', 'shared_learnings.json', 'docs/platform_knowledge.html', 'eval/publish_learnings.py'],
        ['git', 'commit', '-m', f'feat: platform knowledge page + Meesho algorithm learnings {now}'],
        ['git', 'push'],
    ):
        res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if res.returncode != 0 and res.stderr.strip():
            print(f"  git: {res.stderr.strip()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-push', action='store_true')
    args = ap.parse_args()

    if not SRC_JSON.exists():
        print(f"shared_learnings.json not found at {SRC_JSON}")
        sys.exit(1)

    data  = json.loads(SRC_JSON.read_text(encoding='utf-8'))
    items = data.get('learnings', [])
    if not items:
        print("No learnings found in shared_learnings.json.")
        sys.exit(1)

    DOCS_DIR.mkdir(exist_ok=True)
    html = _build_html(items)
    OUT_HTML.write_text(html, encoding='utf-8')
    print(f"HTML written: {OUT_HTML} ({len(items)} learnings)")

    if args.no_push:
        print("--no-push set. Done.")
        return
    _git_push()
    print("\nLive at: https://rumeein.github.io/vantage-agent/platform_knowledge.html")


if __name__ == '__main__':
    main()
