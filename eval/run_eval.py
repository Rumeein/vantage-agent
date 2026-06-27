"""
Vantage eval loop — tests Vantage's conversational answers and judges them.

Smartest-setup batch (current plan):
  Vantage answers  = Claude Opus 4.8   (the quality bar we are chasing)
  Judge            = Claude Haiku 4.5  (cheap, only scores pass/fail)
  Hard ceiling     = INR 200 (~one full pass of the 40-question suite)
  Batching         = 6 questions per round (one per scenario/category), round by round

Usage:
  python run_eval.py --instance-path "D:/vantage-rumee"
  python run_eval.py --instance-path "D:/vantage-rumee" --budget-inr 200
  python run_eval.py --instance-path "D:/vantage-rumee" --max-rounds 1   # one round, then stop to observe
  python run_eval.py ... --categories fk_skus_interpretation   # filter
  python run_eval.py ... --ids q001,q002                       # specific questions

Needs ANTHROPIC_API_KEY in the instance .env (billing enabled).

IMPORTANT — context mode:
  Uses mode='brief' (daily_brief.txt). Production Discord Q&A uses mode='discord',
  which now serves the same brief (context_builder treats 'discord' as brief-preferred).
  So this eval tests what users get. Do NOT switch to mode='nightly' (audit context).
  Run brief_builder.py first if daily_brief.txt is stale.

NOTE on model parity: this batch runs Vantage on Opus to probe the quality ceiling.
  Production (business_profile.json) may still be on Groq. If you adopt Opus for
  production too, eval==production is fully restored.
"""

import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Windows consoles default to cp1252 and choke on box chars / rupee symbols in answers.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

EVAL_DIR = Path(__file__).parent
TEST_SUITE_PATH = EVAL_DIR / 'test_suite.json'
LOG_PATH = EVAL_DIR / 'eval_log.jsonl'
SYSTEM_PROMPT_PATH = EVAL_DIR.parent / 'system_prompt.md'

# --- Pricing (USD per 1M tokens) and budget ---
INR_PER_USD = 84.0
PRICING = {
    'claude-opus-4-8':  {'in': 5.0,  'out': 25.0},
    'claude-sonnet-4-6': {'in': 3.0, 'out': 15.0},
    'claude-haiku-4-5': {'in': 1.0,  'out': 5.0},
}
DEFAULT_BUDGET_INR = 200.0
DEFAULT_VANTAGE_MODEL = 'claude-opus-4-8'
DEFAULT_JUDGE_MODEL = 'claude-haiku-4-5'
TOKEN_SAFETY = 1.15   # over-estimate tokens by 15% so the cap stops early, never late
QUESTIONS_PER_ROUND = 6

JUDGE_SYSTEM = """You are an expert evaluator for an AI ecommerce advisor called Vantage.
Assess whether Vantage's answer to a question is correct per the rubric.

Output JSON only — no prose, no markdown:
{"score": "pass|fail|partial", "reason": "one-line explanation", "failure_category": "hallucination|format|wrong_data|stage_mismatch|refusal_required|null"}

Scoring rules:
- pass: answer is correct and complete per rubric
- partial: answer is partially correct but missing one required element
- fail: answer is wrong, hallucinates, violates a rule, or refuses when it should not (or vice versa)

Strict rules:
- REFUSAL REQUIRED means Vantage MUST say the data is not available. If it gives a number instead, score = fail, failure_category = hallucination
- Experiment format questions: missing hypothesis OR missing baseline OR missing evaluate_after_days = fail, failure_category = format
- Stage calibration: recommending Stage 3/4 tactic to Stage 1/2 business = fail, failure_category = stage_mismatch
- You ARE given the DATA CONTEXT Vantage saw (appended after the rubric). A number is invented ONLY if it does NOT appear anywhere in that DATA CONTEXT. If the cited figure appears in the DATA CONTEXT, it is correctly cited — do NOT mark it hallucination.
"""


def _est_tokens(text: str) -> int:
    return math.ceil(len(text or '') / 4 * TOKEN_SAFETY)


def _cost_usd(model: str, in_text: str, out_text: str) -> float:
    p = PRICING.get(model, PRICING[DEFAULT_VANTAGE_MODEL])
    return _est_tokens(in_text) / 1e6 * p['in'] + _est_tokens(out_text) / 1e6 * p['out']


def _build_rounds(test_suite: list) -> list:
    """Group questions by category, then build rounds of one-per-category."""
    by_cat = defaultdict(list)
    for q in test_suite:
        by_cat[q['category']].append(q)
    cats = list(by_cat.keys())
    rounds = []
    depth = max((len(v) for v in by_cat.values()), default=0)
    for r in range(depth):
        rnd = [by_cat[c][r] for c in cats if r < len(by_cat[c])]
        if rnd:
            rounds.append(rnd)
    return rounds


def run_eval(instance_path: str, filter_categories=None, filter_ids=None,
             budget_inr=DEFAULT_BUDGET_INR, vantage_model=DEFAULT_VANTAGE_MODEL,
             judge_model=DEFAULT_JUDGE_MODEL, max_rounds=None):
    load_dotenv(Path(instance_path) / '.env')
    if not os.environ.get('ANTHROPIC_API_KEY'):
        print("ERROR: ANTHROPIC_API_KEY not found in the instance .env. Add it and re-run.")
        return

    with open(TEST_SUITE_PATH, encoding='utf-8') as f:
        test_suite = json.load(f)
    if filter_categories:
        test_suite = [q for q in test_suite if q['category'] in filter_categories]
    if filter_ids:
        test_suite = [q for q in test_suite if q['id'] in filter_ids]
    if not test_suite:
        print("No questions matched filters.")
        return

    budget_usd = budget_inr / INR_PER_USD
    rounds = _build_rounds(test_suite)
    if max_rounds:
        rounds = rounds[:max_rounds]

    print(f"Vantage: {vantage_model} | Judge: {judge_model}")
    print(f"Budget: Rs{budget_inr:.0f} (${budget_usd:.2f}) | Questions: {len(test_suite)} | Rounds: {len(rounds)} (6/round)\n")

    sys.path.insert(0, str(EVAL_DIR.parent / 'runner'))
    from context_builder import build_context

    context, profile = build_context(instance_path, mode='brief')
    profile = dict(profile)
    profile['llm'] = {'provider': 'anthropic', 'model': vantage_model}   # eval-only override
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding='utf-8')

    spend_usd = 0.0
    results = []
    stopped_on_budget = False

    for r_idx, rnd in enumerate(rounds, 1):
        print(f"{'─'*54}\nROUND {r_idx}/{len(rounds)}\n{'─'*54}")
        round_results = []
        for q in rnd:
            # Stop before a question if a typical question would cross the ceiling.
            if spend_usd + (5.0 / INR_PER_USD) > budget_usd:
                stopped_on_budget = True
                break

            ans, ans_cost = _ask_vantage(system_prompt, context, q['question'], profile, vantage_model)
            if ans is None:
                print(f"  {q['id']} SKIP — Vantage error")
                continue
            judge, judge_cost = _judge_answer(q['question'], ans, q['expected'], judge_model, context)
            if judge is None:
                print(f"  {q['id']} SKIP — judge error")
                continue

            spend_usd += ans_cost + judge_cost
            result = {
                'ts': _now(), 'round': r_idx, 'id': q['id'], 'category': q['category'],
                'question': q['question'], 'vantage_answer': ans, 'expected': q['expected'],
                'score': judge.get('score'), 'reason': judge.get('reason'),
                'failure_category': judge.get('failure_category'),
                'vantage_model': vantage_model, 'judge_model': judge_model,
                'cost_inr': round((ans_cost + judge_cost) * INR_PER_USD, 3),
                'spend_inr_so_far': round(spend_usd * INR_PER_USD, 2),
            }
            results.append(result)
            round_results.append(result)
            with open(LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps(result) + '\n')

            label = {'pass': 'PASS', 'partial': 'PART', 'fail': 'FAIL'}.get(judge.get('score'), '?')
            print(f"  {q['id']} [{q['category']}] {label} — {judge.get('reason','')}")

        if round_results:
            rp = sum(1 for r in round_results if r['score'] == 'pass')
            print(f"  → Round {r_idx}: {rp}/{len(round_results)} pass | spend Rs{spend_usd*INR_PER_USD:.1f}/{budget_inr:.0f}\n")
        if stopped_on_budget:
            print(f"BUDGET CEILING reached — stopping before next question.\n")
            break

    _print_summary(results, spend_usd, budget_inr)


def _ask_vantage(system_prompt, context, question, profile, vantage_model):
    sys.path.insert(0, str(EVAL_DIR.parent / 'runner'))
    from llm_client import call_llm
    try:
        user_message = f"{context}\n\n---\n\nUSER QUESTION (conversational mode): {question}"
        answer = call_llm(system_prompt, user_message, profile)
        cost = _cost_usd(vantage_model, system_prompt + user_message, answer)
        return answer, cost
    except Exception as e:
        print(f"  Vantage error: {e}")
        return None, 0.0


def _judge_answer(question, vantage_answer, expected, judge_model, data_context=''):
    import anthropic
    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        user_message = (f"Question: {question}\n\nVantage's answer:\n{vantage_answer}\n\n"
                        f"Expected / rubric:\n{expected}\n\n"
                        f"--- DATA CONTEXT Vantage saw (verify every cited figure against this; "
                        f"a number here is NOT invented) ---\n{data_context}")
        resp = client.messages.create(
            model=judge_model, max_tokens=300,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, 'type', '') == 'text').strip()
        cost = _cost_usd(judge_model, JUDGE_SYSTEM + user_message, raw)
        m = re.search(r'\{.*?\}', raw, re.DOTALL)
        if m:
            return json.loads(m.group()), cost
        print(f"  Judge parse error: {raw[:100]}")
        return None, cost
    except Exception as e:
        print(f"  Judge error: {e}")
        return None, 0.0


def _print_summary(results, spend_usd, budget_inr):
    print(f"\n{'='*54}\nEVAL SUMMARY\n{'='*54}")
    total = len(results)
    if not total:
        print("No results.")
        return
    scores = Counter(r['score'] for r in results)
    passes = scores.get('pass', 0)
    print(f"Total: {total} | Pass: {passes} ({passes/total*100:.0f}%) | "
          f"Partial: {scores.get('partial',0)} | Fail: {scores.get('fail',0)}")
    print(f"Spend: Rs{spend_usd*INR_PER_USD:.1f} of Rs{budget_inr:.0f} (${spend_usd:.2f})")

    # Stage-1 trust gate: hallucination/refusal categories must be 100%.
    trust_cats = {'no_hallucination', 'fk_skus_interpretation'}
    trust = [r for r in results if r['category'] in trust_cats]
    if trust:
        tp = sum(1 for r in trust if r['score'] == 'pass')
        gate = "PASS" if tp == len(trust) else "NOT YET"
        print(f"Stage-1 trust gate (no invented numbers): {tp}/{len(trust)} — {gate}")

    fail_cats = Counter(r['failure_category'] for r in results
                        if r['score'] != 'pass' and r['failure_category'] not in ('null', None))
    if fail_cats:
        print(f"\nFailure types: {dict(fail_cats)}")

    print(f"\nBy category:")
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r['category']].append(r['score'])
    for cat in sorted(by_cat):
        s = by_cat[cat]
        print(f"  {cat}: {s.count('pass')}/{len(s)} pass")

    fails = [r for r in results if r['score'] == 'fail']
    if fails:
        print(f"\nFailed questions:")
        for r in fails:
            print(f"  {r['id']} [{r['failure_category']}]: {r['reason']}")


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Vantage eval loop (Opus answers, Haiku judge, INR cap)')
    parser.add_argument('--instance-path', required=True)
    parser.add_argument('--categories', help='Comma-separated category filter')
    parser.add_argument('--ids', help='Comma-separated question IDs')
    parser.add_argument('--budget-inr', type=float, default=DEFAULT_BUDGET_INR)
    parser.add_argument('--vantage-model', default=DEFAULT_VANTAGE_MODEL)
    parser.add_argument('--judge-model', default=DEFAULT_JUDGE_MODEL)
    parser.add_argument('--max-rounds', type=int, help='Run only the first N rounds, then stop')
    args = parser.parse_args()

    cats = [c.strip() for c in args.categories.split(',')] if args.categories else None
    ids = [i.strip() for i in args.ids.split(',')] if args.ids else None

    run_eval(args.instance_path, filter_categories=cats, filter_ids=ids,
             budget_inr=args.budget_inr, vantage_model=args.vantage_model,
             judge_model=args.judge_model, max_rounds=args.max_rounds)
