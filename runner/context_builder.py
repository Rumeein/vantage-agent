"""
Assembles context from the business instance folder into a single string
for the LLM. Reads business data directly from dashboard DB CSV files.

DB path is read from business_profile.json["data_source"]["db_path"].

DB files (in db_path):
  rumee_db_summary.csv  — fk_monthly, me_monthly, fk_skus, me_skus,
                          me_return_reasons, fk_pairs, fk_keywords,
                          me_claims, me_views, me_state_summary, fk_zone_summary
  rumee_db_daily.csv    — fk_daily, me_daily (per-SKU rows, aggregated by date in context)

Table sets per mode:
  nightly       — fk_monthly, me_monthly, fk_skus (top 30), me_skus, me_return_reasons, me_views
  monthly_sku   — fk_monthly, me_monthly, fk_skus (top 30), me_skus, me_state_summary, fk_zone_summary, fk_pairs
  recent_daily  — fk_daily, me_daily (last 30 days, aggregated by date)
  state_kw      — me_views, fk_keywords (top 40), me_return_reasons, me_claims (last 20)
"""

import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ACTIVITY_LOG_TAIL = 50

_SUMMARY_TABLES = frozenset({
    'fk_monthly', 'me_monthly', 'fk_skus', 'me_skus',
    'me_return_reasons', 'fk_pairs', 'fk_keywords',
    'me_claims', 'me_views', 'me_state_summary', 'fk_zone_summary', 'config',
})
_DAILY_TABLES = frozenset({'fk_daily', 'me_daily'})

_PASS_TABLES = {
    'nightly':      ['fk_monthly', 'me_monthly', 'fk_skus', 'me_skus', 'me_return_reasons', 'me_views'],
    'monthly_sku':  ['fk_monthly', 'me_monthly', 'fk_skus', 'me_skus', 'me_state_summary', 'fk_zone_summary', 'fk_pairs'],
    'recent_daily': ['fk_daily', 'me_daily'],
    'state_kw':     ['me_views', 'fk_keywords', 'me_return_reasons', 'me_claims'],
}

# Max rows to include per table (None = no limit)
_TABLE_LIMITS = {
    'fk_skus':     30,
    'fk_keywords': 40,
    'me_claims':   20,
}


def build_context(instance_path: str, mode: str = 'nightly', tables: list = None) -> tuple:
    """
    Returns (context_string, business_profile_dict).
    tables overrides the default table set for the given mode.
    """
    base = Path(instance_path)
    memory = base / 'memory'

    profile = _load_json(base / 'business_profile.json')
    db_path = Path(profile.get('data_source', {}).get('db_path', ''))

    requested = tables or _PASS_TABLES.get(mode, _PASS_TABLES['nightly'])

    summary_wanted = [t for t in requested if t in _SUMMARY_TABLES]
    daily_wanted   = [t for t in requested if t in _DAILY_TABLES]

    db_data = {}
    if summary_wanted and db_path:
        db_data.update(_load_db_csv(db_path / 'rumee_db_summary.csv', summary_wanted))
    if daily_wanted and db_path:
        db_data.update(_load_db_csv(db_path / 'rumee_db_daily.csv', daily_wanted))

    sections = []
    for t in requested:
        rows = db_data.get(t, [])
        sections.append(f'### {t}\n{_format_table(t, rows)}')

    experiments = _load_json(memory / 'experiments.json')
    learnings   = _load_json(memory / 'learnings.json')
    activity    = _load_activity_log(memory / 'activity_log.jsonl', tail=ACTIVITY_LOG_TAIL)

    task_lines = {
        'nightly':      '## TASK: Perform the nightly analysis. Return structured JSON only as per output schema.',
        'monthly_sku':  '## TASK: Analyze monthly performance and per-SKU trends. Identify growth opportunities. Return structured JSON.',
        'recent_daily': '## TASK: Analyze recent daily trends. Flag anomalies and sudden changes. Return structured JSON.',
        'state_kw':     '## TASK: Analyze state distribution, search keywords, and return reasons. Surface actionable insights. Return structured JSON.',
    }
    task_line = task_lines.get(mode, '')

    context = f"""## CONTEXT LOADED: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
## MODE: {mode}

---

## BUSINESS PROFILE
{json.dumps(profile, indent=2)}

---

## DATA

{chr(10).join(sections)}

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

{task_line}""".strip()

    return context, profile


# ─── DB loading ───────────────────────────────────────────────────────────────

def _load_db_csv(path: Path, wanted_tables: list) -> dict:
    """Parse a DB CSV and return {table_name: [dict, ...]} for requested tables only."""
    if not path.exists():
        return {}
    wanted = set(wanted_tables)
    result = defaultdict(list)
    headers = None

    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            if row[0] == '__table__':
                headers = row[1:]
                continue
            if headers is None or row[0] not in wanted:
                continue
            rec = {}
            for i, h in enumerate(headers):
                rec[h] = row[i + 1] if i + 1 < len(row) else ''
            result[row[0]].append(rec)

    return dict(result)


# ─── Formatting ───────────────────────────────────────────────────────────────

def _format_table(table_name: str, rows: list) -> str:
    if not rows:
        return '(no data)'

    if table_name in _DAILY_TABLES:
        return _format_daily(rows, days=30)

    limit = _TABLE_LIMITS.get(table_name)

    if table_name == 'fk_skus':
        _fk_skus_rename = {
            'ad_revenue':  'ad_attributed_revenue_rs',
            'conversions': 'units_sold_via_ads',
            'ad_views':    'ad_impressions',
            'settlement':  'revenue_earned_rs',
        }
        _fk_skus_drop = {'stock'}
        rows = [
            {_fk_skus_rename.get(k, k): v for k, v in r.items() if k not in _fk_skus_drop}
            for r in rows
        ]
        rows = sorted(rows, key=lambda r: _to_float(r.get('ad_attributed_revenue_rs')) or 0, reverse=True)
    elif table_name == 'fk_keywords':
        rows = sorted(rows, key=lambda r: _to_float(r.get('views')) or 0, reverse=True)
    elif table_name == 'me_claims':
        rows = sorted(rows, key=lambda r: r.get('created_date', ''), reverse=True)

    if limit:
        rows = rows[:limit]

    if not rows:
        return '(no data)'

    cols = list(rows[0].keys())
    lines = [
        ' | '.join(cols),
        ' | '.join('---' for _ in cols),
    ]
    for r in rows:
        lines.append(' | '.join(str(r.get(c, '')) for c in cols))
    return '\n'.join(lines)


def _format_daily(rows: list, days: int = 30) -> str:
    """Aggregate daily per-SKU rows by date, last N days, markdown table."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')
    rows = [r for r in rows if r.get('date', '') >= cutoff]
    if not rows:
        return f'(no data in last {days} days)'

    _skip = {'date', 'sku_id', 'sku_name', 'top_return_reason', 'states'}
    by_date = defaultdict(lambda: defaultdict(float))
    numeric_cols = set()

    for r in rows:
        date = r['date']
        for k, v in r.items():
            if k in _skip:
                continue
            f = _to_float(v)
            if f is not None:
                by_date[date][k] += f
                numeric_cols.add(k)

    if not by_date:
        return '(no numeric data)'

    cols = sorted(numeric_cols)
    lines = [
        'date | ' + ' | '.join(cols),
        '--- | ' + ' | '.join('---' for _ in cols),
    ]
    for date in sorted(by_date.keys()):
        vals = by_date[date]
        lines.append(date + ' | ' + ' | '.join(f'{vals.get(c, 0):.0f}' for c in cols))
    return '\n'.join(lines)


def _to_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_activity_log(path: Path, tail: int = 50) -> str:
    if not path.exists():
        return '(no activity yet)'
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    recent = lines[-tail:] if len(lines) > tail else lines
    return ''.join(recent).strip() or '(no activity yet)'
