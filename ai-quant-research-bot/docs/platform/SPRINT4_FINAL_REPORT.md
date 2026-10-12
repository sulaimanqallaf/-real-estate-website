# Sprint 4: Autonomous AI Quant Research Company — Final Report

**Branch**: `claude/sprint4-autonomous-research-company`
**Final commit**: `89dedef1b884e2a3d319239d685fdaa73d4c564a`
**Base**: `claude/sprint3-reliability-real-data`, commit `cbe7cbb2a0abb95c841f18dab736bfd7491545ff`
(preserved intact — this branch only ever adds commits on top of it)
**7 commits**, all six requested priorities complete.

This report answers what the sprint kickoff asked for at completion:
a verified report distinguishing code-tested evidence from real-market/
broker evidence, remaining blockers, and the minimum steps you
personally need to take. It does not repeat `docs/platform/
SPRINT3_FINAL_REPORT.md` (the prior sprint's own report, still accurate
for what it covers) or `docs/platform/BLOCKERS.md` (quoted, not
rewritten, below).

## 1. What was built, priority by priority

### SETUP — Shared agent runtime (`src/agents/`)
`registry.py` (durable per-agent job status: RUNNING/SUCCESS/FAILED,
one JSON file per agent so a crash mid-write only ever corrupts one
agent's record), `heartbeat.py` (the same crash-recovery pattern
`execution/watchdog.py` built for `position_monitor.py`, generalized
to any agent name), `budget.py` (daily per-source call caps — real
rate quotas worth respecting even at $0 cost), `checkpoint.py`
(resumable mid-cycle state, cleared on success). Every agent built on
this package is proven, by test and by source inspection
(`tests/test_agents_framework.py`), to add no new execution privilege.

### Priority 1 — One-click real-world validation (`src/validation_suite.py`)
Orchestrates Sprint 3's strategy validation report, IEX data check,
and connectivity preflight into one `SuiteReport` with honest
PASS/FAIL/BLOCKED/SKIPPED verdicts (the exact constants
`connectivity_preflight.py` already defined, never redefined). A new
`mac_launcher/Run Real Validation.command` runs it with no terminal
typing and never prompts for or logs a credential. Does not touch
`src.main`'s scheduler and never calls `submit_order`/`cancel_order`.

### Priority 2 — Research Scholar Agent (`src/agents/research_scholar.py`)
Real HTTP clients for OpenAlex, arXiv, and Semantic Scholar
(`src/data_providers/{openalex,arxiv,semantic_scholar}_provider.py`),
deduplicated by DOI → arXiv id → normalized title
(`research/papers.py`), scored for evidence strength, mapped to
falsifiable hypotheses only when a strategy genuinely has the
matching tunable parameter (`research/hypothesis_mapper.py` — never a
guessed override), tested through the existing sandbox, and recorded
permanently with citations in its own SQLite store
(`research/paper_memory.py`). Own process lock, checkpointed, daily
budget-capped per source.

### Priority 3 — News Agent (`src/agents/news_agent.py`)
"Financial news" is deliberately scoped to official SEC 8-K
material-event filings — no scraping, no new signup-required service
(even a free one), consistent with a conservative reading of "do not
purchase services." Also pulls FRED macro series (pre-existing
provider) and issues a daily Telegram briefing. A persistent,
separate-from-checkpoint "seen" dedup file (`news_agent_seen.json`)
avoids re-alerting on the same filing across runs.

### Priority 4 — Strategy Lab (`src/agents/strategy_lab.py`)
Generates candidates by sweeping each strategy's own tunable window
(never a fabricated knob for Trend Following, which has none), then
runs every candidate through code this project already trusts,
unmodified: `walk_forward.run_walk_forward()` for out-of-sample
testing, `monte_carlo.run_monte_carlo_stress_test()` and the same
evidence-gated verdict Sprint 3's V3 built for overfitting/leakage
defense, `backtester_crosscheck.run_crosscheck()` for the optional
VectorBT cross-check, and the same `oss_quant_adapter` subprocess task
for QuantStats metrics. Every candidate lands in the SAME
`hypothesis_ledger` the Research Scholar writes to — one shared record
regardless of which agent proposed it.

### Priority 5 — AI Company Dashboard
Two new read-only panels: `AgentOperationsPanel.tsx` (`GET /api/
agent-operations`) shows every agent's real RUNNING/SUCCESS/FAILED
status and heartbeat freshness; `ResearchMemoryPanel.tsx` (`GET /api/
research-memory`) shows every recorded paper with its citation count,
evidence score, and which hypothesis IDs (if any) it produced — so a
hypothesis is always traceable to a citation. `readonly.py`'s
AST-enforced `src.*` import allow-list was extended by exactly three
names (`src.agents.registry`, `src.agents.heartbeat`,
`src.research.paper_memory`), each a pure read with no network call.

### Priority 6 — Resilience wrap-up (`src/agents/health_report.py`)
Aggregates all three new agents' registry status, heartbeat, and
daily budget usage into one fleet report, and sends a **deduplicated**
Telegram alert on a newly FAILED run or a newly stale heartbeat — new
ground this sprint adds, since Sprint 3's own alert senders fire
unconditionally on every call (fine for a position-monitor watchdog,
wrong for a periodic fleet check). Strategy Lab also gained its own
Telegram run summary, closing the one gap where a Sprint 4 agent had
no notification wiring at all.

## 2. Test results

| Point in the sprint | Full suite result |
|---|---|
| Start of Sprint 4 (Sprint 3's final state) | 1395 passed, 1 skipped |
| End of Sprint 4 (this report, HEAD `89dedef`) | **1595 passed, 1 skipped, 0 failures** |

**200 net new/changed tests**, verified passing after every milestone
in this sprint (re-run in full after P5 and again after P6), not just
once at the end. Dashboard backend: 56 passed (included in the same
root `pytest` run — `dashboard/backend/` is collected automatically).
Dashboard frontend: 21 passed (`vitest`), plus a clean `tsc -b`
type-check — both re-verified for this report.

## 3. Real-market/broker evidence — read this section literally

**This sandbox's egress proxy blocks every external host this sprint
needed**, confirmed directly with `curl`, not assumed:
`api.openalex.org`, `export.arxiv.org`, `api.semanticscholar.org`, and
`www.sec.gov/files/company_tickers.json` all return a `403 Forbidden
organization policy` CONNECT-tunnel rejection — the same class of
block Sprint 3 already hit on every financial-data-vendor host. This
is a hard environment limitation, not a code gap, and it means:

- **No academic paper has been fetched from a real OpenAlex/arXiv/
  Semantic Scholar response in this session.** Unlike Sprint 3's
  Alpaca provider (verified against the real `alpaca-py` SDK source),
  there is no SDK to check these three providers' JSON/XML shape
  against — each is written from that API's publicly documented
  format, as best understood, and labeled that way in its own
  docstring and in `docs/platform/BLOCKERS.md` items 10 and 11 (new
  this sprint). `tests/test_openalex_provider.py`/
  `test_arxiv_provider.py`/`test_semantic_scholar_provider.py` prove
  the PARSING LOGIC is correct against hand-built fixtures matching
  the documented schema; none of them prove the real API still
  matches that schema.
- **No SEC ticker→CIK resolution has been verified against the real
  `company_tickers.json` file.** Same posture: parsing logic tested
  against a hand-built fixture, never a real response.
- **No Strategy Lab candidate has been evaluated against real market
  history.** `run_lab_cycle()`'s own tests exercise the REAL
  backtester/walk-forward/Monte-Carlo/evidence-verdict logic
  end-to-end — that logic is genuinely correct and tested — but only
  ever against `analytics/synthetic_fixtures.py`'s deterministic
  synthetic series, for the same reason Sprint 3's V1-V3 report
  already stated: this sandbox cannot reach yfinance either.
- **Priority 1's validation suite has never produced a real PASS.**
  `src/validation_suite.py` orchestrates genuinely real logic
  (Sprint 3's own strategy validation, IEX check, and connectivity
  preflight), but every one of its own inputs is still blocked from
  this sandbox for the same reasons Sprint 3 documented — running it
  here can only produce `BLOCKED`/`SKIPPED`, never a real `PASS`.
- Nothing in this report, in any module's docstring, or in
  `docs/platform/BLOCKERS.md` claims otherwise. Items 10 and 11 are
  new this sprint; items 1, 2, 8, and 9 (IBKR paper, licensed data
  feed, Alpaca/IEX real-data check, real-data strategy validation)
  are unchanged and still open from Sprint 3.

This is the honest state of "real-market and broker evidence" at the
end of this sprint: every new piece of validation/research/learning
MACHINERY is real, tested, and ready; no actual real-market or
real-broker RESULT exists yet, because this sandbox cannot produce
one. The dashboard panels, fleet health report, and Telegram
notifications all report this truthfully — an agent that has never
run shows `None`/"never run," never a fabricated status.

## 4. Remaining blockers

All eleven items in `docs/platform/BLOCKERS.md` (two new this sprint —
10 and 11), in the order they'd most likely unblock further work:

1. Real IBKR TWS/Gateway paper session (unchanged from Sprint 3).
2. Licensed live/delayed market data feed (unchanged).
3. Forex feed/broker support; crypto broker support (unchanged).
4. Real copyrighted research for the RAG knowledge library (unchanged).
5. Apple Developer signing identity for the Mac launcher (unchanged).
6. Cloud account for 24/7 deployment (unchanged — nothing deployed
   this sprint either).
7. Your own $500 real-money authorization (unchanged — nothing in
   this sprint moves this forward or needs it).
8. Real-data validation of the Alpaca/IEX feed (Sprint 3, unchanged).
9. Real-data run of the backtest/walk-forward/validation report
   (Sprint 3, unchanged).
10. **New**: real-data verification of the Research Scholar Agent's
    three academic providers (Task P2).
11. **New**: real-data verification of the News Agent's SEC
    ticker→CIK resolver (Task P3).

Nothing was purchased (including a free-tier signup for any new
service — News Agent was deliberately scoped to SEC filings instead of
a new API specifically to honor this), no cloud resource was deployed,
no credential was exposed or logged (every dynamic string in every new
Telegram-sending path is passed through `utils.redact_secrets()`
first), your running Mac bot's 7 existing `com.aiquantresearchbot.*`
launchd jobs were never touched, no new launchd job was installed as
an actual file (every reserved label this sprint, including
`agentfleethealth`, is documented-only, exactly like every job before
it), live trading remains impossible by construction, and no new
agent can promote anything into production: `grep -rn "def promote("
src/` finds nothing, and the only "execution"-adjacent import anywhere
in `src/agents/*.py` is the generic `execution/process_lock.py`
singleton-lock utility (used for mutual exclusion, not order
placement) — verified directly for this report, not assumed.

## 5. Minimum steps you personally need to complete

On a machine with real outbound network access (your Mac is
sufficient — none of this needs this sandbox):

1. **See real research evidence**: run
   `python -m src.agents.research_scholar` (no credentials needed —
   all three academic APIs are free and keyless) and inspect
   `data/research/paper_memory.db` for real papers with sane-looking
   titles/abstracts/citation counts. Read the very first run's output
   yourself rather than assuming it matches the documented schema.
2. **See real news/filings evidence**: set `SEC_IDENTITY` in `.env`
   (required by SEC's fair-access policy) and run
   `python -m src.agents.news_agent`.
3. **See real learning-lab evidence**: once Sprint 3's real-data
   strategy validation works on your machine (see its own report,
   step 1), run `python -m src.agents.strategy_lab` and read its
   candidates' verdicts literally.
4. **Run the one-click validation suite for real**: double-click
   `mac_launcher/Run Real Validation.command`, or run
   `python -m src.validation_suite` yourself, and read the PASS/FAIL/
   BLOCKED/SKIPPED lines it prints — do not treat a `SKIPPED` as a
   `PASS`.
5. **Turn on the new agents' Telegram notifications** (optional): set
   `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` as you already have for the
   rest of the bot — no new credential type is introduced this sprint.
6. **See it all in the dashboard**: start the dashboard as before;
   the two new panels populate automatically from whatever the agents
   above actually produce, with no further setup.
7. **Optional**: add new launchd jobs for the three agents plus
   `python -m src.agents.health_report` on whatever cadence you like
   (daily for the research/news/lab agents, e.g. every 30 minutes for
   the health report) — see the README sections this sprint added for
   each one's reserved label. None of this is required for any of the
   above to work by hand first.

Autonomous order submission remains OFF
(`autonomous_paper.enabled`/`auto_execute.enabled` are still `false` in
`config/settings.yaml`) and stays that way until you explicitly decide
otherwise. Every new strategy candidate Priority 4 generates is a row
in a database, never a deployment — reviewing those rows and deciding
whether anything is worth a human-reviewed strategy change remains
entirely outside this codebase, by design.
