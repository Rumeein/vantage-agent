"""
Reads eval_log.jsonl and prints an aggregate score report.

Usage:
  python score_report.py
  python score_report.py --last 40        # last N entries only
  python score_report.py --run 2026-06-21 # filter by date prefix
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

LOG_PATH = Path(__file__).parent / 'eval_log.jsonl'


def report(last_n: int = None, run_date: str = None):
    if not LOG_PATH.exists():
        print("No eval_log.jsonl found. Run run_eval.py first.")
        return

    with open(LOG_PATH, encoding='utf-8') as f:
        rows = [json.loads(line) for line in f if line.strip()]

    if run_date:
        rows = [r for r in rows if r.get('ts', '').startswith(run_date)]
    if last_n:
        rows = rows[-last_n:]

    if not rows:
        print("No records matched.")
        return

    total = len(rows)
    scores = Counter(r['score'] for r in rows)
    passes = scores.get('pass', 0)
    spend = max((r.get('spend_so_far', 0) for r in rows), default=0)

    print(f"\n{'='*55}")
    print(f"VANTAGE EVAL REPORT  ({rows[0]['ts'][:10]} to {rows[-1]['ts'][:10]})")
    print(f"{'='*55}")
    print(f"Questions: {total}")
    print(f"Pass rate: {passes}/{total} ({passes/total*100:.0f}%)")
    print(f"Partial:   {scores.get('partial', 0)}")
    print(f"Fail:      {scores.get('fail', 0)}")
    print(f"Spend:     ${spend:.3f} (~Rs{spend*84:.0f})")

    fail_cats = Counter(
        r['failure_category']
        for r in rows
        if r['score'] != 'pass' and r.get('failure_category') not in (None, 'null')
    )
    if fail_cats:
        print(f"\nTop failure categories:")
        for cat, count in fail_cats.most_common():
            print(f"  {cat}: {count}")

    print(f"\nBy category:")
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r['category']].append(r['score'])
    for cat in sorted(by_cat):
        s = by_cat[cat]
        p = s.count('pass')
        pct = p / len(s) * 100
        bar = '#' * p + '.' * (len(s) - p)
        print(f"  {cat:<30} {p}/{len(s)} ({pct:.0f}%)  [{bar}]")

    fails = [r for r in rows if r['score'] == 'fail']
    partials = [r for r in rows if r['score'] == 'partial']

    if fails:
        print(f"\nFailed ({len(fails)}):")
        for r in fails:
            print(f"  {r['id']} [{r.get('failure_category','?')}]: {r.get('reason','')}")
            print(f"    Q: {r['question'][:70]}")

    if partials:
        print(f"\nPartial ({len(partials)}):")
        for r in partials:
            print(f"  {r['id']}: {r.get('reason','')}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--last', type=int, help='Last N log entries')
    parser.add_argument('--run', help='Filter by date prefix, e.g. 2026-06-21')
    args = parser.parse_args()
    report(last_n=args.last, run_date=args.run)
