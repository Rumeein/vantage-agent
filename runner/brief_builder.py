"""
Reads Rumee DB CSVs from GitHub, generates a compressed daily_brief.txt.
Target: ~1,500 tokens vs ~8,000 for raw tables.

Run after the pipeline. Output: <instance_path>/daily_brief.txt

Usage:
  python brief_builder.py [--instance-path PATH]
"""

import argparse
import csv
import io
import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_INSTANCE = Path("D:/vantage-rumee")
FK_RETURN_ALARM = 50.0   # % — flag FK return rate above this
ME_RETURN_WATCH = 15.0   # % per SKU — flag Meesho SKU above this
FK_SKU_RENAME = {
    'ad_revenue':  'ad_attributed_revenue_rs',
    'conversions': 'units_sold_via_ads',
    'ad_views':    'ad_impressions',
    'settlement':  'revenue_earned_rs',
}
FK_SKU_DROP = {'stock'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--instance-path', default=str(DEFAULT_INSTANCE))
    args = ap.parse_args()

    instance = Path(args.instance_path)
    profile  = _load_json(instance / 'business_profile.json')
    ds       = profile.get('data_source', {})
    ds_type  = ds.get('type', 'db')

    if ds_type == 'firestore':
        project_id = ds.get('project_id', '')
        api_key    = ds.get('api_key', '')
        summary   = _parse_csv_string(_fs_fetch_doc(project_id, api_key, 'rumee_db', 'summary'))
        ord_csv   = _fs_fetch_monthly(project_id, api_key, 'rumee_orders_daily', n_months=3)
        fk_orders = _parse_csv_string(ord_csv)
        me_daily  = _parse_csv_string(_fs_fetch_monthly(project_id, api_key, 'rumee_me_daily', n_months=1))
    else:
        db_path = ds.get('db_path', '').rstrip('/')

        def _url(name):
            if db_path.startswith('http'):
                return f'{db_path}/{name}'
            return str(Path(db_path) / name)

        summary   = _load_db_csv(_url('rumee_db_summary.csv'))
        # fk_orders_daily lives in rumee_db_daily.csv, not a separate file
        fk_orders = _load_db_csv(_url('rumee_db_daily.csv'))
        me_daily  = _load_db_csv(_url('rumee_db_daily.csv'))

    experiments  = _load_json(instance / 'memory' / 'experiments.json')
    activity_log = instance / 'memory' / 'activity_log.jsonl'
    run_log      = _load_json(instance.parent / 'pipeline_run_log.json')

    brief = _build_brief(summary, fk_orders, experiments, activity_log, me_daily, run_log)

    output = instance / 'daily_brief.txt'
    output.write_text(brief, encoding='utf-8')

    tokens_est = len(brief) // 4
    print(f"Brief written: {output}")
    print(f"Size: {len(brief)} chars, ~{tokens_est} tokens (target <6,000)")
    print()
    import sys
    sys.stdout.buffer.write(brief.encode('utf-8'))
    sys.stdout.buffer.write(b'\n')


def _build_brief(summary: dict, fk_orders: dict, experiments, activity_log: Path,
                 me_daily: dict = None, run_log: dict = None) -> str:
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    parts = [f"DAILY SNAPSHOT — {today}", ""]

    parts += _health_section(run_log)
    parts.append("")
    parts += _fk_section(summary, fk_orders)
    parts.append("")
    parts += _me_section(summary, me_daily)
    parts.append("")
    parts += _experiments_section(experiments)
    parts.append("")
    parts += _activity_section(activity_log)

    return "\n".join(parts)


# Stream display order and friendly labels for health block
_HEALTH_STREAMS = [
    ('me_orders',   'Meesho orders'),
    ('me_payments', 'Meesho payments'),
    ('me_returns',  'Meesho returns'),
    ('me_ads',      'Meesho ads'),
    ('me_views',    'Meesho views'),
    ('fk_payments', 'FK payments'),
    ('fk_views',    'FK views/daily'),
    ('fk_orders',   'FK orders'),
    ('fk_returns',  'FK returns'),
    ('fk_ads',      'FK ads'),
    ('fk_claims',   'FK claims'),
]


def _health_section(run_log: dict) -> list:
    lines = ["=== DATA HEALTH ==="]
    if not run_log:
        lines.append("  (pipeline_run_log.json not found — health unknown)")
        return lines

    last_run   = (run_log.get('last_run') or '')[:16].replace('T', ' ')
    statuses   = run_log.get('stream_status', {})
    dates      = run_log.get('stream_dates', {})
    rows       = run_log.get('stream_rows', {})

    lines.append(f"Pipeline last ran: {last_run} UTC")

    gap_streams = []
    for sid, label in _HEALTH_STREAMS:
        status  = statuses.get(sid, 'unknown')
        last_dt = dates.get(sid) or '—'
        row_counts = rows.get(sid, {})
        row_str = ', '.join(f'{t}:{n}' for t, n in row_counts.items()) if row_counts else '—'
        flag = ' [GAP]' if status == 'gap' else (' [PARTIAL]' if status == 'partial' else '')
        lines.append(f"  {label}: {status}{flag}  last={last_dt}  rows={row_str}")
        if flag:
            gap_streams.append(label)

    if gap_streams:
        lines.append(f"WARNING: {len(gap_streams)} stream(s) with gaps — {', '.join(gap_streams)}")
    else:
        lines.append("All tracked streams: ok")

    return lines


def _fk_section(summary: dict, fk_orders: dict = None) -> list:
    lines = ["=== FLIPKART ==="]

    fk_months = summary.get('fk_monthly', [])
    if fk_months:
        lines.append("Monthly performance (recent 3 months):")
        months = sorted(fk_months, key=lambda r: r.get('month', ''), reverse=True)[:3]
        for r in reversed(months):
            month = r.get('month', '?')
            orders = _fmt_int(r.get('orders') or r.get('total_orders', '0'))
            returns = _fmt_int(r.get('returns') or r.get('total_returns', '0'))
            rate = _compute_rate(r)
            alarm = ' [ALARM]' if rate is not None and rate > FK_RETURN_ALARM else ''
            rate_str = f'{rate:.1f}%{alarm}' if rate is not None else '?%'
            gmv = r.get('gmv') or r.get('total_gmv', '')
            gmv_str = f'  GMV ₹{_fmt_lakh(gmv)}' if gmv else ''
            lines.append(f"  {month}: {orders} orders, {returns} returns, {rate_str} return rate{gmv_str}")
    else:
        lines.append("  (no monthly data)")

    fk_skus = summary.get('fk_skus', [])
    if fk_skus:
        skus = [
            {FK_SKU_RENAME.get(k, k): v for k, v in r.items() if k not in FK_SKU_DROP}
            for r in fk_skus
        ]
        skus = sorted(skus, key=lambda r: _to_float(r.get('ad_attributed_revenue_rs')) or 0, reverse=True)

        lines.append("Top 8 ad earners (ad-attributed only — NOT total orders/revenue):")
        for r in skus[:8]:
            name = r.get('sku_name') or r.get('sku_id', '?')
            rev = _to_float(r.get('ad_attributed_revenue_rs'))
            units = _to_float(r.get('units_sold_via_ads'))
            impr = _to_float(r.get('ad_impressions'))
            ctr = _to_float(r.get('ctr'))
            parts = []
            if rev:
                parts.append(f'₹{_fmt_lakh(rev)} ad revenue')
            if units:
                parts.append(f'{int(units)} units via ads')
            if impr:
                parts.append(f'{int(impr)} impressions')
            if ctr:
                parts.append(f'{ctr:.2f}% CTR')
            lines.append(f"  {name}: {', '.join(parts) or '(no data)'}")

        ctr_skus = [r for r in skus if _to_float(r.get('ctr'))]
        if ctr_skus:
            best = max(ctr_skus, key=lambda r: _to_float(r.get('ctr')) or 0)
            name = best.get('sku_name') or best.get('sku_id', '?')
            ctr = _to_float(best.get('ctr'))
            lines.append(f"Top CTR: {name} at {ctr:.2f}%")
    else:
        lines.append("  (no SKU data)")

    # Daily order velocity (last 7 days)
    if fk_orders:
        daily = sorted(fk_orders.get('fk_orders_daily', []),
                       key=lambda r: r.get('date', ''), reverse=True)[:7]
        if daily:
            avg = sum(_to_float(r.get('orders', 0)) or 0 for r in daily) / len(daily)
            lines.append(f"Daily orders (last {len(daily)}d, avg {avg:.0f}/day):")
            for r in reversed(daily):
                lines.append(f"  {r['date']}: {r.get('orders','?')} orders, {r.get('quantity','?')} units")

    return lines


def _me_section(summary: dict, me_daily: dict = None) -> list:
    lines = ["=== MEESHO ==="]

    me_months = summary.get('me_monthly', [])
    if me_months:
        lines.append("Monthly performance (recent 3 months, delivered orders only — current month updates with settlement lag):")
        months = sorted(me_months, key=lambda r: r.get('month', ''), reverse=True)[:3]
        for r in reversed(months):
            month = r.get('month', '?')
            orders = _fmt_int(r.get('orders') or r.get('total_orders', '0'))
            returns = _fmt_int(r.get('returns') or r.get('total_returns', '0'))
            rate = _compute_rate(r)
            rate_str = f'{rate:.1f}%' if rate is not None else '?%'
            gmv = r.get('gmv') or r.get('total_gmv', '')
            gmv_str = f'  GMV ₹{_fmt_lakh(gmv)}' if gmv else ''
            lines.append(f"  {month}: {orders} orders, {returns} returns, {rate_str} return rate{gmv_str}")
    else:
        lines.append("  (no monthly data)")

    # Daily placed orders (last 7 days) — includes in-transit, not yet settled
    if me_daily:
        raw_daily = me_daily.get('me_daily', [])
        by_date = defaultdict(int)
        for r in raw_daily:
            placed = _to_float(r.get('orders_placed', 0))
            if placed:
                by_date[r['date']] += int(placed)
        if by_date:
            recent = sorted(by_date.keys(), reverse=True)[:7]
            avg = sum(by_date[d] for d in recent) / len(recent)
            lines.append(f"Daily orders placed (last {len(recent)}d, avg {avg:.0f}/day — includes in-transit):")
            for d in reversed(recent):
                lines.append(f"  {d}: {by_date[d]} orders placed")

    me_skus = summary.get('me_skus', [])
    if me_skus:
        skus = sorted(me_skus, key=lambda r: _to_float(r.get('total_orders')) or 0, reverse=True)

        lines.append("Top 8 sellers (by orders):")
        for r in skus[:8]:
            name = r.get('sku_name') or r.get('sku_id', '?')
            orders = _fmt_int(r.get('total_orders', '0'))
            rate = _to_float(r.get('return_rate'))
            rate_str = f', {rate:.1f}% return rate' if rate is not None else ''
            lines.append(f"  {name}: {orders} orders{rate_str}")

        watch = [r for r in me_skus if (_to_float(r.get('return_rate')) or 0) > ME_RETURN_WATCH]
        watch = sorted(watch, key=lambda r: _to_float(r.get('return_rate')) or 0, reverse=True)
        if watch:
            lines.append(f"High return SKUs [>{ME_RETURN_WATCH:.0f}% — watch]:")
            for r in watch[:5]:
                name = r.get('sku_name') or r.get('sku_id', '?')
                rate = _to_float(r.get('return_rate'))
                orders = _fmt_int(r.get('total_orders', '0'))
                lines.append(f"  {name}: {rate:.1f}% ({orders} orders)")
    else:
        lines.append("  (no SKU data)")

    reasons = summary.get('me_return_reasons', [])
    if reasons:
        sorted_reasons = sorted(
            reasons,
            key=lambda r: _to_float(r.get('pct')) or _to_float(r.get('count')) or 0,
            reverse=True,
        )
        lines.append("Top return reasons:")
        for r in sorted_reasons[:5]:
            reason = r.get('reason', '?')
            pct = _to_float(r.get('pct'))
            count = _to_float(r.get('count'))
            if pct:
                lines.append(f"  {reason}: {pct:.1f}%")
            elif count:
                lines.append(f"  {reason}: {int(count)} cases")

    return lines


def _experiments_section(experiments) -> list:
    lines = ["=== EXPERIMENTS ==="]

    if not experiments:
        lines.append("  (none)")
        return lines

    items = []
    if isinstance(experiments, dict):
        items = experiments.get('experiments', [])
    elif isinstance(experiments, list):
        items = experiments

    active = [e for e in items if isinstance(e, dict) and e.get('status') not in ('completed', 'cancelled')]
    completed = [e for e in items if isinstance(e, dict) and e.get('status') == 'completed']

    lines.append(f"Active: {len(active)} | Completed: {len(completed)} | Total: {len(items)}")

    for e in active[:3]:
        name = e.get('catalog') or e.get('name') or ''
        platform = e.get('platform', '')
        hyp = e.get('hypothesis', '')[:80]
        eval_after = e.get('evaluate_after_days', '')
        created = (e.get('created_at') or '')[:10]
        label = f"{name} ({platform})" if name else platform
        detail = ', '.join(filter(None, [
            f'started {created}' if created else '',
            f'evaluate after {eval_after}d' if eval_after else '',
        ]))
        lines.append(f"  [{label}] {hyp}" + (f" — {detail}" if detail else ""))

    return lines


def _activity_section(log_path: Path) -> list:
    lines = ["=== RECENT ACTIVITY ==="]
    if not log_path.exists():
        lines.append("  (no activity log)")
        return lines

    with open(log_path, 'r', encoding='utf-8') as f:
        all_lines = f.readlines()

    recent = all_lines[-5:] if len(all_lines) > 5 else all_lines
    for line in recent:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            ts = (event.get('timestamp') or '')[:10]
            msg = event.get('message') or event.get('action') or event.get('event', '')
            msg = str(msg)[:100]
            lines.append(f"  {ts}: {msg}")
        except Exception:
            lines.append(f"  {line[:100]}")

    return lines


# ─── Data loading ─────────────────────────────────────────────────────────────

def _load_db_csv(url_or_path: str) -> dict:
    try:
        if url_or_path.startswith('http'):
            with urllib.request.urlopen(url_or_path, timeout=15) as resp:
                content = resp.read().decode('utf-8')
        else:
            if not Path(url_or_path).exists():
                return {}
            content = Path(url_or_path).read_text(encoding='utf-8')
        return _parse_csv_string(content)
    except Exception as e:
        print(f'Warning: could not load {url_or_path}: {e}')
        return {}


def _parse_csv_string(content: str) -> dict:
    """Parse a DB CSV string (all tables) and return {table_name: [dict, ...]}."""
    if not content:
        return {}
    result  = defaultdict(list)
    headers = None
    try:
        for row in csv.reader(io.StringIO(content)):
            if not row:
                continue
            if row[0] == '__table__':
                headers = row[1:]
                continue
            if headers is None:
                continue
            result[row[0]].append(
                {h: (row[i + 1] if i + 1 < len(row) else '') for i, h in enumerate(headers)}
            )
    except Exception as e:
        print(f'Warning: could not parse CSV: {e}')
    return dict(result)


# ─── Firestore helpers ─────────────────────────────────────────────────────────

_FS_BASE = 'https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents'


def _fs_month_keys(n: int) -> list:
    """Return the last n calendar months as YYYY_MM strings, oldest first."""
    now = datetime.now(timezone.utc)
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


def _load_json(path: Path):
    if not path.exists():
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ─── Formatting helpers ────────────────────────────────────────────────────────

def _compute_rate(r: dict):
    rate = _to_float(r.get('return_rate'))
    if rate is not None:
        return rate
    orders = _to_float(r.get('orders') or r.get('total_orders'))
    returns = _to_float(r.get('returns') or r.get('total_returns'))
    if orders and orders > 0 and returns is not None:
        return (returns / orders) * 100
    return None


def _fmt_int(v) -> str:
    f = _to_float(v)
    return str(int(f)) if f is not None else '?'


def _fmt_lakh(v) -> str:
    f = _to_float(v)
    if f is None:
        return '?'
    if f >= 100000:
        return f'{f / 100000:.1f}L'
    if f >= 1000:
        return f'{f / 1000:.1f}K'
    return str(int(f))


def _to_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


if __name__ == '__main__':
    main()
