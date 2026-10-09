# Platform Roadmap

## This sprint (`claude/platform-integration-sprint1`)

Sequenced by dependency and leverage, executed in order, each step committed
and tested independently (see `CHANGELOG.md` for the actual commit history):

1. Planning docs (this directory) — done first so the rest of the sprint has
   a written contract to execute against and the user has something durable
   even if the sprint is interrupted.
2. Market data universe + screening (A) — foundational; later milestones
   (scanner panel) read from it.
3. Forex/crypto provider stubs (A) — small, isolated, proves extensibility.
4. IBKR execution safety audit + readiness doc (D) — highest real-world
   stakes; verify before building more on top of it.
5. RAG knowledge library (E) — isolated, no dependency on the above.
6. Dashboard visual upgrade (F) — depends on nothing above; can reuse the
   existing event schema unchanged.
7. Command-center missing panels (G) — depends on (2) for the scanner panel
   and reads the existing ledgers for the rest.
8. Containerization prep (H) — depends on the dashboard being feature-
   complete enough to containerize meaningfully.
9. Mac launcher (I) — last, since it launches the services built above.
10. Full regression + consolidated report.

## Near-term (next sprint candidates, not started)

- Performance attribution report (strategy × regime × ticker breakdown).
- Corporate-action auto-adjustment pipeline beyond yfinance's own handling.
- A dedicated REPLAY date-range/speed control in the dashboard UI (currently
  only reachable via the WebSocket URL's query params).
- Containerizing the bot's own scheduled jobs (not just the dashboard).

## Path toward restricted autonomous IBKR Paper trading

Today, `autonomous_paper.enabled`/`auto_execute.enabled` default to `false`
and `execution.mode` defaults to `DRY_RUN` — nothing in this sprint changes
either default. The documented, reviewable path to turning PAPER autonomy on
for real (still paper money, never live) is:

1. Run `python -m src.execution.circuit_breaker status` and the health
   command (`python -m src.execution.run_health`) daily for at least 2-4
   weeks with `execution.mode: IBKR_PAPER` and autonomy still OFF, watching
   the decision ledger accumulate real classified candidates with no orders
   placed — this validates the research/classification pipeline against
   real market days before any order is ever submitted.
2. Flip `autonomous_paper.enabled: true` with `auto_execute.enabled` still
   `false` — every AUTO_EXECUTE-classified candidate goes to Telegram for a
   manual tap instead of auto-submitting. Run this for another few weeks,
   comparing what you WOULD have approved against what you DID approve.
2. Only once that manual-approval track record looks right to you, flip
   `auto_execute.enabled: true` for real PAPER auto-submission — review the
   circuit breaker's limits (`config.execution.risk`) against your own risk
   tolerance first; they are conservative defaults, not tuned to your
   preferences.
3. Keep the kill switch (`circuit_breaker halt`) and Telegram `/halt` command
   known and tested throughout — this sprint's `UNATTENDED_PAPER_READINESS.md`
   has the full checklist.

None of this requires further coding — it's a configuration + observation
process the user runs. The code support for every step above already exists.

## Roadmap toward the (separately authorized) $500 real-money pilot

**Explicitly not started, and nothing in this sprint moves toward it code-
wise** — there is no LIVE execution mode in this codebase, by design
(`utils.VALID_EXECUTION_MODES`). If/when the user authorizes exploring this:

1. A real-money pilot needs its OWN new execution mode, added deliberately
   and reviewed line-by-line — not a flag flip on the PAPER path. This is
   explicitly flagged in the user's own instructions as requiring separate
   authorization, and nothing here should be read as scaffolding for it.
2. Before any such review could even start, the PAPER track record above
   (restricted autonomy, running cleanly for weeks, every breaker tested for
   real) is the prerequisite evidence — there is no responsible way to skip
   from "paper trading" to "$500 real money" without it.
3. A real-money pilot would also need: a funded brokerage account
   (separate from the paper account), real position-sizing math re-derived
   for the smaller real account size (not just reusing paper's sizing),
   and a tax/recordkeeping plan (outside this codebase's scope entirely).
4. Given (1)-(3), the honest estimate is that this is MONTHS of paper track
   record away, not a next-sprint item — and the decision to proceed is the
   user's alone, not something to be nudged toward by having "mostly built"
   the live path in advance.
