"""
Vantage nightly runner.

Usage:
  python agent.py --instance-path "D:/Claude RuMee Dashbord/vantage"
  python agent.py --instance-path "D:/Claude RuMee Dashbord/vantage" --full-audit

Modes:
  default     Single nightly pass (fk_monthly, me_monthly, top SKUs, views, return reasons)
  --full-audit  Three passes covering the full DB: monthly+SKU, recent daily, state+keywords.
                Results are merged before writing to experiments/learnings.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from context_builder import build_context
from llm_client import call_llm
from memory_writer import update_memory, append_activity_log

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / 'system_prompt.md'

_FULL_AUDIT_PASSES = [
    ('monthly_sku',  'Pass 1/3: monthly performance + SKU analysis'),
    ('recent_daily', 'Pass 2/3: recent daily trends'),
    ('state_kw',     'Pass 3/3: state distribution + keywords + returns'),
]


def run(instance_path: str):
    load_dotenv(Path(instance_path) / '.env')
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding='utf-8')
    context, profile = build_context(instance_path, mode='nightly')
    biz = profile.get('business', {}).get('name', 'unknown')

    print(f'[{_now()}] Running Vantage nightly for: {biz}')

    raw = call_llm(system_prompt, context, profile)
    result = _parse_or_exit(instance_path, raw, 'nightly')

    update_memory(instance_path, result)
    append_activity_log(instance_path, {
        'ts': _now(),
        'event': 'nightly_run',
        'summary': result.get('summary', ''),
        'alerts_count': len(result.get('alerts', [])),
        'new_experiments_count': len(result.get('new_experiments', [])),
        'monitoring_updates_count': len(result.get('monitoring_updates', [])),
        'learnings_added': len(result.get('learnings_update', [])),
    })

    print(f'[{_now()}] Done. Summary: {result.get("summary", "")}')
    print(f'  Alerts: {len(result.get("alerts", []))}')
    print(f'  New experiments: {len(result.get("new_experiments", []))}')
    print(f'  Monitoring updates: {len(result.get("monitoring_updates", []))}')


def run_full_audit(instance_path: str):
    load_dotenv(Path(instance_path) / '.env')
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding='utf-8')

    results = []
    for mode, label in _FULL_AUDIT_PASSES:
        context, profile = build_context(instance_path, mode=mode)
        biz = profile.get('business', {}).get('name', 'unknown')
        print(f'[{_now()}] {label} — {biz}')

        raw = call_llm(system_prompt, context, profile)
        result = _parse_or_exit(instance_path, raw, mode)
        results.append(result)

        append_activity_log(instance_path, {
            'ts': _now(),
            'event': f'full_audit_{mode}',
            'summary': result.get('summary', ''),
            'alerts_count': len(result.get('alerts', [])),
        })
        print(f'  Summary: {result.get("summary", "")}')

    merged = _merge_results(results)
    update_memory(instance_path, merged)

    append_activity_log(instance_path, {
        'ts': _now(),
        'event': 'full_audit_complete',
        'summary': merged.get('summary', ''),
        'total_alerts': len(merged.get('alerts', [])),
        'total_experiments': len(merged.get('new_experiments', [])),
        'total_learnings': len(merged.get('learnings_update', [])),
    })

    print(f'[{_now()}] Full audit complete.')
    print(f'  Total alerts: {len(merged.get("alerts", []))}')
    print(f'  New experiments: {len(merged.get("new_experiments", []))}')
    print(f'  Learnings added: {len(merged.get("learnings_update", []))}')


def _merge_results(results: list) -> dict:
    merged = {
        'summary': ' | '.join(r.get('summary', '') for r in results if r.get('summary')),
        'alerts': [],
        'new_experiments': [],
        'monitoring_updates': [],
        'learnings_update': [],
    }
    for r in results:
        merged['alerts'].extend(r.get('alerts', []))
        merged['new_experiments'].extend(r.get('new_experiments', []))
        merged['monitoring_updates'].extend(r.get('monitoring_updates', []))
        merged['learnings_update'].extend(r.get('learnings_update', []))
    return merged


def _parse_or_exit(instance_path: str, raw: str, mode: str) -> dict:
    try:
        return _parse_json_response(raw)
    except Exception as e:
        _log_error(instance_path, f'[{mode}] Failed to parse LLM response: {e}\n\nRaw:\n{raw}')
        sys.exit(1)


def _parse_json_response(raw: str) -> dict:
    import re
    raw = raw.strip()
    # Try fenced code block anywhere in response
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # Try outermost { ... }
    start = raw.find('{')
    end = raw.rfind('}')
    if start != -1 and end != -1 and end > start:
        return json.loads(raw[start:end + 1])
    raise ValueError("No JSON object found in response")


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _log_error(instance_path: str, message: str):
    path = Path(instance_path) / 'memory' / 'activity_log.jsonl'
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps({'ts': _now(), 'event': 'error', 'message': message}) + '\n')
    print(f'ERROR: {message}', file=sys.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Vantage agent runner')
    parser.add_argument('--instance-path', required=True, help='Path to the business vantage/ folder')
    parser.add_argument('--full-audit', action='store_true', help='Run 3-pass full audit instead of nightly')
    args = parser.parse_args()

    if args.full_audit:
        run_full_audit(args.instance_path)
    else:
        run(args.instance_path)
