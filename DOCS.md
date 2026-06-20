# Vantage — Project Documentation

> **Who this is for:** Any developer or AI assistant working on Vantage. You should be able to understand the full system from this file without reading the code or asking the owner.
>
> **Companion file:** `README.md` covers the generic product design and setup instructions. This file covers the Rumee deployment specifically — decisions made, current state, what is done and what is pending.
>
> **Rule:** When any decision changes, this file must be updated in the same session it changes.

Last updated: 2026-06-20

---

## Table of Contents

1. [What Vantage Is](#1-what-vantage-is)
2. [Rumee Deployment — Overview](#2-rumee-deployment--overview)
3. [Repository Structure](#3-repository-structure)
4. [Data Flow](#4-data-flow)
5. [Runner Files — What Each Does](#5-runner-files--what-each-does)
6. [LLM Configuration](#6-llm-configuration)
7. [Discord Bot](#7-discord-bot)
8. [Data Schema — fk_skus (Critical)](#8-data-schema--fk_skus-critical)
9. [Memory — Where Everything Is Stored](#9-memory--where-everything-is-stored)
10. [Eval Loop Plan](#10-eval-loop-plan)
11. [Cloud Hosting Plan](#11-cloud-hosting-plan)
12. [Build Status](#12-build-status)
13. [Key Decisions](#13-key-decisions)
14. [How to Run](#14-how-to-run)

---

## 1. What Vantage Is

An AI growth advisor for ecommerce sellers. It reads business data, identifies problems and opportunities, designs experiments with measurable hypotheses, tracks outcomes, and accumulates learnings over time. It is not a one-off analysis tool — it gets smarter the longer it runs.

For the generic product design, see `README.md`.

---

## 2. Rumee Deployment — Overview

**Business:** Rumee Jewellery (rumeein@gmail.com) — artificial jewellery on Flipkart and Meesho.

**Two repos involved:**

| Repo | Path | Role |
|---|---|---|
| vantage-agent | `D:\vantage-agent\` | Generic product — runner scripts, system prompt, shared learnings |
| rumee-dashboard | `D:\Claude RuMee Dashbord\vantage\` | Rumee instance — business profile, memory, environment config |

**Key divergences from the generic README:**
- Uses **Discord** (not Telegram) — Telegram is banned in India
- Reads data from **GitHub raw URLs** (not local CSV files) — no local machine dependency
- Memory files are committed to the **GitHub repo** after every write — not stored only on disk
- LLM is **Groq / llama-3.3-70b-versatile** (free) — not Anthropic or OpenAI

---

## 3. Repository Structure

```
vantage-agent/
├── DOCS.md                     — this file (Rumee deployment doc)
├── README.md                   — generic product design and setup
├── system_prompt.md            — expert persona (the brain)
├── shared_learnings.json       — universal + category learnings
├── runner/
│   ├── agent.py                — nightly analysis runner
│   ├── discord_bot.py          — Discord Q&A bot (replaces telegram_bot.py)
│   ├── telegram_bot.py         — legacy, not used for Rumee
│   ├── context_builder.py      — assembles DB data + memory into LLM context
│   ├── llm_client.py           — LLM-agnostic caller (Anthropic / OpenAI / Groq)
│   └── memory_writer.py        — writes LLM output back to memory files
└── templates/                  — starter templates for new business instances

Rumee instance (separate repo):
D:\Claude RuMee Dashbord\vantage\
├── business_profile.json       — Rumee business config (provider=groq, stage=2)
├── .env                        — GROQ_API_KEY, DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID
└── memory/
    ├── experiments.json        — all experiments and outcomes
    ├── learnings.json          — what works for Rumee specifically
    └── activity_log.jsonl      — complete audit trail
```

---

## 4. Data Flow

```
[GitHub repo: rumee-dashboard]
    rumee_db_summary.csv
    rumee_db_daily.csv
        ↓  context_builder.py fetches via HTTP (GitHub raw URLs)
[Vantage runner / Discord bot]
    Builds context → calls Groq LLM
        ↓
[LLM response — structured JSON]
    memory_writer.py parses and writes:
        ↓
[GitHub repo: rumee-dashboard / vantage/memory/]
    experiments.json    (committed + pushed after every write)
    learnings.json      (committed + pushed after every write)
    activity_log.jsonl  (committed + pushed after every write)
```

**GitHub raw URLs context_builder.py fetches:**
- `https://raw.githubusercontent.com/Rumeein/rumee-dashboard/main/rumee_db_summary.csv`
- `https://raw.githubusercontent.com/Rumeein/rumee-dashboard/main/rumee_db_daily.csv`

**Status: context_builder.py still reads local file path — switch to HTTP fetch is pending.**

---

## 5. Runner Files — What Each Does

### agent.py

Nightly analysis runner. Three modes:

| Mode | Tables loaded | Use when |
|---|---|---|
| `nightly` (default) | fk_monthly, me_monthly, fk_skus, me_skus, me_return_reasons, me_views | Standard daily run |
| `--full-audit` | Three passes — monthly+SKU, recent daily, state+keywords | When you want a complete deep analysis |

Run:
```
python agent.py --instance-path "D:\Claude RuMee Dashbord\vantage"
python agent.py --instance-path "D:\Claude RuMee Dashbord\vantage" --full-audit
```

### context_builder.py

Assembles everything the LLM needs into a single context string:
- Business profile
- All requested DB tables (from GitHub raw URLs — **pending implementation**)
- Active experiments
- Accumulated learnings
- Last 50 activity log events

**Table modes defined in `_PASS_TABLES`** — controls which DB tables go into each pass.

**fk_skus column rename** (applied in `_format_table()` before data reaches LLM):

| Raw column | Renamed to | Why |
|---|---|---|
| `ad_revenue` | `ad_attributed_revenue_rs` | Prevents LLM from treating it as order count |
| `conversions` | `units_sold_via_ads` | Prevents LLM from treating it as return count |
| `ad_views` | `ad_impressions` | Clarity |
| `settlement` | `revenue_earned_rs` | Clarity |
| `stock` | dropped | All zeros — misleads LLM |

### llm_client.py

Reads `provider` and `model` from `business_profile.json`. Supports Anthropic, OpenAI, Groq. For Rumee: Groq / llama-3.3-70b-versatile.

**Groq limit:** 12,000 tokens per minute (free tier). Context is capped at 8,000 chars in discord_bot.py and system prompt truncated to 8,000 chars to stay within this.

### discord_bot.py

Discord Q&A bot. Watches channel ID `1517718649429954691` (#pipeline on Rumee Discord server).

**Commands:**
| Command | What it does |
|---|---|
| `!status` | Active/suggested experiment counts |
| `!alerts` | Latest alerts from learnings.json |
| Free-form text | LLM Q&A with full business context |

**Bot details:**
- Name: vantage#8332
- Application ID: 1517859731539234826
- Requires Message Content Intent ON in Discord Developer Portal

### memory_writer.py

Parses LLM JSON output and writes to `experiments.json`, `learnings.json`, and `activity_log.jsonl`. After writing, must commit + push to GitHub repo (**pending implementation**).

---

## 6. LLM Configuration

Set in `D:\Claude RuMee Dashbord\vantage\business_profile.json`:

```json
"llm": {
  "provider": "groq",
  "model": "llama-3.3-70b-versatile"
}
```

**Why Groq:** Free, no credit card, 70B quality model. Sufficient for nightly analysis and Discord Q&A.

**To switch to Claude:** Change provider to `anthropic`, model to `claude-sonnet-4-6`, add `ANTHROPIC_API_KEY` to `.env`. All memory files and system prompt work unchanged — no migration needed.

---

## 7. Discord Bot

**Status: Built and tested locally. Not yet hosted on cloud server.**

**Credentials in `D:\Claude RuMee Dashbord\vantage\.env`:**
```
GROQ_API_KEY=...
DISCORD_BOT_TOKEN=...
DISCORD_CHANNEL_ID=1517718649429954691
```

**To run locally:**
```
cd D:\vantage-agent\runner
python discord_bot.py --instance-path "D:\Claude RuMee Dashbord\vantage"
```

**Bot invite URL (already done — do not re-invite):**
`https://discord.com/oauth2/authorize?client_id=1517859731539234826&permissions=68608&scope=bot`

**Known gotcha:** Message Content Intent must be ON in Discord Developer Portal or the bot sees empty messages.

---

## 8. Data Schema — fk_skus (Critical)

`fk_skus` contains **Flipkart ad performance data only**. It does NOT contain order counts or return counts per SKU. Only monthly totals exist in `fk_monthly`.

| Column (after rename) | What it means | What it is NOT |
|---|---|---|
| `ad_attributed_revenue_rs` | Revenue (₹) earned via ad-driven sales | Not order count |
| `units_sold_via_ads` | Units sold specifically via ads | Not return count |
| `ad_impressions` | Times the ad was shown | Not product page views |
| `revenue_earned_rs` | Settlement payout from Flipkart | Not profit |

If asked about FK orders or returns per SKU: **no data exists — say so, do not infer**.

**Real FK return rate:** 43–65% per month (from `fk_monthly`) — alarming, high priority.
**Real Meesho return rate:** 9–15% per month. Per-SKU highest: DJ-14 at 21.4%.

---

## 9. Memory — Where Everything Is Stored

All memory lives in `D:\Claude RuMee Dashbord\vantage\memory\`:

| File | What it holds |
|---|---|
| `experiments.json` | All experiments: status (suggested/monitoring/success/failure), hypothesis, baseline, evaluate_after_days |
| `learnings.json` | What works for Rumee specifically. Also holds alerts from last run. |
| `activity_log.jsonl` | One JSON line per event — every run, every Discord message, every experiment update |

**Decision (2026-06-20):** After every write, memory files must be committed and pushed to the GitHub repo. Nothing stored only on local disk. **Pending implementation in memory_writer.py.**

---

## 10. Eval Loop Plan

Automated training: run test questions through Vantage, judge answers with Claude Haiku, score and patch the system prompt.

**Prerequisite:** Data standardization (fk_skus rename) — DONE.

**Cost:**
- Groq (Vantage answers): Free
- Claude Haiku 4.5 (judge): ~$0.003 per question

**Budget cap:** `BUDGET_USD = 5.95` (₹500) hardcoded at top of eval script. Anthropic console monthly limit also set to $6 as backup.

**Files to create (when building):**
```
D:\vantage-agent\eval\
├── test_suite.json       — 50–200 questions with expected answers
├── run_eval.py           — main loop: Vantage → Haiku judge → log
├── eval_log.jsonl        — per-run results
├── score_report.py       — aggregate stats, failure categories
└── prompt_patcher.py     — auto-patch system_prompt.md from failures
```

**Test categories:**
| Category | Example | What Haiku checks |
|---|---|---|
| fk_skus read | "Which FK SKU has highest ad spend?" | Matches actual top row |
| Return rate | "What is Meesho's overall return rate?" | Within ±2% of me_monthly |
| Stage calibration | "Should we run brand store ads?" | Says NO (Stage 1/2 business) |
| Experiment format | Any suggestion | Has hypothesis + baseline + evaluate_after_days |
| No hallucination | "How many FK orders did DJ-5 get?" | Refuses — data does not exist |
| JSON schema | Nightly run output | Valid JSON matching output schema |

**Status: NOT STARTED — begins after GitHub Actions and cloud hosting are set up.**

---

## 11. Cloud Hosting Plan

**Decision (2026-06-20):** Vantage runs entirely on cloud. No local machine involved after setup.

| Component | Where it runs | Status |
|---|---|---|
| Nightly audit (agent.py) | GitHub Actions — scheduled daily | Not yet implemented |
| Discord Q&A bot (discord_bot.py) | Cloud server — 24/7 (Fly.io or equivalent) | Not yet implemented |
| Memory writes | Committed + pushed to GitHub repo | Not yet implemented |

**GitHub Actions secrets needed:**
| Secret | Value |
|---|---|
| `GROQ_API_KEY` | Groq API key |
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `DISCORD_CHANNEL_ID` | 1517718649429954691 |

---

## 12. Build Status

| Component | Status |
|---|---|
| system_prompt.md — expert persona | Done |
| agent.py — nightly runner | Done |
| context_builder.py — assembles context | Done |
| llm_client.py — Groq/Anthropic/OpenAI | Done |
| memory_writer.py — writes experiments + learnings | Done |
| discord_bot.py — Discord Q&A | Done, tested locally |
| First full-audit run | Done (2026-06-20) — 4 alerts, 4 experiments, 3 learnings |
| fk_skus column rename (data standardization) | Done (2026-06-20) |
| system_prompt.md — Data Schema section | Done (2026-06-20) |
| system_prompt.md — Discord response format | Done (2026-06-20) |
| context_builder.py reads from GitHub raw URLs | **Pending** |
| memory_writer.py commits + pushes to GitHub | **Pending** |
| Nightly audit on GitHub Actions | **Pending** |
| Discord bot on cloud server (24/7) | **Pending** |
| Eval loop (automated training) | Not started |
| onboard.py (new business onboarding) | Not started |

---

## 13. Key Decisions

| Decision | What was decided | Date |
|---|---|---|
| Communication platform | Discord (not Telegram — banned in India) | 2026-06-19 |
| LLM provider | Groq / llama-3.3-70b-versatile (free) | 2026-06-20 |
| Data source | GitHub raw URLs from rumee-dashboard repo — not local files | 2026-06-20 |
| Memory storage | Committed to GitHub repo after every write — not local disk only | 2026-06-20 |
| No local machine | Everything runs on GitHub Actions + cloud server after setup | 2026-06-20 |
| fk_skus interpretation | Ad performance data only — no SKU-level orders or returns exist | 2026-06-20 |
| Eval loop budget | ₹500 hard cap — hardcoded in script + Anthropic console limit as backup | 2026-06-20 |
| Cloud hosting platform | Fly.io (free tier, always on) — to be confirmed | 2026-06-20 |

---

## 14. How to Run

### Nightly analysis (local, for testing)
```
cd D:\vantage-agent\runner
python agent.py --instance-path "D:\Claude RuMee Dashbord\vantage"
```

### Full audit (local, for testing)
```
cd D:\vantage-agent\runner
python agent.py --instance-path "D:\Claude RuMee Dashbord\vantage" --full-audit
```

### Discord bot (local, for testing)
```
cd D:\vantage-agent\runner
python discord_bot.py --instance-path "D:\Claude RuMee Dashbord\vantage"
```

### Production (once GitHub Actions is set up)
- Nightly audit runs automatically on schedule — no manual step
- Discord bot runs on cloud server — no manual step
- Memory writes commit + push to GitHub automatically — no manual step
