"""
judge_compare.py — Run expert questions through Vantage, score each answer
with 3 candidate judges, write results to judge_compare_results.json.

Run first 5 questions:
  python eval/judge_compare.py --instance-path "D:\vantage-rumee"

Run second 5 questions (after daily Groq limit resets):
  python eval/judge_compare.py --instance-path "D:\vantage-rumee" --batch 2

Prerequisite: run brief_builder.py first to generate daily_brief.txt.
  python runner/brief_builder.py --instance-path "D:\vantage-rumee"

Token budget per run (Groq free tier = 100K/day):
  Vantage calls : 5 x 3.9K = 19.5K  (brief mode — was 11K x 5 = 55K)
  llama-8b judge: 5 x  3K  = 15K
  qwen judge    : 5 x  3K  = 15K
  Total         :           = 49.5K  (comfortably under 100K limit)
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, str(Path(__file__).parent.parent / 'runner'))

from dotenv import load_dotenv
from context_builder import build_context

# ─── Test questions ───────────────────────────────────────────────────────────

TEST_QUESTIONS = [
    {
        "id": "q01",
        "question": "Analyze DJ-5 Bahubali's complete performance on Flipkart.",
        "rubric": (
            "PASS: References multiple data points — CTR, ad revenue, impressions, units_sold_via_ads — "
            "and compares with other SKUs to give a relative view. "
            "FAIL: Mentions only one number or gives generic advice without citing actual data."
        ),
    },
    {
        "id": "q02",
        "question": "DJ-14 has the highest return rate on Meesho. What are the possible causes and what do you check first?",
        "rubric": (
            "PASS: Gives a prioritised root cause framework (image accuracy, description mismatch, size issue) "
            "and names one specific first action. "
            "FAIL: Only flags the high number without any diagnosis or investigation plan."
        ),
    },
    {
        "id": "q03",
        "question": "DJ-5 has 1,415 units_sold_via_ads on Flipkart. Does that make it our top seller?",
        "rubric": (
            "PASS: Explicitly clarifies that units_sold_via_ads is ad-driven units only, not total orders. "
            "States that per-SKU total order data does not exist on Flipkart — only monthly totals. "
            "FAIL: Confirms DJ-5 is the top seller based on this number without the clarification."
        ),
    },
    {
        "id": "q04",
        "question": "OG DJ-5 and DJ-5 Bahubali are both listed on Flipkart. Are they competing with each other?",
        "rubric": (
            "PASS: Recognises these as style variations of the same design (OG = base earring, "
            "Bahubali = chain added by Rumee), discusses whether they cannibalise, references their "
            "respective ad performance data. "
            "FAIL: Treats them as completely independent unrelated products."
        ),
    },
    {
        "id": "q05",
        "question": "I have 3 hours this week to improve one thing. What is the highest ROI action across all SKUs?",
        "rubric": (
            "PASS: Picks exactly ONE specific action with clear reasoning from the data. "
            "Prioritises stop-bleeding (high return rate or low ROAS) over growth tactics. "
            "FAIL: Lists multiple things, or gives generic improvement advice without a clear single recommendation."
        ),
    },
    {
        "id": "q06",
        "question": "Flipkart return rate is 43 to 65 percent per month. What is your read on this and what do I do?",
        "rubric": (
            "PASS: Treats this as alarming (43-65% is extremely high for any category), "
            "marks it as top priority, gives specific next steps to investigate root cause. "
            "FAIL: Treats it as normal, downplays the severity, or responds with vague general advice."
        ),
    },
    {
        "id": "q07",
        "question": "Compare DJ-6 across Flipkart and Meesho. Where should effort go?",
        "rubric": (
            "PASS: Uses FK ad data (ad revenue, impressions, CTR) for the Flipkart side "
            "and Meesho orders/returns for the Meesho side. Makes a clear platform recommendation. "
            "FAIL: Only looks at one platform or makes no recommendation."
        ),
    },
    {
        "id": "q08",
        "question": "Which Flipkart keywords are wasted spend and which are driving real value?",
        "rubric": (
            "PASS: References fk_keywords data specifically. Applies a value criterion "
            "(CTR, conversion, ROAS logic) to distinguish high-value from wasted keywords. "
            "FAIL: Gives generic keyword advice without referencing the actual keyword data in context."
        ),
    },
    {
        "id": "q09",
        "question": "Should Rumee expand to Amazon India now?",
        "rubric": (
            "PASS: Says not yet for a Stage 2 business. Gives specific conditions or thresholds "
            "for when expansion makes sense. "
            "FAIL: Says yes without stage calibration, or gives a vague 'it depends' non-answer."
        ),
    },
    {
        "id": "q10",
        "question": "Design a full experiment to improve DJ-6's return rate on Meesho.",
        "rubric": (
            "PASS: Includes all required fields — hypothesis (if X then Y from baseline to target "
            "because mechanism), single variable only, specific baseline metric with current value, "
            "evaluation date of ~30 days. "
            "FAIL: Missing hypothesis format, tests multiple variables at once, or no baseline/date."
        ),
    },
]

JUDGE_PROMPT = """You are evaluating an AI ecommerce advisor called Vantage. Score its answer strictly.

Question asked to Vantage:
{question}

Vantage's answer:
{answer}

Scoring rubric:
{rubric}

Return JSON only — no other text:
{{"score": "pass|fail|partial", "reason": "one sentence", "failure_category": "hallucination|wrong_data|no_synthesis|generic_advice|format_error|stage_mismatch|null"}}"""


# ─── Vantage caller ───────────────────────────────────────────────────────────

def ask_vantage(system_prompt: str, context: str, question: str, profile: dict) -> str:
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1",
    )
    model = profile.get("llm", {}).get("model", "llama-3.3-70b-versatile")
    # Strip nightly JSON task line — we want conversational answers
    ctx = context
    if "## TASK:" in ctx:
        ctx = ctx[: ctx.rfind("## TASK:")].rstrip()
    user_msg = ctx + f"\n\n---\n\nQuestion: {question}\n\nAnswer conversationally based on the data above."
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
    )
    return resp.choices[0].message.content


# ─── Judges ───────────────────────────────────────────────────────────────────

def judge_gemini(question: str, answer: str, rubric: str) -> dict:
    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = JUDGE_PROMPT.format(question=question, answer=answer, rubric=rubric)
    time.sleep(5)  # stay under Gemini free tier rate limit (15 RPM)
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
    resp = client.models.generate_content(model=model, contents=prompt)
    return _parse_json(resp.text)


def judge_groq(model_name: str, question: str, answer: str, rubric: str) -> dict:
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1",
    )
    prompt = JUDGE_PROMPT.format(question=question, answer=answer, rubric=rubric)
    resp = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json(resp.choices[0].message.content)


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return {"score": "error", "reason": "parse failed", "failure_category": "null", "_raw": text[:300]}


# ─── Main ─────────────────────────────────────────────────────────────────────

def main(instance_path: str, batch: int = 1, resume: bool = False):
    load_dotenv(Path(instance_path) / ".env")
    system_prompt = (Path(__file__).parent.parent / "system_prompt.md").read_text(encoding="utf-8")
    context, profile = build_context(instance_path, mode="brief")

    # Split into two batches of 5 to stay under Groq 100K/day limit
    batch_questions = TEST_QUESTIONS[:5] if batch == 1 else TEST_QUESTIONS[5:]
    print(f"\nBatch {batch}/2 — questions {(batch-1)*5+1} to {(batch-1)*5+len(batch_questions)}")

    # Load existing results
    out = Path(__file__).parent / "judge_compare_results.json"
    if (batch == 2 or resume) and out.exists():
        with open(out, "r", encoding="utf-8") as f:
            existing = json.load(f)
        results = existing.get("results", [])
    else:
        results = []

    # Build index of already-answered questions (resume mode)
    answered = {}
    if resume:
        answered = {r["id"]: r for r in results if not r.get("vantage_answer","").startswith("ERROR:")}
        if answered:
            print(f"  Resuming — skipping {len(answered)} already-answered: {', '.join(answered)}")

    for i, q in enumerate(batch_questions):
        print(f"\n[{i+1}/10] {q['id']} — {q['question'][:65]}...")

        # Skip if already answered successfully in a previous run
        if resume and q["id"] in answered:
            print(f"  Already answered — skipping.")
            continue

        print("  Vantage (Groq)...")
        try:
            answer = ask_vantage(system_prompt, context, q["question"], profile)
        except Exception as e:
            answer = f"ERROR: {e}"
            print(f"  Vantage error: {e}")

        print("  Judge 1: Gemini Flash...")
        try:
            g_flash = judge_gemini(q["question"], answer, q["rubric"])
        except Exception as e:
            g_flash = {"score": "error", "reason": str(e)[:120], "failure_category": "null"}

        print("  Judge 2: qwen3-32b (Groq)...")
        try:
            g_gemma = judge_groq("qwen/qwen3-32b", q["question"], answer, q["rubric"])
        except Exception as e:
            g_gemma = {"score": "error", "reason": str(e)[:120], "failure_category": "null"}

        print("  Judge 3: llama-3.1-8b-instant (Groq)...")
        try:
            g_llama = judge_groq("llama-3.1-8b-instant", q["question"], answer, q["rubric"])
        except Exception as e:
            g_llama = {"score": "error", "reason": str(e)[:120], "failure_category": "null"}

        row = {
            "id": q["id"],
            "question": q["question"],
            "vantage_answer": answer,
            "rubric": q["rubric"],
            "judges": {
                "gemini_flash": g_flash,
                "qwen3_32b":    g_gemma,
                "llama_8b":     g_llama,
            },
        }
        results.append(row)
        print(f"  -> gemini={g_flash.get('score')}  qwen={g_gemma.get('score')}  llama={g_llama.get('score')}")

    with open(out, "w", encoding="utf-8") as f:
        json.dump({"run_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "results": results}, f, indent=2, ensure_ascii=False)

    if batch == 1:
        print(f"\nBatch 1 done. Results -> {out}")
        print("Run batch 2 next:")
        print('  python eval/judge_compare.py --instance-path "D:\\vantage-rumee" --batch 2')
    else:
        print(f"\nAll 10 questions done. Results -> {out}")
        _archive_round(out)
        _print_readable_report(results)


def _archive_round(results_file: Path):
    """Copy completed 10-question run to eval/rounds/ for historical tracking."""
    rounds_dir = results_file.parent / "rounds"
    rounds_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    archive = rounds_dir / f"round_{stamp}.json"
    archive.write_bytes(results_file.read_bytes())
    print(f"Archived to: {archive}")


def _print_readable_report(results: list):
    print("\n" + "=" * 70)
    print("JUDGE COMPARISON REPORT")
    print("=" * 70)
    for r in results:
        print(f"\n[{r['id']}] {r['question']}")
        print(f"  Vantage: {r['vantage_answer'][:300].strip()}{'...' if len(r['vantage_answer']) > 300 else ''}")
        j = r["judges"]
        g = j.get("gemini_flash", {})
        q = j.get("qwen3_32b", {})
        l = j.get("llama_8b", {})
        print(f"  Gemini : [{g.get('score','?'):7}] {g.get('reason','')}")
        print(f"  Qwen   : [{q.get('score','?'):7}] {q.get('reason','')}")
        print(f"  Llama  : [{l.get('score','?'):7}] {l.get('reason','')}")
    print("\n" + "=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-path", required=True)
    parser.add_argument("--batch", type=int, default=1, choices=[1, 2],
                        help="Which batch: 1 = q01-q05, 2 = q06-q10")
    parser.add_argument("--resume", action="store_true",
                        help="Skip questions already answered successfully — use after a partial run")
    args = parser.parse_args()
    main(args.instance_path, batch=args.batch, resume=args.resume)
