"""
Write human judge verdicts into eval_log.jsonl.

After run_eval.py logs answers with score='pending', the human judge (Claude in session)
reads the printed output and provides verdicts. This script patches those entries.

Usage — pass verdicts as JSON on the command line:
  python apply_verdicts.py --verdicts '[{"id":"q002","run_id":"2026-06-27T...","score":"pass","reason":"...","failure_category":"null"}]'

Or interactively via --interactive (reads from stdin).

Fields per verdict:
  id               — question ID (e.g. "q002")
  run_id           — run_id from the log entry (must match exactly)
  score            — "pass" | "partial" | "fail"
  reason           — one-line explanation
  failure_category — "hallucination"|"format"|"wrong_data"|"stage_mismatch"|"refusal_required"|"null"
"""

import argparse
import json
from pathlib import Path

EVAL_DIR  = Path(__file__).parent
LOG_PATH  = EVAL_DIR / 'eval_log.jsonl'


def apply(verdicts: list):
    if not LOG_PATH.exists():
        print("ERROR: eval_log.jsonl not found.")
        return

    rows = []
    for line in LOG_PATH.open(encoding='utf-8'):
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))

    index = {(r['id'], r.get('run_id', '')): i for i, r in enumerate(rows)}
    applied = 0

    for v in verdicts:
        key = (v['id'], v.get('run_id', ''))
        if key not in index:
            # Try matching by id only if run_id not provided
            if not v.get('run_id'):
                candidates = [i for i, r in enumerate(rows)
                              if r['id'] == v['id'] and r.get('score') == 'pending']
                if len(candidates) == 1:
                    idx = candidates[0]
                elif len(candidates) > 1:
                    print(f"  {v['id']}: multiple pending entries — provide run_id to disambiguate")
                    continue
                else:
                    print(f"  {v['id']}: no pending entry found")
                    continue
            else:
                print(f"  {v['id']} / {v['run_id']}: not found in log")
                continue
        else:
            idx = index[key]

        rows[idx]['score']            = v['score']
        rows[idx]['reason']           = v.get('reason', '')
        rows[idx]['failure_category'] = v.get('failure_category', 'null')
        rows[idx]['judge_model']      = 'human-claude'
        applied += 1
        print(f"  {v['id']} → {v['score'].upper()} — {v.get('reason','')}")

    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')

    print(f"\nApplied {applied}/{len(verdicts)} verdicts to {LOG_PATH.name}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Patch human judge verdicts into eval_log.jsonl')
    parser.add_argument('--verdicts', help='JSON array of verdict objects')
    parser.add_argument('--interactive', action='store_true',
                        help='Read JSON array from stdin')
    args = parser.parse_args()

    if args.interactive:
        print("Paste JSON array of verdicts, then press Ctrl+Z (Windows) or Ctrl+D (Unix):")
        raw = input()
        verdicts = json.loads(raw)
    elif args.verdicts:
        verdicts = json.loads(args.verdicts)
    else:
        parser.print_help()
        raise SystemExit(1)

    apply(verdicts)
