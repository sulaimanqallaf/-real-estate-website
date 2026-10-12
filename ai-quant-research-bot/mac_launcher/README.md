# Dashboard Launcher (no-Terminal-copy-pasting UX)

Seven double-clickable scripts, meant to replace ever having to copy a
Terminal command out of a chat again for ordinary day-to-day dashboard
and agent-fleet use:

| File | What it does |
|---|---|
| `Start Dashboard.command` | Checks dependencies, installs/updates what's needed, starts the dashboard backend + frontend, opens your browser to it. |
| `Stop Dashboard.command` | Stops both, and only both - nothing else on your Mac. |
| `Dashboard Status.command` | Shows whether the dashboard is running, whether it's actually reachable, (read-only) whether the separate Mac launchd research scheduler is loaded, and - Sprint 3 - a full reliability/freshness summary (position_monitor heartbeat, circuit breaker, cached-data staleness for both the yfinance and Alpaca/IEX caches) via `python -m src.execution.run_health`, so you never have to open Terminal yourself just to check this. |
| `Run Real Validation.command` | Sprint 4, Priority 1: one guided run of `src.validation_suite` - real-market strategy validation, an Alpaca Basic/IEX data check, and a read-only IBKR Paper connectivity preflight - reported as PASS/FAIL/SKIPPED/BLOCKED with real evidence. Never asks for a credential (those come only from your own `.env`); never places an order; never touches the scheduler. |
| `Run Full Activation Check.command` | Sprint 5: everything `Run Real Validation.command` does, PLUS a real OpenAlex/arXiv/Semantic Scholar smoke test (recording one real sample paper into permanent research memory if any provider returns one), a real SEC ticker->CIK + FRED check, and one real Telegram test message if already configured - still only real PASS/FAIL/BLOCKED/SKIPPED, never a fabricated success. |
| `Agent Control Center.command` | Sprint 5: a menu for the autonomous research agent fleet (Research Scholar, News Agent, Strategy Lab, Agent Fleet Health) - show status, run any agent once right now, install/uninstall its launchd schedule (always asks `[y/N]` per job, never automatic), tail a log, or roll back to the last `Check for Updates.command` backup point. |
| `Check for Updates.command` | Checks GitHub for new commits on your current branch; only applies them if you explicitly type `y`, after creating a rollback tag. Already covers Sprint 5's "safely prepare and update an isolated local copy" ask in full - see "What it never does" below. |

## Honest limitations - read this before relying on it

**This is NOT a signed, notarized native macOS app.** It's a set of
plain bash scripts with a `.command` extension, which macOS lets you
double-click to run in a new Terminal window - that Terminal window
IS the "UI": installation progress, errors, and status all print there
as plain text, not in a custom graphical window. No Apple Developer
signing identity is available in the environment that built this (see
`docs/platform/BLOCKERS.md` item 5) - a fully signed/notarized `.app`
is the natural next step once you decide that's worth the $99/year
Apple Developer Program cost, which is your call to make, not
something built on spec here.

**First launch will likely show a Gatekeeper warning** ("cannot be
opened because it is from an unidentified developer"). This is expected
for any unsigned script/app, not a sign something is wrong. One-time
fix: right-click (or Control-click) the `.command` file → **Open** →
confirm **Open** in the dialog that appears. After that first time,
double-clicking works normally. (If macOS instead says the file "is
damaged", run once in Terminal: `xattr -d com.apple.quarantine
"Start Dashboard.command"` from inside this folder - this clears the
same quarantine flag a signed app's notarization would normally clear
for you automatically.)

**"Check for GitHub releases"**, literally, isn't possible yet - this
project doesn't currently publish formal GitHub Releases/tags for the
bot. `Check for Updates.command` checks your current branch against
its GitHub remote for new commits instead, which is the closest honest
equivalent today. If this project adopts real releases later, this
script is the natural place to point at the releases API instead.

## What it never does (safety, by construction - not just by promise)

- Never starts, stops, loads, or unloads the Mac launchd research
  scheduler (`com.aiquantresearchbot.*`) - `Dashboard Status.command`
  only ever READS its state via `launchctl list`, the same read-only
  check `python -m src.execution.run_health` already does.
- Never sets `DASHBOARD_ALLOW_DEMO=1` itself - DEMO mode (synthetic
  data) stays opt-in; set that variable yourself first if you want it.
- Never runs `src.main`, never calls an LLM provider, never places an
  order - it only ever starts the read-only dashboard backend/frontend.
- Never touches `.env`, `config/settings.yaml`, or anything under
  `data/` (except `Run Real Validation.command`, which writes ONLY a
  dated report file under `data/reports/` - never `.env` or
  `config/settings.yaml`) - dependency installs go into
  `dashboard/backend/.venv/` and `dashboard/frontend/node_modules/`
  only, both already gitignored.
- `Run Real Validation.command` never prompts for, displays, or logs a
  credential - `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`/`IBKR_*`
  values are read only from your own `.env`, never typed into this
  script's prompt, and every line it prints or saves is passed through
  `utils.redact_secrets()` first. It never places, cancels, or modifies
  an order, and never changes `autonomous_paper.*`/`execution.mode`.
- `Check for Updates.command` never discards uncommitted work (stashes
  it first, recoverable with `git stash pop`) and never pulls without
  you typing `y` first; it always creates a timestamped git tag right
  before pulling, so rollback is always exactly one `git reset --hard
  <that tag>` away (the exact command is printed for you at the time).
- `Run Full Activation Check.command` has the exact same security
  posture as `Run Real Validation.command` above, for every credential
  type it touches (`SEC_IDENTITY`/`FRED_API_KEY`/`TELEGRAM_*` added
  this sprint, same as `ALPACA_*`/`IBKR_*` already covered) - `.env`
  only, never typed into this script, every printed/saved line
  redacted. The one real write anywhere in this script is a single
  sample-paper row in `data/research/paper_memory.db`; the one real
  network side effect is a single Telegram test message, sent only if
  you already have `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` configured.
- `Agent Control Center.command` can only install/uninstall a launchd
  job for one of the four NEW agents this project introduced (Research
  Scholar, News Agent, Strategy Lab, Agent Fleet Health) -
  `src/agents/launchd_installer.py`'s `RESERVED_EXISTING_LABELS` makes
  it structurally impossible for this script to install, uninstall, or
  even query any of the 7 pre-existing `com.aiquantresearchbot.*` jobs,
  and every install/uninstall still requires its own separate `[y/N]`
  confirmation per job. Its rollback option refuses outright if your
  working tree has uncommitted changes, rather than silently
  discarding them.

## Verified, not just written

Every one of the four scripts above was actually run - repeatedly - in
the environment that built this (a Linux sandbox, not a Mac, so the
`open` browser step and the macOS-only Gatekeeper/`.command` double-
click behavior itself could NOT be verified from there - that part
genuinely needs your Mac to confirm). What WAS verified on real
processes, across multiple full cycles:
- Dependency install (Python venv + pip, npm install) completes and
  the backend/frontend actually start and become reachable over HTTP.
- Running `Start` again while already running correctly detects and
  skips, rather than starting a second copy.
- `Stop` correctly terminates the ENTIRE process tree - an earlier
  version of this script only killed the top-level tracked PID and
  left `npm run dev`'s actual `vite` child process running in the
  background; this was caught by explicitly checking for leftover
  processes after `Stop` ran, and fixed with a recursive process-tree
  kill (`kill_tree()` in `Stop Dashboard.command`) before being
  considered done. Confirmed clean (zero stray processes) across
  repeated start/stop cycles after the fix.
- `Check for Updates.command` correctly fetches the real GitHub remote
  and reports "already up to date" or lists real pending commits.
- `Dashboard Status.command`'s new Sprint 3 reliability section was run
  for real against the backend's actual Python venv (after installing
  this sprint's new `requirements.txt` entries into it) and correctly
  printed the full `run_health` report, including the new Alpaca/IEX
  cache-freshness line, with no errors.
- `Run Real Validation.command` was run for real (non-interactively,
  answering "no" to the reconnect-check prompt) in the environment that
  built this - no real market/broker network access there, so every
  check correctly reported `BLOCKED` (yfinance/IBKR unreachable) or
  `SKIPPED` (no Alpaca credentials set) rather than crashing or
  fabricating a `PASS`; the one check needing no network at all (the
  kill switch) correctly reported real `PASS`. On your Mac, with real
  network access, the strategy-validation and Alpaca sections should
  turn into real `PASS`/`FAIL` evidence instead of `BLOCKED`/`SKIPPED`.
- `Run Full Activation Check.command` was likewise run for real
  (non-interactively) in the sandbox that built it, with a temporary
  `.venv` symlink pointing at the system interpreter (never committed -
  `.venv/` is gitignored). Every network-touching check correctly
  reported `BLOCKED` with a REAL caught exception (a `ProxyError`/
  `ConnectionError` from this sandbox's own egress policy, not a
  simulated one) - OpenAlex, arXiv, and Semantic Scholar all three -
  confirming Sprint 5's new BLOCKED-vs-FAIL classification
  (`data_providers.base.provider_blocked()`) behaves correctly against
  a genuine failure, not just a mocked one in a unit test. SEC/FRED/
  Telegram correctly reported `SKIPPED` (not configured); the kill
  switch again correctly reported real `PASS`. Summary line that run
  actually printed: `1 PASS, 0 FAIL, 5 SKIPPED, 7 BLOCKED`.
- `Agent Control Center.command` was run for real (non-interactively):
  option 1 printed a genuine fleet report (every agent correctly
  "never run," budgets at 0/N) via the real `src.agents.health_report`
  module; option 5 printed the real `launchd_installer.py list`
  output (`unknown (not macOS...)` for every job - correct off a Linux
  sandbox) and correctly took no action on a declined `[y/N]` prompt.

## Uninstalling

Delete the `mac_launcher/` folder, and optionally
`dashboard/backend/.venv/` and `dashboard/frontend/node_modules/`
(both regenerable by running `Start Dashboard.command` again, or by
hand per `dashboard/README.md`). None of this touches anything outside
the `ai-quant-research-bot/` repository.
