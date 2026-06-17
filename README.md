# Vantage — AI Ecommerce Growth Advisor

Vantage is a persistent AI agent that acts as a world-class ecommerce growth operator for your business. It analyzes your sales, ad, and catalog data, identifies specific opportunities, creates experiments with measurable hypotheses, tracks outcomes, and learns what works — for your business specifically.

It is not a chatbot. It is an expert advisor that gets smarter the longer it runs.

---

## What Vantage Does

- Analyzes your catalog and SKU-level data when there is something worth acting on
- Suggests experiments — specific, one-variable changes with a clear hypothesis and baseline
- Tracks whether experiments succeeded or failed
- Accumulates learnings about your business over time
- Answers your questions interactively via Telegram, with full context of your data and history
- Learns from external content — YouTube videos, reels, articles — and tells you what applies to your business and what does not

Vantage does not run experiments on a fixed schedule. It acts when the business data shows an opportunity or a problem worth addressing.

---

## Core Concepts

### Experiments

Every suggestion Vantage makes is an experiment — not a directive. An experiment has:

- **Hypothesis**: "If we [specific change] on [catalog], then [metric] will [direction] from [baseline] to [target] because [mechanism]"
- **Baseline**: The current value of the metric before the change
- **Evaluation date**: When to check the result (7 days for clicks/CTR, 14 days for orders/ROAS, 30 days for return rate)
- **Status**: suggested → monitoring → success or failure

You implement the change. Vantage monitors the outcome. If it succeeds, Vantage replicates the change to similar catalogs as new experiments. If it fails, it marks it failed and tries the next hypothesis.

### Learning Scopes

Vantage classifies every learning it accumulates:

| Scope | What it means | Example |
|---|---|---|
| `universal` | True for any business on this platform | Lifestyle images on Meesho increase conversion by ~20% |
| `category` | True for this product category | Jewellery peaks during Navratri and Nov–Feb wedding season |
| `business` | Specific to this business only | This seller's buyers prefer ₹299 over ₹349 — confirmed twice |

Universal and category learnings are stored in `shared_learnings.json` (in this repo) and are available to all businesses. Business-specific learnings stay in that business's own `memory/learnings.json`.

When Vantage applies a shared learning to a new business, it always treats it as an untested hypothesis for that business — not a confirmed fact — until that business's own data confirms it.

### LLM-Agnostic Design

Vantage is not tied to any one AI provider. The system prompt (`system_prompt.md`) and all memory files are plain text and JSON. You configure which LLM to use in `business_profile.json`. The same memory works with Claude today and GPT tomorrow — no migration needed.

---

## Repository Structure

```
vantage-agent/
├── system_prompt.md            The expert persona — the brain of the agent
├── shared_learnings.json       Universal + category learnings across all businesses
├── runner/
│   ├── agent.py                Nightly analysis runner
│   ├── telegram_bot.py         Interactive Telegram layer
│   ├── context_builder.py      Assembles memory + data into LLM context
│   ├── llm_client.py           LLM-agnostic API caller (Anthropic / OpenAI)
│   ├── memory_writer.py        Writes LLM output back to memory files
│   └── onboard.py              New business onboarding interview (coming soon)
├── templates/
│   ├── business_profile.json   Starter template — copy to your business repo
│   ├── experiments.json        Empty experiments file
│   └── learnings.json          Empty learnings file
├── schemas/                    JSON schemas for validation
├── config.example.env          Environment variables template
└── requirements.txt
```

Your business data lives in **your own repository** — not here:

```
your-business-repo/
└── vantage/
    ├── business_profile.json   Who your business is, which platforms, which stage
    ├── .env                    API keys (never committed to git)
    ├── memory/
    │   ├── experiments.json    All experiments and their outcomes
    │   ├── learnings.json      What works for your business specifically
    │   └── activity_log.jsonl  Complete log of everything Vantage has done
    └── data/
        └── latest/
            ├── catalog_metrics.csv    Per-catalog/SKU data (refreshed nightly)
            └── platform_summary.csv   Platform totals
```

---

## Setup — New Business

### Step 1: Clone or fork this repo

```bash
git clone https://github.com/rumeein/vantage-agent.git
cd vantage-agent
pip install -r requirements.txt
```

### Step 2: Create your business instance

In your business repository, create a `vantage/` folder and copy the templates:

```
your-repo/
  vantage/
    business_profile.json     (copy from templates/)
    memory/
      experiments.json        (copy from templates/)
      learnings.json          (copy from templates/)
    data/latest/              (create empty folder)
```

### Step 3: Fill in business_profile.json

Open `vantage/business_profile.json` and fill in every field. See the [Business Profile Reference](#business-profile-reference) section below.

The most important fields:
- `business.stage` — determines what kind of advice Vantage gives (1 = small, 4 = large)
- `platforms` — which platforms are active and what is the current focus
- `current_focus` — what problems you are trying to solve right now

### Step 4: Set up environment variables

Copy `config.example.env` to `your-repo/vantage/.env` and fill in your keys:

```env
ANTHROPIC_API_KEY=your_key_here
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_ALLOWED_CHAT_IDS=your_telegram_chat_id
```

The `.env` file must never be committed to git. Add `vantage/.env` to your `.gitignore`.

### Step 5: Connect your data

Your pipeline or data export must write two CSV files to `vantage/data/latest/` before each Vantage run. See the [Data Format](#data-format) section for the exact column definitions.

### Step 6: Test the first run

```bash
cd vantage-agent/runner
python agent.py --instance-path "path/to/your-repo/vantage"
```

Check the output and `vantage/memory/activity_log.jsonl` for the result.

---

## Running Vantage

### Nightly Agent

```bash
python runner/agent.py --instance-path "/path/to/your/vantage"
```

Run this after your data pipeline completes. Recommended: schedule it via cron or a task scheduler.

**What it does:**
1. Loads system prompt + all memory files + latest CSV data
2. Calls the LLM
3. Parses the structured JSON response
4. Writes new experiments to `experiments.json`
5. Updates experiment statuses based on monitoring results
6. Appends new learnings to `learnings.json`
7. Logs the full event to `activity_log.jsonl`

Vantage only creates an experiment when the data shows a genuine opportunity. If nothing is worth acting on, it says so and exits cleanly.

### Telegram Bot (Interactive)

```bash
python runner/telegram_bot.py --instance-path "/path/to/your/vantage"
```

Keep this running as a background process. You can ask it anything via Telegram.

**Built-in commands:**

| Command | What it does |
|---|---|
| `/status` | Summary of active experiments (suggested, monitoring, outcomes) |
| `/alerts` | Any current urgent issues flagged by the last run |
| `/exp <id>` | Full detail on a specific experiment |

**What you can ask in plain language:**

- "Why did you suggest changing the thumbnail for catalog X?"
- "Our ROAS dropped this week, what could be the reason?"
- "Is it worth running ads on Meesho right now?"
- "I watched this video [YouTube link] — is this relevant for us?"
- "Mark experiment exp_20260617_001 as done, I made the change"

Every conversation is logged to `activity_log.jsonl` with timestamp, your message, and Vantage's reply.

### Video and External Content

Send Vantage a YouTube URL, Instagram Reel URL, or a video/audio file via Telegram. It will:

1. Extract the transcript (YouTube) or transcribe the audio (video/audio files)
2. Read it against your business context, data, and experiment history
3. Tell you point by point what applies to your business, what does not, and why

Example reply format:
> "This video recommends X. For your business: point 1 applies — your CTR data supports this. Point 2 does not apply — you are a Stage 2 seller and this tactic requires a dedicated team. Point 3 is untested here — I can set it up as an experiment if you want."

---

## Business Profile Reference

Full field documentation for `business_profile.json`:

```json
{
  "business": {
    "name": "Your business name",
    "category": "Product category e.g. Artificial Jewellery",
    "description": "One line: what you sell, to whom, at what price range",
    "stage": 2,
    // Stage 1: < ₹5L/month | Stage 2: ₹5L–₹25L | Stage 3: ₹25L–₹1Cr | Stage 4: ₹1Cr+
    "monthly_revenue_approx": "few lakhs"
  },
  "platforms": {
    "meesho": {
      "active": true,
      "seller_id": "your seller ID (optional, for reference)",
      "maturity": "new | growing | mature",
      "focus": "What you are currently trying to improve on this platform"
    },
    "flipkart": {
      "active": true,
      "seller_id": "",
      "maturity": "growing",
      "focus": ""
    }
  },
  "current_focus": [
    "Problem 1 you are actively trying to solve",
    "Problem 2"
  ],
  "known_issues": [
    "Issues Vantage should be aware of e.g. high return rate in Oct 2024"
  ],
  "constraints": [
    "Limitations e.g. no professional photographer, pricing floor ₹99"
  ],
  "data_source": {
    "type": "csv",
    "path": "data/latest"
    // relative to this business_profile.json file
  },
  "llm": {
    "provider": "anthropic",
    // Options: anthropic, openai
    "model": "claude-sonnet-4-6"
    // Anthropic models: claude-sonnet-4-6, claude-opus-4-8, claude-haiku-4-5-20251001
    // OpenAI models: gpt-4o, gpt-4o-mini
  },
  "telegram": {
    "allowed_chat_ids": [123456789]
    // Your Telegram chat ID. Get it from @userinfobot on Telegram.
    // Leave empty [] to allow any chat (not recommended).
  }
}
```

---

## Data Format

Your pipeline must write these two CSV files to `vantage/data/latest/` before each run.

### catalog_metrics.csv

One row per catalog/SKU per platform.

| Column | Type | Description |
|---|---|---|
| `catalog_name` | string | Catalog or SKU name |
| `platform` | string | meesho / flipkart / amazon |
| `units_sold_7d` | integer | Units sold in last 7 days |
| `revenue_7d` | float | Revenue in ₹ in last 7 days |
| `ad_spend_7d` | float | Ad spend in ₹ in last 7 days (0 if no ads) |
| `roas_7d` | float | Return on ad spend (revenue / ad_spend). 0 if no ads. |
| `views_7d` | integer | Product page views / impressions in last 7 days |
| `return_rate_pct` | float | Return rate as percentage e.g. 12.5 |
| `stock_qty` | integer | Current stock quantity |
| `last_upload_date` | date | YYYY-MM-DD — date catalog was last updated/uploaded |

### platform_summary.csv

One row per platform.

| Column | Type | Description |
|---|---|---|
| `platform` | string | meesho / flipkart / amazon |
| `total_revenue_7d` | float | Total revenue across all catalogs |
| `total_ad_spend_7d` | float | Total ad spend |
| `total_roas_7d` | float | Blended ROAS |
| `total_orders_7d` | integer | Total orders |
| `avg_return_rate_pct` | float | Average return rate across all catalogs |
| `active_catalogs` | integer | Number of catalogs with at least 1 sale in last 30 days |

---

## Activity Log Format

Every event Vantage performs is appended to `memory/activity_log.jsonl` as a single JSON line. This file is the complete audit trail — every decision, every experiment, every conversation.

**Event types:**

| Event | When | Key fields |
|---|---|---|
| `nightly_run` | After every agent.py run | summary, alerts_count, new_experiments_count |
| `telegram_message` | After every Telegram exchange | user_message, reply_length |
| `experiment_updated` | When an experiment status changes | experiment_id, old_status, new_status |
| `learning_promoted` | When a learning is moved to shared_learnings | learning, scope |
| `error` | When anything fails | message, stack trace |

To read the log for a specific catalog or experiment, search by the catalog name or experiment ID:

```bash
grep "catalog_name_here" memory/activity_log.jsonl
grep "exp_20260617_001" memory/activity_log.jsonl
```

---

## Experiment Lifecycle

```
SUGGESTED
  └─ You implement the change in the platform
       └─ MONITORING (14 days)
             ├─ Data improves → SUCCESS
             │     └─ Vantage replicates to similar catalogs
             └─ Data flat or worse → FAILURE
                   └─ Logged in learnings. Not suggested again.
```

When you implement a suggested experiment, tell Vantage via Telegram:
> "I made the change for exp_20260617_001"

Vantage will update the status to `monitoring` and start tracking the outcome.

---

## Switching LLM Providers

To switch from Claude to GPT (or back):

1. Edit `business_profile.json`:
   ```json
   "llm": { "provider": "openai", "model": "gpt-4o" }
   ```
2. Add `OPENAI_API_KEY` to your `.env`
3. Run as normal

All memory files (experiments, learnings, activity log) are plain JSON — they work with any provider. The system prompt (`system_prompt.md`) is also plain text and works unchanged.

---

## Adding a Second Business

Each business is a separate instance. You do not need to modify the vantage-agent repo.

1. Create `vantage/` folder in the second business's repository
2. Copy templates and fill in `business_profile.json` for that business
3. Set up `.env` for that business
4. Run the agent with `--instance-path` pointing to that business's `vantage/` folder

The two businesses do not share memory files. Universal and category learnings from `shared_learnings.json` (this repo) are available to both, but each business's own learnings stay in their own instance.

---

## Onboarding a New Business (Planned)

An interactive onboarding flow via Telegram (`onboard.py`) is in development. It will interview the business owner before the first run:

- What do you sell? Who is your buyer?
- Which platforms are you on? How long?
- What is currently working? What is not?
- What have you already tried?
- What is the biggest problem right now?

From the answers, it writes `business_profile.json` and seeds the first entries in `learnings.json` — marked as "stated by owner, not yet data-confirmed." The first nightly run then takes it from there.

---

## Troubleshooting

**Agent exits with "Failed to parse LLM response"**
The LLM returned unstructured text instead of JSON. Check `activity_log.jsonl` for the raw response. Usually caused by a very large context — reduce `ACTIVITY_LOG_TAIL` in `context_builder.py` (default: 50 events).

**Telegram bot does not respond**
- Check `TELEGRAM_BOT_TOKEN` is correct in `.env`
- Check `TELEGRAM_ALLOWED_CHAT_IDS` includes your chat ID
- Run the bot with `python telegram_bot.py --instance-path ...` and watch the console for errors

**No experiments generated after nightly run**
Vantage only suggests experiments when data shows a genuine opportunity. If the data is flat and healthy, no experiments is the correct output. Check `activity_log.jsonl` for the run's summary.

**Data files not found**
Verify `data/latest/catalog_metrics.csv` and `platform_summary.csv` exist and have data. Check `data_source.path` in `business_profile.json` is correct (relative to the profile file).

---

## Requirements

- Python 3.10+
- `pip install -r requirements.txt`
- An API key for Anthropic or OpenAI (one is enough)
- A Telegram bot token (from @BotFather) for the interactive layer

---

## License

MIT — free to use, modify, and deploy for any business.
