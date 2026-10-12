# Sprint 5: Real-World Activation — Final Report

**Branch**: `claude/sprint5-real-world-activation`
**Final commit**: `b7cbe8a1772aa383a01a7412beaa4f7c05c25e20`
**Base**: `claude/sprint4-autonomous-research-company`, commit
`ed9c37c59a4308e738ae1c899d2be331fb9436cc` (preserved intact)
**2 commits**, all twelve requested items addressed.

This report answers exactly what the sprint kickoff asked for: a
verified report, full regression results, and **one simple Mac setup
procedure for the remaining real-world validation** (section 5).

## 1. What was built, item by item

### Items 1 — safely update a local copy
**No new code was needed.** `mac_launcher/Check for Updates.command`
(built in Sprint 4) already does exactly this: fetches your current
branch from GitHub, shows you the pending commits, only applies them
if you type `y`, stashes any uncommitted local changes first
(recoverable with `git stash pop`), and always creates a timestamped
`pre-update-<UTC timestamp>` git tag immediately before pulling - the
rollback point `Agent Control Center.command`'s own rollback option
(item 10) now reads back. It never touches `.env` or `data/` (both
gitignored; git operations don't touch gitignored files at all).
Confirmed by re-reading its source this sprint, not assumed.

### Items 2, 5 — real yfinance validation + real Alpaca/IEX
**Unchanged, reused.** Sprint 4's `src/validation_suite.py` already
runs the cost-inclusive strategy validation report against real
yfinance history and the Alpaca Basic/IEX data check. Sprint 5's
`src/activation_suite.py` runs it wholesale, unmodified, as the first
section of its own combined report.

### Items 3, 6, 11 — real academic providers + honest statuses + failure visibility
- **New `STATUS_BLOCKED`** (`src/data_providers/base.py`): distinct
  from `STATUS_ERROR`. All five providers that make outbound HTTP
  calls (OpenAlex, arXiv, Semantic Scholar, SEC - 3 call sites, FRED)
  now classify a caught exception via
  `reliability.is_retryable_network_error()`: a connection/timeout/5xx
  failure → `BLOCKED` ("never reached a real answer"); anything else
  (a 4xx, malformed JSON/XML) → `FAIL` ("reached it, got something
  wrong") - the same philosophy `connectivity_preflight.py` already
  used for the broker connection.
- **`src/activation_suite.py`**'s academic-provider check calls each
  real `fetch_papers()` and classifies the real result onto
  `PASS`/`FAIL`/`BLOCKED`/`SKIPPED`. If at least one provider returns
  a real paper, it ALSO records one deduplicated sample into
  `research.paper_memory`'s permanent SQLite store and reads it back -
  concrete, end-to-end proof that a real paper enters permanent
  research memory, not just a reachability ping.
- **Item 11's gap, found and closed**: before this sprint, a provider
  exception or a real `STATUS_ERROR`/`STATUS_BLOCKED` result collapsed
  silently to an empty list inside `research_scholar.py`/
  `news_agent.py` - only a `logger.warning` line marked it, invisible
  in the returned summary, the Telegram notification, or the registry.
  Both agents now record a real failure into
  `summary["provider_issues"]`, surfaced in their Telegram
  notifications (now sent even when nothing new happened, if an issue
  occurred), `health_report.py`'s fleet report text, AND (a real gap
  found while reviewing dashboard coverage for item 8) the dashboard's
  `AgentOperationsPanel.tsx`, which read the data from the backend
  already but never rendered it.

### Item 4 — real SEC ticker→CIK + FRED
`activation_suite._run_sec_and_fred()`: a real `fetch_company_tickers()`
call with a sanity check that AAPL's real CIK resolves out of the
fetched map (a fetch that SUCCEEDS but can't find AAPL is itself a
`FAIL`, not a quiet pass - a response that passes but looks wrong is
exactly the case Sprint 4's academic providers were already flagged as
unable to detect); a real FRED Fed-funds-rate fetch. Both `SKIPPED`
outright when `SEC_IDENTITY`/`FRED_API_KEY` aren't configured.

### Item 7 — real Telegram delivery test
`activation_suite._run_telegram_delivery()` sends ONE real, clearly
labeled test message via the existing `telegram_bot.send_telegram_message()`
when already configured; `PASS`/`FAIL` on whether it actually sent,
`SKIPPED` otherwise. Never constructs, logs, or echoes the token/chat
id - same `.env`-only path every other command in this project uses.

### Item 8 — dashboard: all agent statuses, findings, learning experiments
Mostly already satisfied by Sprint 4's `AgentOperationsPanel.tsx`
(status/heartbeat), `ResearchMemoryPanel.tsx` (papers with citations),
and `ResearchAnalyticsPanel.tsx` (hypotheses - Strategy Lab's
candidates and Research Scholar's hypotheses share the same
`hypothesis_ledger`, so both already appear there with zero extra
wiring). The one genuine gap found this sprint: `provider_issues`
(item 11's new visibility data) flowed through the backend already
but wasn't rendered - fixed in `AgentOperationsPanel.tsx`.

### Item 9 — explicit, opt-in, conflict-safe scheduling
**New `src/agents/launchd_installer.py`** - the first code in this
project that actually writes a `.plist` file and calls `launchctl
load`/`unload`; every prior sprint's launchd setup was README-
documented copy-paste prose only. Nothing in it runs on its own:
every install/uninstall needs an explicit call, gated by
`mac_launcher/Agent Control Center.command`'s own `[y/N]` prompt per
job. **Conflict-safe by hard assertion**: `RESERVED_EXISTING_LABELS`
lists the 7 pre-existing jobs; `assert_label_is_new()` raises if
asked to touch any of them, checked at import time against the four
new labels this module can manage
(`researchscholar`/`newsagent`/`strategylab`/`agentfleethealth`). No
credential is ever written into a `.plist`.

### Item 10 — Start/Stop/Status/Logs/Rollback
**New `mac_launcher/Agent Control Center.command`**: one menu for the
agent fleet - show status, run any agent once now (foreground, no
broker access, ever), install/uninstall its schedule (item 9, above),
tail a log, or roll back to the git tag `Check for Updates.command`
already creates (item 1) - refuses outright if the working tree has
uncommitted changes, rather than silently discarding them.

### Item 12 — DRY_RUN / no execution privilege increase
Verified directly for this report (not assumed): `grep -rn "def
promote(" src/` finds nothing; a full diff of every line this sprint
added or changed was grepped for `submit_order`/`cancel_order`/
`replace_order`/`autonomous_paper.enabled`/`execution.mode=`/
`IBKR_LIVE` - every hit is a docstring/README sentence stating what
is NEVER called, not a new call site. No new module imports
`src.execution` (the only "execution"-adjacent reference anywhere is
a docstring sentence naming `connectivity_preflight` as the source of
the `BLOCKED`/`FAIL` vocabulary). `config/settings.yaml`'s
`autonomous_paper.enabled`/`auto_execute.enabled` are untouched.

## 2. Test results

| Point in the sprint | Full suite result |
|---|---|
| Start of Sprint 5 (Sprint 4's final state) | 1595 passed, 1 skipped |
| After S5-3 (provider classification + failure visibility) | 1611 passed, 1 skipped |
| End of Sprint 5 (this report, HEAD `b7cbe8a`) | **1693 passed, 1 skipped, 0 failures** |

**98 net new/changed tests**, re-verified after every milestone, not
just once at the end. Dashboard frontend: `tsc -b` clean, `vitest`
21/21 passing (unchanged count - the `AgentOperationsPanel.tsx` fix
needed no new test harness, since this project's frontend tests cover
scene/event-mapping logic, not panel rendering, and that convention
was kept rather than introduced ad hoc for one component).

## 3. Real-world evidence — read this section literally

**Genuinely new this sprint: real evidence, not just mocked unit
tests, that the BLOCKED/FAIL/SKIPPED classification behaves correctly
against a real failure.** `mac_launcher/Run Full Activation Check.command`
was run for real (non-interactively) in this sandbox, with a temporary
`.venv` symlink to the system interpreter (never committed - `.venv/`
is gitignored). The result, verbatim:

```
--- academic_providers ---
[BLOCKED] openalex: failed to fetch OpenAlex works for query 'stock market momentum anomaly':
  HTTPSConnectionPool(...) Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden'))
[BLOCKED] arxiv: failed to fetch arXiv results for query 'stock market momentum anomaly':
  HTTPConnectionPool(...) Caused by NameResolutionError(...)
[BLOCKED] semantic_scholar: failed to fetch Semantic Scholar results for query 'stock market momentum anomaly':
  HTTPSConnectionPool(...) Caused by ProxyError(...)
[SKIPPED] paper_memory: no real paper was fetched this run - nothing to record

Summary: 1 PASS, 0 FAIL, 5 SKIPPED, 7 BLOCKED
```

This is a REAL caught `ProxyError`/`ConnectionError` from this
sandbox's own egress policy - not a simulated one in a test fixture -
correctly classified as `BLOCKED`, not fabricated into a `PASS` and
not silently swallowed. The one check needing no network at all (the
kill switch) correctly reported real `PASS`.

**What this sprint still has NOT produced**: an actual real paper
from OpenAlex/arXiv/Semantic Scholar, an actual real SEC/FRED
response, an actual real Alpaca/IEX bar, or an actual real strategy-
validation verdict against real yfinance history - every one of
those still needs a machine with real outbound network access, which
this sandbox does not have (confirmed directly, not assumed - see the
`curl` evidence `docs/platform/BLOCKERS.md` items 8-11 already
document, unchanged this sprint). Nothing in this report, in any
module's docstring, or in the Mac scripts' own output claims
otherwise.

## 4. Remaining blockers

Unchanged from Sprint 4 - `docs/platform/BLOCKERS.md`'s 11 items are
still exactly what stands between "code complete, fixture-verified"
and "real-world verified" for each piece. Nothing in this sprint adds
a new blocker (no new external host was introduced) or removes one
(none of the existing ones can be resolved without a machine that has
real network access, real credentials, or a real TWS/Gateway session
running - none of which exist in this sandbox).

## 5. ONE simple Mac setup procedure for the remaining real-world validation

Everything below runs on your own Mac, where real network access
already exists. No new credential type is introduced this sprint -
only `SEC_IDENTITY`/`FRED_API_KEY` are new since Sprint 3, and both
are free/optional.

1. **Get the code.** If you haven't already:
   ```bash
   git clone <this repo's URL> ai-quant-research-bot
   cd ai-quant-research-bot
   git checkout claude/sprint5-real-world-activation
   python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
   ```
   Already have a checkout from an earlier sprint? Just double-click
   `mac_launcher/Check for Updates.command` instead (item 1 - already
   built, nothing new to learn).

2. **(Optional) add the two new free credentials to `.env`** - skip
   either line if you don't want that check to run for real yet:
   ```
   SEC_IDENTITY=Your Name your-email@example.com
   FRED_API_KEY=<free key from fred.stlouisfed.org>
   ```
   Your existing `ALPACA_*`/`IBKR_*`/`TELEGRAM_*` lines (if any) keep
   working unchanged.

3. **Double-click `mac_launcher/Run Full Activation Check.command`.**
   Answer "N" (or just press Enter) at the one prompt unless you want
   to exercise the interactive IBKR reconnect step. This single click
   runs every real check this sprint and last sprint added, and prints
   (and saves) a plain-English `PASS`/`FAIL`/`BLOCKED`/`SKIPPED` line
   per check. Read it literally - a `SKIPPED` line tells you exactly
   which `.env` value to set to turn it into real evidence next time.

4. **Double-click `mac_launcher/Agent Control Center.command`** to see
   the three new agents (Research Scholar, News Agent, Strategy Lab)
   for the first time:
   - Choose option **1** to see their status (all "never run" the
     first time - that's correct, not broken).
   - Choose options **2/3/4** to run each one once and watch it
     produce real output.
   - Only once you're happy with what you saw, choose option **5** to
     install a daily (or periodic, for fleet health) schedule for
     whichever agents you want running unattended - it asks `[y/N]`
     for each one individually; nothing is scheduled until you say yes.

5. **Open the dashboard** (`mac_launcher/Start Dashboard.command`, if
   not already running) and look at the "AI Company - Agent
   Operations" and "Research Scholar - Paper Memory" panels - they'll
   now show the real runs from step 4.

That's it - no other setup is required to see every piece of this
sprint and the last one producing genuinely real output on your own
machine. Autonomous order submission remains OFF
(`autonomous_paper.enabled`/`auto_execute.enabled` are still `false`
in `config/settings.yaml`) throughout every step above, and stays that
way until you explicitly decide otherwise.
