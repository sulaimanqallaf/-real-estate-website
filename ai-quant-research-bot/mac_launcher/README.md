# Dashboard Launcher (no-Terminal-copy-pasting UX)

Four double-clickable scripts, meant to replace ever having to copy a
Terminal command out of a chat again for ordinary day-to-day dashboard
use:

| File | What it does |
|---|---|
| `Start Dashboard.command` | Checks dependencies, installs/updates what's needed, starts the dashboard backend + frontend, opens your browser to it. |
| `Stop Dashboard.command` | Stops both, and only both - nothing else on your Mac. |
| `Dashboard Status.command` | Shows whether the dashboard is running, whether it's actually reachable, and (read-only) whether the separate Mac launchd research scheduler is loaded. |
| `Check for Updates.command` | Checks GitHub for new commits on your current branch; only applies them if you explicitly type `y`, after creating a rollback tag. |

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
  `data/` - dependency installs go into `dashboard/backend/.venv/` and
  `dashboard/frontend/node_modules/` only, both already gitignored.
- `Check for Updates.command` never discards uncommitted work (stashes
  it first, recoverable with `git stash pop`) and never pulls without
  you typing `y` first; it always creates a timestamped git tag right
  before pulling, so rollback is always exactly one `git reset --hard
  <that tag>` away (the exact command is printed for you at the time).

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

## Uninstalling

Delete the `mac_launcher/` folder, and optionally
`dashboard/backend/.venv/` and `dashboard/frontend/node_modules/`
(both regenerable by running `Start Dashboard.command` again, or by
hand per `dashboard/README.md`). None of this touches anything outside
the `ai-quant-research-bot/` repository.
