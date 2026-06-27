"""
Read and display a judge_compare_results.json in readable format.
Use this to review past runs and decide which questions to improve.

Usage:
  python eval/review_compare.py
  python eval/review_compare.py --file eval/judge_compare_results.json
  python eval/review_compare.py --q q04          # show one question in full
  python eval/review_compare.py --fails-only     # show only fail/partial
"""

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

DEFAULT = Path(__file__).parent / "judge_compare_results.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT))
    ap.add_argument("--q", help="Show one question ID in full (e.g. q04)")
    ap.add_argument("--fails-only", action="store_true", help="Show only fail/partial")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results", [])
    run_date = data.get("run_date", "?")

    print(f"\nRun: {run_date}  |  Questions: {len(results)}")

    if args.q:
        row = next((r for r in results if r["id"] == args.q), None)
        if not row:
            print(f"Question {args.q} not found.")
            return
        _print_full(row)
        return

    for r in results:
        j = r["judges"]
        scores = {
            "gemini": j.get("gemini_flash", {}).get("score", "?"),
            "qwen":   j.get("qwen3_32b", {}).get("score", "?"),
            "llama":  j.get("llama_8b", {}).get("score", "?"),
        }
        if args.fails_only and all(s == "pass" for s in scores.values()):
            continue

        agreement = "AGREE" if len(set(scores.values())) == 1 else "SPLIT"
        print(f"\n[{r['id']}] {r['question'][:70]}")
        print(f"  gemini={scores['gemini']}  qwen={scores['qwen']}  llama={scores['llama']}  [{agreement}]")

        # Show reasons for any non-pass
        for judge_key, label in [("gemini_flash","Gemini"), ("qwen3_32b","Qwen"), ("llama_8b","Llama")]:
            jd = j.get(judge_key, {})
            if jd.get("score") != "pass":
                print(f"    {label}: {jd.get('reason','')}")

        # Flag disagreements
        if agreement == "SPLIT":
            print(f"  *** SPLIT VERDICT — review this question ***")

    # Summary table
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for judge_key, label in [("gemini_flash","Gemini"), ("qwen3_32b","Qwen"), ("llama_8b","Llama")]:
        scores = [r["judges"].get(judge_key, {}).get("score", "?") for r in results]
        p = scores.count("pass")
        f = scores.count("fail")
        pa = scores.count("partial")
        e = scores.count("error")
        print(f"  {label:<8}: pass={p}  partial={pa}  fail={f}  error={e}  ({p}/{len(scores)-e} valid)")
    print()

    splits = [r for r in results if len({
        r["judges"].get("gemini_flash", {}).get("score"),
        r["judges"].get("qwen3_32b", {}).get("score"),
        r["judges"].get("llama_8b", {}).get("score"),
    }) > 1]
    if splits:
        print(f"Split verdicts ({len(splits)} questions): {', '.join(r['id'] for r in splits)}")
        print("These are the most useful for judge comparison — they reveal disagreements.\n")


def _print_full(r: dict):
    print(f"\n{'='*70}")
    print(f"[{r['id']}] {r['question']}")
    print(f"\nRubric:\n  {r['rubric']}")
    print(f"\nVantage answer:\n{r['vantage_answer']}")
    for judge_key, label in [("gemini_flash","Gemini"), ("qwen3_32b","Qwen"), ("llama_8b","Llama")]:
        jd = r["judges"].get(judge_key, {})
        print(f"\n{label}:")
        print(f"  Score    : {jd.get('score','?')}")
        print(f"  Reason   : {jd.get('reason','?')}")
        print(f"  Category : {jd.get('failure_category','?')}")
    print("=" * 70)


if __name__ == "__main__":
    main()
