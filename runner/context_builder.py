"""
Assembles context from the business instance folder into a single string
for the LLM. Reads business data from Firestore (type=firestore) or local/URL
CSV files (type=db), as configured in business_profile.json["data_source"].

Vantage is a multi-platform product. Tables prefixed fk_ = Flipkart,
me_ = Meesho, am_ = Amazon (not yet integrated). Add new platform tables
following the same prefix convention.

Firestore collections (example: rumee-dashboard-6c4c6):
  rumee_db/summary             — summary tables: fk_monthly, me_monthly, fk_skus, me_skus, …
  rumee_fk_daily/{YYYY_MM}     — FK daily rows for that month
  rumee_me_daily/{YYYY_MM}     — Meesho daily rows for that month
  rumee_orders_daily/{YYYY_MM} — fk_orders_daily rows
  rumee_orders_sku/{YYYY_MM}   — fk_orders_sku rows
  rumee_fk_ads_sku/{YYYY_MM}         — FK per-SKU ad spend + ROAS (from FSN report)
  rumee_fk_ads_daily/{YYYY_MM}       — FK campaign-level daily ROAS
  rumee_fk_ads_kw/{YYYY_MM}          — FK keyword performance
  rumee_fk_ads_placements/{YYYY_MM}  — FK placement-level spend breakdown
  rumee_fk_ads_order_items/{YYYY_MM} — FK ad-attributed order line items
  rumee_me_ads_daily/{YYYY_MM}       — Meesho campaign daily: spend, revenue, orders, roi, cpo
  rumee_me_ads_catalog/{YYYY_MM}     — Meesho per-catalog: spend, revenue, orders, clicks, cpc

Table sets per mode:
  nightly       — fk_monthly, me_monthly, fk_skus, me_skus, me_return_reasons,
                  me_views, fk_orders_daily, fk_ads_sku, fk_ads_daily
  monthly_sku   — fk_monthly, me_monthly, fk_skus, me_skus, me_state_summary,
                  fk_zone_summary, fk_pairs
  recent_daily  — fk_daily, me_daily (last 30 days, aggregated by date), fk_orders_daily
  state_kw      — me_views, fk_keywords (top 40), me_return_reasons, me_claims (last 20)
"""

import csv
import io
import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ACTIVITY_LOG_TAIL = 15

_SUMMARY_TABLES = frozenset({
    'fk_monthly', 'me_monthly', 'fk_skus', 'me_skus',
    'me_return_reasons', 'fk_pairs', 'fk_keywords',
    'me_claims', 'me_views', 'me_state_summary', 'fk_zone_summary', 'config',
})
_DAILY_TABLES     = frozenset({'fk_daily', 'me_daily'})
_FK_ORDERS_TABLES = frozenset({'fk_orders_daily', 'fk_orders_sku'})
_ADS_TABLES       = frozenset({'fk_ads_sku', 'fk_ads_daily', 'fk_ads_kw',
                               'fk_ads_placements', 'fk_ads_order_items',
                               'me_ads_daily', 'me_ads_catalog'})

_PASS_TABLES = {
    'nightly':      ['fk_monthly', 'me_monthly', 'fk_skus', 'me_skus', 'me_return_reasons', 'me_views', 'fk_orders_daily', 'fk_ads_sku', 'fk_ads_daily', 'me_ads_daily', 'me_ads_catalog'],
    'monthly_sku':  ['fk_monthly', 'me_monthly', 'fk_skus', 'me_skus', 'me_state_summary', 'fk_zone_summary', 'fk_pairs'],
    'recent_daily': ['fk_daily', 'me_daily', 'fk_orders_daily'],
    'state_kw':     ['me_views', 'fk_keywords', 'me_return_reasons', 'me_claims'],
}

# Max rows to include per table (None = no limit)
_TABLE_LIMITS = {
    'fk_skus':          20,
    'me_skus':          30,
    'fk_keywords':      40,
    'me_claims':        20,
    'fk_orders_daily':  30,
    'fk_orders_sku':    50,
    'fk_ads_sku':           20,
    'fk_ads_daily':         14,
    'fk_ads_kw':            30,
    'fk_ads_placements':    30,
    'fk_ads_order_items':   30,
    'me_ads_daily':         14,
    'me_ads_catalog':       30,
}


def build_context(instance_path: str, mode: str = 'nightly', tables: list = None) -> tuple:
    """
    Returns (context_string, business_profile_dict).
    tables overrides the default table set for the given mode.
    mode='brief' reads daily_brief.txt instead of raw CSV tables (run brief_builder.py first).
    """
    base = Path(instance_path)
    memory = base / 'memory'

    profile = _load_json(base / 'business_profile.json')

    # 'brief' (eval) and 'discord' (live Q&A) share ONE context so the eval tests
    # exactly what production serves. 'brief' hard-requires the file; 'discord' falls
    # back to live raw tables if the brief has not been built yet, so the bot never crashes.
    brief_file = base / 'daily_brief.txt'
    use_brief = mode in ('brief', 'discord') and brief_file.exists()
    if mode == 'brief' and not use_brief:
        raise FileNotFoundError(
            f"daily_brief.txt not found at {brief_file}. Run brief_builder.py first."
        )

    if use_brief:
        brief_content = brief_file.read_text(encoding='utf-8')
        experiments = _load_json(memory / 'experiments.json')
        learnings   = _load_json(memory / 'learnings.json')
        activity    = _load_activity_log(memory / 'activity_log.jsonl', tail=ACTIVITY_LOG_TAIL)
        context = f"""## CONTEXT LOADED: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
## MODE: brief

---

## BUSINESS PROFILE
{json.dumps(profile, indent=2)}

---

## DATA (pre-processed brief)

{brief_content}

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

## TASK: Perform the nightly analysis. Return structured JSON only as per output schema.""".strip()
        return context, profile

    ds      = profile.get('data_source', {})
    ds_type = ds.get('type', 'db')

    requested = tables or _PASS_TABLES.get(mode, _PASS_TABLES['nightly'])

    summary_wanted   = [t for t in requested if t in _SUMMARY_TABLES]
    daily_wanted     = [t for t in requested if t in _DAILY_TABLES]
    fk_orders_wanted = [t for t in requested if t in _FK_ORDERS_TABLES]
    fk_ads_wanted    = [t for t in requested if t in _ADS_TABLES]

    db_data = {}

    if ds_type == 'firestore':
        project_id = ds.get('project_id', '')
        api_key    = ds.get('api_key', '')
        if summary_wanted:
            db_data.update(_parse_db_csv(
                _fs_fetch_doc(project_id, api_key, 'rumee_db', 'summary'),
                summary_wanted,
            ))
        if daily_wanted:
            fk_csv = _fs_fetch_monthly(project_id, api_key, 'rumee_fk_daily')
            me_csv = _fs_fetch_monthly(project_id, api_key, 'rumee_me_daily')
            db_data.update(_parse_db_csv(fk_csv + '\n' + me_csv, daily_wanted))
        if fk_orders_wanted:
            ord_csv = _fs_fetch_monthly(project_id, api_key, 'rumee_orders_daily')
            sku_csv = _fs_fetch_monthly(project_id, api_key, 'rumee_orders_sku', n_months=1)
            db_data.update(_parse_db_csv(ord_csv + '\n' + sku_csv, fk_orders_wanted))
        if fk_ads_wanted:
            _ads_parts = []
            if any(t in fk_ads_wanted for t in ('fk_ads_sku', 'fk_ads_daily', 'fk_ads_kw', 'fk_ads_placements', 'fk_ads_order_items')):
                _ads_parts += [
                    _fs_fetch_monthly(project_id, api_key, 'rumee_fk_ads_sku', n_months=1),
                    _fs_fetch_monthly(project_id, api_key, 'rumee_fk_ads_daily', n_months=1),
                    _fs_fetch_monthly(project_id, api_key, 'rumee_fk_ads_kw', n_months=1),
                    _fs_fetch_monthly(project_id, api_key, 'rumee_fk_ads_placements', n_months=1),
                    _fs_fetch_monthly(project_id, api_key, 'rumee_fk_ads_order_items', n_months=1),
                ]
            if any(t in fk_ads_wanted for t in ('me_ads_daily', 'me_ads_catalog')):
                _ads_parts += [
                    _fs_fetch_monthly(project_id, api_key, 'rumee_me_ads_daily', n_months=1),
                    _fs_fetch_monthly(project_id, api_key, 'rumee_me_ads_catalog', n_months=1),
                ]
            db_data.update(_parse_db_csv('\n'.join(_ads_parts), fk_ads_wanted))
    else:
        db_path = ds.get('db_path', '')

        def _db_file(name):
            if db_path.startswith('http'):
                return f'{db_path.rstrip("/")}/{name}'
            return Path(db_path) / name

        if summary_wanted and db_path:
            db_data.update(_load_db_csv(_db_file('rumee_db_summary.csv'), summary_wanted))
        if daily_wanted and db_path:
            db_data.update(_load_db_csv(_db_file('rumee_db_daily.csv'), daily_wanted))
        if fk_orders_wanted and db_path:
            # fk_orders tables are in rumee_db_daily.csv, not a separate file
            db_data.update(_load_db_csv(_db_file('rumee_db_daily.csv'), fk_orders_wanted))

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

def _load_db_csv(path, wanted_tables: list) -> dict:
    """Parse a DB CSV (local path or HTTP URL) and return {table_name: [dict, ...]}."""
    try:
        if isinstance(path, str) and path.startswith('http'):
            with urllib.request.urlopen(path, timeout=15) as resp:
                content = resp.read().decode('utf-8')
        else:
            if not Path(path).exists():
                return {}
            content = Path(path).read_text(encoding='utf-8')
        return _parse_db_csv(content, wanted_tables)
    except Exception as e:
        print(f'Warning: could not load {path}: {e}')
        return {}


def _parse_db_csv(content: str, wanted_tables: list) -> dict:
    """Parse a DB CSV string and return {table_name: [dict, ...]}."""
    if not content:
        return {}
    wanted  = set(wanted_tables)
    result  = defaultdict(list)
    headers = None
    try:
        for row in csv.reader(io.StringIO(content)):
            if not row:
                continue
            if row[0] == '__table__':
                headers = row[1:]
                continue
            if headers is None or row[0] not in wanted:
                continue
            result[row[0]].append(
                {h: (row[i + 1] if i + 1 < len(row) else '') for i, h in enumerate(headers)}
            )
    except Exception as e:
        print(f'Warning: could not parse CSV content: {e}')
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
    elif table_name == 'me_skus':
        rows = sorted(rows, key=lambda r: _to_float(r.get('total_orders')) or 0, reverse=True)
    elif table_name == 'fk_keywords':
        rows = sorted(rows, key=lambda r: _to_float(r.get('views')) or 0, reverse=True)
    elif table_name == 'me_claims':
        rows = sorted(rows, key=lambda r: r.get('created_date', ''), reverse=True)
    elif table_name in ('fk_orders_daily', 'fk_orders_sku'):
        rows = sorted(rows, key=lambda r: r.get('date', ''), reverse=True)
    elif table_name == 'fk_ads_sku':
        rows = sorted(rows, key=lambda r: _to_float(r.get('ad_spend')) or 0, reverse=True)
    elif table_name == 'fk_ads_daily':
        rows = sorted(rows, key=lambda r: r.get('date', ''), reverse=True)
    elif table_name == 'fk_ads_kw':
        rows = sorted(rows, key=lambda r: _to_float(r.get('spend')) or 0, reverse=True)
    elif table_name == 'fk_ads_placements':
        rows = sorted(rows, key=lambda r: _to_float(r.get('spend')) or _to_float(r.get('ad_spend')) or 0, reverse=True)
    elif table_name == 'fk_ads_order_items':
        rows = sorted(rows, key=lambda r: r.get('date', ''), reverse=True)
    elif table_name == 'me_ads_daily':
        rows = sorted(rows, key=lambda r: r.get('date', ''), reverse=True)
    elif table_name == 'me_ads_catalog':
        rows = sorted(rows, key=lambda r: _to_float(r.get('spend')) or 0, reverse=True)

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


# ─── Firestore helpers ─────────────────────────────────────────────────────────

_FS_BASE = 'https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents'


def _fs_month_keys(n: int) -> list:
    """Return the last n calendar months as YYYY_MM strings, oldest first."""
    now = datetime.utcnow()
    year, month = now.year, now.month
    keys = []
    for _ in range(n):
        keys.insert(0, f'{year}_{month:02d}')
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return keys


def _fs_fetch_doc(project_id: str, api_key: str, collection: str, doc_id: str) -> str:
    """Fetch a Firestore doc and return its content.stringValue field."""
    url = f'{_FS_BASE.format(project=project_id)}/{collection}/{doc_id}?key={api_key}'
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return data.get('fields', {}).get('content', {}).get('stringValue', '')
    except Exception as e:
        print(f'Warning: could not fetch Firestore {collection}/{doc_id}: {e}')
        return ''


def _fs_fetch_monthly(project_id: str, api_key: str, collection: str, n_months: int = 3) -> str:
    """Fetch last n_months docs from a monthly Firestore collection and concatenate CSV."""
    parts = []
    for mk in _fs_month_keys(n_months):
        content = _fs_fetch_doc(project_id, api_key, collection, mk)
        if content:
            parts.append(content)
    return '\n'.join(parts)
