"""
Builds the daily_brief.txt for a Vantage business instance.
Reads data from Firestore (or local DB CSVs) and generates a compressed
snapshot for the LLM — ~700 tokens vs ~8,000 for raw tables.

Vantage is a multi-platform product. Sections: Flipkart, Meesho, Amazon
(pending data integration), Experiments, Activity.
Add new platform sections following the _fk_section / _me_section pattern.

Run after the data pipeline. Output: <instance_path>/daily_brief.txt

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
        summary      = _parse_csv_string(_fs_fetch_doc(project_id, api_key, 'rumee_db', 'summary'))
        ord_csv      = _fs_fetch_monthly(project_id, api_key, 'rumee_orders_daily', n_months=3)
        fk_orders    = _parse_csv_string(ord_csv)
        me_daily     = _parse_csv_string(_fs_fetch_monthly(project_id, api_key, 'rumee_me_daily', n_months=1))
        fk_ads_sku      = _parse_csv_string(_fs_fetch_monthly(project_id, api_key, 'rumee_fk_ads_sku', n_months=1))
        fk_ads_daily    = _parse_csv_string(_fs_fetch_monthly(project_id, api_key, 'rumee_fk_ads_daily', n_months=1))
        me_ads_daily    = _parse_csv_string(_fs_fetch_monthly(project_id, api_key, 'rumee_me_ads_daily', n_months=1))
        me_ads_catalog  = _parse_csv_string(_fs_fetch_monthly(project_id, api_key, 'rumee_me_ads_catalog', n_months=1))
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
        _fk_ads_db      = _load_db_csv(_url('rumee_db_fk_ads.csv'))
        fk_ads_sku      = _fk_ads_db
        fk_ads_daily    = _fk_ads_db
        _me_ads_db      = _load_db_csv(_url('rumee_db_me_ads.csv'))
        me_ads_daily    = _me_ads_db
        me_ads_catalog  = _me_ads_db

    experiments  = _load_json(instance / 'memory' / 'experiments.json')
    activity_log = instance / 'memory' / 'activity_log.jsonl'
    run_log      = _load_json(instance.parent / 'pipeline_run_log.json')

    data = {'summary': summary, 'fk_orders': fk_orders or {}, 'me_daily': me_daily or {},
            'fk_ads_sku': fk_ads_sku or {}, 'me_ads_catalog': me_ads_catalog or {}}
    coverage_gaps  = _check_coverage(run_log, data)
    pipeline_gaps  = [lbl for sid, lbl in _HEALTH_STREAMS
                      if run_log.get('stream_status', {}).get(sid) in ('gap', 'partial')]

    brief = _build_brief(summary, fk_orders, experiments, activity_log, me_daily, run_log,
                         fk_ads_sku=fk_ads_sku, fk_ads_daily=fk_ads_daily,
                         me_ads_daily=me_ads_daily, me_ads_catalog=me_ads_catalog,
                         _precomputed=(coverage_gaps, pipeline_gaps))

    output = instance / 'daily_brief.txt'
    output.write_text(brief, encoding='utf-8')

    tokens_est = len(brief) // 4
    print(f"Brief written: {output}")
    print(f"Size: {len(brief)} chars, ~{tokens_est} tokens (target <6,000)")

    _notify_discord_gaps(run_log, coverage_gaps, pipeline_gaps)

    print()
    import sys
    sys.stdout.buffer.write(brief.encode('utf-8'))
    sys.stdout.buffer.write(b'\n')


def _build_brief(summary: dict, fk_orders: dict, experiments, activity_log: Path,
                 me_daily: dict = None, run_log: dict = None,
                 fk_ads_sku: dict = None, fk_ads_daily: dict = None,
                 me_ads_daily: dict = None, me_ads_catalog: dict = None,
                 _precomputed=None) -> str:
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    parts = [f"DAILY SNAPSHOT — {today}", ""]

    if _precomputed is not None:
        coverage_gaps, _ = _precomputed
    else:
        data = {'summary': summary, 'fk_orders': fk_orders or {}, 'me_daily': me_daily or {},
                'fk_ads_sku': fk_ads_sku or {}, 'me_ads_catalog': me_ads_catalog or {}}
        coverage_gaps = _check_coverage(run_log, data)
    parts += _health_section(run_log, coverage_gaps)
    parts.append("")
    parts += _platform_summary(summary, me_ads_daily=me_ads_daily, fk_ads_sku=fk_ads_sku, fk_ads_daily=fk_ads_daily)
    parts.append("")
    parts += _fk_section(summary, fk_orders, fk_ads_sku=fk_ads_sku, fk_ads_daily=fk_ads_daily)
    parts.append("")
    parts += _me_section(summary, me_daily, me_ads_daily=me_ads_daily, me_ads_catalog=me_ads_catalog)
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

# Coverage spec: for each stream, a callable(data_dict) → bool.
# data_dict keys: 'summary', 'fk_orders', 'me_daily'
# Returns True if the brief has visible data for that stream.
# Only checked when pipeline reports status=ok for the stream.
_COVERAGE = {
    'me_orders':   lambda d: bool(d['summary'].get('me_monthly') or d['me_daily'].get('me_daily')),
    'me_payments': lambda d: bool(d['summary'].get('me_monthly')),
    'me_returns':  lambda d: bool(d['summary'].get('me_return_reasons')),
    'me_ads':      lambda d: bool(d.get('me_ads_catalog', {}).get('me_ads_catalog') or d['summary'].get('me_skus')),
    'me_views':    lambda d: bool(d['summary'].get('me_views')),
    'fk_payments': lambda d: bool(d['summary'].get('fk_monthly')),
    'fk_views':    lambda d: bool(d['summary'].get('fk_monthly') or d['fk_orders'].get('fk_orders_daily')),
    'fk_orders':   lambda d: bool(d['fk_orders'].get('fk_orders_daily')),
    'fk_returns':  lambda d: bool(d['summary'].get('fk_monthly')),
    'fk_ads':      lambda d: bool(d['summary'].get('fk_skus') or d.get('fk_ads_sku', {}).get('fk_ads_sku')),
    'fk_claims':   lambda d: bool(d['summary'].get('fk_claims')),
}


def _check_coverage(run_log: dict, data: dict) -> list:
    """Return list of (stream_id, label) where pipeline=ok but brief has no data."""
    if not run_log:
        return []
    statuses = run_log.get('stream_status', {})
    label_map = dict(_HEALTH_STREAMS)
    gaps = []
    for sid, check in _COVERAGE.items():
        if statuses.get(sid) != 'ok':
            continue  # pipeline gap — already flagged in stream status
        try:
            has_data = check(data)
        except Exception:
            has_data = False
        if not has_data:
            gaps.append((sid, label_map.get(sid, sid)))
    return gaps


def _health_section(run_log: dict, coverage_gaps: list = None) -> list:
    lines = ["=== DATA HEALTH ==="]
    if not run_log:
        lines.append("  (pipeline_run_log.json not found — health unknown)")
        return lines

    last_run   = (run_log.get('last_run') or '')[:16].replace('T', ' ')
    statuses   = run_log.get('stream_status', {})
    dates      = run_log.get('stream_dates', {})
    rows       = run_log.get('stream_rows', {})
    cov_ids    = {sid for sid, _ in (coverage_gaps or [])}

    lines.append(f"Pipeline last ran: {last_run} UTC")

    pipeline_gaps = []
    for sid, label in _HEALTH_STREAMS:
        status  = statuses.get(sid, 'unknown')
        last_dt = dates.get(sid) or '—'
        row_counts = rows.get(sid, {})
        row_str = ', '.join(f'{t}:{n}' for t, n in row_counts.items()) if row_counts else '—'
        pipeline_flag = ' [GAP]' if status == 'gap' else (' [PARTIAL]' if status == 'partial' else '')
        brief_flag    = ' [NOT IN BRIEF]' if sid in cov_ids else ''
        lines.append(f"  {label}: {status}{pipeline_flag}{brief_flag}  last={last_dt}  rows={row_str}")
        if pipeline_flag:
            pipeline_gaps.append(label)

    warnings = []
    if pipeline_gaps:
        warnings.append(f"{len(pipeline_gaps)} pipeline gap(s): {', '.join(pipeline_gaps)}")
    if coverage_gaps:
        labels = [lbl for _, lbl in coverage_gaps]
        warnings.append(f"{len(coverage_gaps)} brief coverage gap(s): {', '.join(labels)}")

    if warnings:
        for w in warnings:
            lines.append(f"WARNING: {w}")
    else:
        lines.append("All tracked streams: ok and covered in brief")

    return lines


def _platform_summary(summary: dict, me_ads_daily=None,
                      fk_ads_sku=None, fk_ads_daily=None) -> list:
    """Cross-platform business health snapshot — shown before individual platform sections.
    Gives Vantage a side-by-side view before it reads per-platform detail.
    """
    lines = ["=== CROSS-PLATFORM SUMMARY ==="]

    # GMV: last settled month for each platform
    fk_months = sorted(summary.get('fk_monthly', []), key=lambda r: r.get('month', ''), reverse=True)
    me_months = sorted(summary.get('me_monthly', []), key=lambda r: r.get('month', ''), reverse=True)

    fk_gmv = _to_float((fk_months[0].get('gmv') or fk_months[0].get('total_gmv', 0)) if fk_months else 0) or 0
    me_gmv = _to_float((me_months[0].get('gmv') or me_months[0].get('total_gmv', 0)) if me_months else 0) or 0
    # Use None default (not 0) so missing field shows '?' not '0.0%'
    fk_ret = _to_float(fk_months[0].get('return_rate')) if fk_months else None
    me_ret = _to_float(me_months[0].get('return_rate')) if me_months else None
    fk_mon = fk_months[0].get('month', '?') if fk_months else '?'
    me_mon = me_months[0].get('month', '?') if me_months else '?'

    lines.append("Latest settled month GMV and return rate:")
    fk_ret_str = f'{fk_ret:.1f}%' if fk_ret is not None else '?'
    me_ret_str = f'{me_ret:.1f}%' if me_ret is not None else '?'
    lines.append(f"  Flipkart  ({fk_mon}): GMV ₹{_fmt_lakh(fk_gmv)}, return rate {fk_ret_str}")
    lines.append(f"  Meesho    ({me_mon}): GMV ₹{_fmt_lakh(me_gmv)}, return rate {me_ret_str}")

    # Ad efficiency: FK — use pre-computed roas per SKU to avoid double-counting multi-row SKUs.
    # Fallback to fk_ads_daily campaign rows if fk_ads_sku is absent.
    fk_roas = me_roi = None
    if fk_ads_sku:
        rows = [r for r in fk_ads_sku.get('fk_ads_sku', []) if _to_float(r.get('roas'))]
        if rows:
            # Weight average ROAS by spend so big spenders dominate the summary
            total_spend = sum(_to_float(r.get('ad_spend', 0)) or 0 for r in rows)
            total_rev   = sum((_to_float(r.get('ad_spend', 0)) or 0) * (_to_float(r.get('roas', 0)) or 0)
                              for r in rows)
            if total_spend:
                fk_roas = total_rev / total_spend
    if fk_roas is None and fk_ads_daily:
        # Fallback: sum campaign daily rows
        rows = fk_ads_daily.get('fk_ads_daily', [])
        total_spend = sum(_to_float(r.get('ad_spend', 0)) or 0 for r in rows)
        total_rev   = sum(_to_float(r.get('revenue', 0)) or 0 for r in rows)
        if total_spend:
            fk_roas = total_rev / total_spend

    # Meesho ROI — aggregate from me_ads_daily (me_ads_catalog has no roi column)
    if me_ads_daily:
        rows = me_ads_daily.get('me_ads_daily', [])
        total_spend = sum(_to_float(r.get('spend', 0)) or 0 for r in rows)
        total_rev   = sum(_to_float(r.get('revenue', 0)) or 0 for r in rows)
        if total_spend:
            me_roi = total_rev / total_spend

    if fk_roas is not None or me_roi is not None:
        lines.append("Ad efficiency (recent period):")
        if fk_roas is not None:
            lines.append(f"  Flipkart ROAS: {fk_roas:.2f}x")
        else:
            lines.append(f"  Flipkart ROAS: ? (no ad spend data)")
        if me_roi is not None:
            lines.append(f"  Meesho ROI:    {me_roi:.2f}x")
        else:
            lines.append(f"  Meesho ROI:    ? (no ad spend data)")

    # Health flags — use module constants for consistency with platform sections
    flags = []
    if fk_ret is not None and fk_ret > FK_RETURN_ALARM:
        flags.append(f"FK return rate {fk_ret:.1f}% — above {FK_RETURN_ALARM:.0f}% alarm threshold")
    if me_ret is not None and me_ret > ME_RETURN_WATCH:
        flags.append(f"Meesho return rate {me_ret:.1f}% — above {ME_RETURN_WATCH:.0f}% suppression threshold")
    if flags:
        lines.append("Flags:")
        for f in flags:
            lines.append(f"  [!] {f}")

    return lines


def _fk_section(summary: dict, fk_orders: dict = None,
                fk_ads_sku: dict = None, fk_ads_daily: dict = None) -> list:
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
            roas = _to_float(r.get('roas'))
            parts = []
            if rev:
                parts.append(f'₹{_fmt_lakh(rev)} ad revenue')
            if roas:
                parts.append(f'ROAS {roas:.2f}x')
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

    # FK ads per-SKU spend + ROAS (from fk_ads_sku — recent period, actual cost data)
    sku_rows = (fk_ads_sku or {}).get('fk_ads_sku', [])
    if sku_rows:
        by_sku = defaultdict(lambda: {'spend': 0.0, 'revenue': 0.0, 'units': 0, 'name': ''})
        for r in sku_rows:
            s = r.get('sku_id', '?')
            try: by_sku[s]['spend']   += float(r.get('ad_spend', 0) or 0)
            except: pass
            try: by_sku[s]['revenue'] += float(r.get('revenue', 0) or 0)
            except: pass
            try: by_sku[s]['units']   += int(float(r.get('units_sold', 0) or 0))
            except: pass
            if r.get('sku_name'):
                by_sku[s]['name'] = r['sku_name']
        top = sorted(by_sku.items(), key=lambda x: -x[1]['spend'])[:8]
        if top:
            lines.append("FK ads per-SKU (recent period — actual spend from FSN report):")
            for sku, v in top:
                roas = v['revenue'] / v['spend'] if v['spend'] else 0
                name = v['name'][:25] or sku
                lines.append(f"  {sku} ({name}): spend ₹{v['spend']:.0f}, revenue ₹{v['revenue']:.0f}, {v['units']} units, ROAS {roas:.2f}x")

    # FK ads campaign daily ROAS trend (last 7 days)
    daily_rows = (fk_ads_daily or {}).get('fk_ads_daily', [])
    if daily_rows:
        recent = sorted(daily_rows, key=lambda r: r.get('date', ''), reverse=True)[:7]
        if recent:
            lines.append("FK ads campaign daily (last 7d):")
            for r in reversed(recent):
                spend = _to_float(r.get('ad_spend', 0)) or 0
                roas  = _to_float(r.get('roas', 0)) or 0
                conv  = r.get('conversions', '?')
                lines.append(f"  {r.get('date','?')}: spend ₹{spend:.0f}, ROAS {roas:.2f}x, {conv} conversions")

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


def _me_section(summary: dict, me_daily: dict = None,
                me_ads_daily: dict = None, me_ads_catalog: dict = None) -> list:
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

    # Meesho ads — overall spend + per-catalog ROI
    daily_rows   = (me_ads_daily   or {}).get('me_ads_daily',   [])
    catalog_rows = (me_ads_catalog or {}).get('me_ads_catalog', [])

    if daily_rows:
        total_spend   = sum(_to_float(r.get('spend',   0)) or 0 for r in daily_rows)
        total_revenue = sum(_to_float(r.get('revenue', 0)) or 0 for r in daily_rows)
        total_orders  = sum(_to_float(r.get('orders',  0)) or 0 for r in daily_rows)
        overall_roi   = total_revenue / total_spend if total_spend else 0
        lines.append(f"Meesho ads (recent period): spend ₹{total_spend:.0f}, revenue ₹{total_revenue:.0f}, "
                     f"{int(total_orders)} orders, ROI {overall_roi:.2f}x")

    if catalog_rows:
        by_cat = defaultdict(lambda: {'spend': 0.0, 'revenue': 0.0, 'orders': 0, 'name': ''})
        for r in catalog_rows:
            cid = r.get('catalog_id', '?')
            try: by_cat[cid]['spend']   += float(r.get('spend',   0) or 0)
            except: pass
            try: by_cat[cid]['revenue'] += float(r.get('revenue', 0) or 0)
            except: pass
            try: by_cat[cid]['orders']  += int(float(r.get('orders', 0) or 0))
            except: pass
            if r.get('catalog_name'):
                by_cat[cid]['name'] = r['catalog_name']
        top = sorted(by_cat.items(), key=lambda x: -x[1]['spend'])[:8]
        if top:
            lines.append("Meesho ads per catalog (recent period — actual spend):")
            for cid, v in top:
                roi  = v['revenue'] / v['spend'] if v['spend'] else 0
                name = v['name'][:25] or cid
                lines.append(f"  {name}: spend ₹{v['spend']:.0f}, revenue ₹{v['revenue']:.0f}, "
                             f"{v['orders']} orders, ROI {roi:.2f}x")

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


# ─── Discord alert ────────────────────────────────────────────────────────────

def _notify_discord_gaps(run_log: dict, coverage_gaps: list, pipeline_gaps: list):
    """Send a Discord alert if any pipeline or brief coverage gaps are detected."""
    if not coverage_gaps and not pipeline_gaps:
        return

    import urllib.request
    import urllib.error
    import os

    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'Claude RuMee Dashbord'))
            from rumee_secrets import DISCORD_WEBHOOK_URL
            webhook_url = DISCORD_WEBHOOK_URL
        except Exception:
            print("Discord webhook not configured — skipping gap alert")
            return

    last_run = (run_log.get('last_run') or 'unknown')[:16].replace('T', ' ')
    fields = []

    if pipeline_gaps:
        fields.append({
            'name': '⚠️ Pipeline gaps (no data)',
            'value': '\n'.join(f'• {lbl}' for lbl in pipeline_gaps),
            'inline': False,
        })

    if coverage_gaps:
        fields.append({
            'name': '🔍 Brief coverage gaps (data exists but not shown to Vantage)',
            'value': '\n'.join(f'• {lbl}' for _, lbl in coverage_gaps),
            'inline': False,
        })

    fields.append({
        'name': 'Action needed',
        'value': 'Check pipeline_run_log.json and brief_builder.py coverage spec.',
        'inline': False,
    })

    embed = {
        'title': '🚨 Vantage Brief — Data Gaps Detected',
        'description': f'Brief generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")} UTC  |  Pipeline last ran: {last_run} UTC',
        'color': 0xe74c3c,
        'fields': fields,
    }

    payload = json.dumps({'embeds': [embed]}).encode('utf-8')
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={'Content-Type': 'application/json', 'User-Agent': 'RumeePipeline/1.0'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"Discord gap alert sent (HTTP {resp.status})")
    except urllib.error.URLError as e:
        print(f"Discord gap alert failed: {e}")


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
