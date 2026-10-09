# Platform Sprint Changelog

Append-only, newest first. One entry per commit on
`claude/platform-integration-sprint1`, in the same spirit as the rest of this
codebase's commit messages: what changed and why, not just what.

## [Unreleased]

- `38203b1` **Mac launcher (deliverable I)** - 4 double-clickable `.command`
  scripts (Start/Stop/Status/Check-for-Updates) under `mac_launcher/`.
  Process-management logic was actually run repeatedly in this sandbox,
  which caught and fixed a real bug: `Stop` originally only killed the
  tracked top-level PID, leaving `npm run dev`'s actual `vite` child
  process running; fixed with a recursive process-tree kill. No Apple
  signing identity available - honest unsigned `.command` alternative,
  documented as such.
- `9fda090` Progress-checklist correction: containerization was build-
  AND-run verified locally, not merely written.
- `7d57497` **Containerization (deliverable H)** - Dockerfiles for the
  dashboard backend (Python runtime only; code/data bind-mounted
  read-only at runtime) and frontend (Node build -> nginx serve), plus
  `deploy/docker-compose.yml`. Build- and run-verified locally: started
  a real Docker daemon, built both images, ran both containers, and
  confirmed a deliberate write attempt against the read-only mount
  failed with a real OS-level error. Not deployed anywhere.
- `d642c73` **5 missing command-center panels (deliverable G)** -
  positions/orders/fills, risk/drawdown, strategy backtest performance,
  learning/challenger experiments, market scanner. `positions_and_
  orders()` deliberately avoids importing `order_manager` at all (a
  local JSONL reader instead), sidestepping that class's side-effecting
  constructor so the dashboard's "never writes anything" claim stays
  airtight.
- `82eaaec` **Dashboard visual upgrade (deliverable F)** - procedural
  walking humanoid characters (real leg/arm swing via `walkCycle.ts`,
  unit-tested) replacing flat colored circles; drawn office backdrop
  (desks/monitors/debate table/trading-floor strip); event-driven
  speech bubbles. `eventMapping.ts`'s event-type -> movement logic is
  completely unchanged.
- `9b59dfc` **IBKR execution safety audit (deliverable D)** - full audit
  of every item in the user's required safety-check list against the
  real `execution/` code; all were already real and complete from
  earlier phases. Added `unattended_readiness.py`, a read-only pre-
  flight summary tool (adds no new enforcement, just visibility) -
  caught and fixed its own test's bug mid-development (a stray
  `data/runtime/trading_halt.json` written into the real repo tree by
  a test that forgot to scope `halt_state_file` to `tmp_path`; deleted
  before commit).
- `97848da` **RAG knowledge library (deliverable E)** - offline,
  pure-Python BM25 keyword retrieval over user-supplied legal documents
  (`data/knowledge/`, gitignored). No embeddings, no LLM calls, no new
  dependency. Every result carries an exact citation; retrieved text is
  always wrapped in an explicit `<retrieved_document trust="untrusted">`
  block, proven by test to survive even when the source text is itself
  shaped like an instruction.
- `97900f4` **Forex/crypto provider stubs (deliverable A)** - proves the
  existing `ProviderResult`/`DataProvider` contract extends to non-
  equity asset classes without claiming a real feed exists; structurally
  incapable of returning `STATUS_OK`, tested including the case where an
  API key IS set but no real vendor call exists yet.
- `9363ee4` **Configurable, screened symbol universe (deliverable A)** -
  `src/universe.py`, expandable from 15 toward 100+ symbols via a new,
  OPT-IN `config.universe` section (defaults to `false` - today's
  15-symbol deployment's behavior is byte-for-byte unchanged until
  explicitly opted into). Price/liquidity screening over already-cached
  bars; a delisted-symbol safeguard (N consecutive fetch failures ->
  excluded until a human intervenes).
- `b1d6cc5` **Persistent engineering plan (this sprint's first commit)**
  - `docs/platform/{ARCHITECTURE,PROGRESS_CHECKLIST,ROADMAP,BLOCKERS}.md`.
  Direct-audit finding: deliverables B (strategy lab), C (continuous
  learning engine), and D (IBKR paper execution)'s deterministic cores
  were already ~85-90% built in earlier phases of this project - this
  sprint's job on those was audit + gap-fill, not a rebuild.
