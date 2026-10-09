# OSS Quant Engineering Integration — Final Sprint Report

Branch: `claude/oss-quant-integration-sprint2` (built on top of
`claude/platform-integration-sprint1`). Commits `c83cdd6..526112f` (9
commits). This report is the capstone deliverable the sprint instructions
asked for: *"A comparative technical report plus working, tested
integrations... which projects were actually integrated; which were
rejected and why; test results and performance benchmarks; a real-data
versus fixture-data breakdown; a new roadmap for autonomous strategy
research; remaining blockers and operational costs; one-click deployment
improvements."*

It consolidates five detailed docs this sprint also produced, each with
its own full evidence trail — this report summarizes and cross-links
them rather than repeating them:

- `docs/platform/OSS_INTEGRATION_AUDIT.md` — the full 12-project audit
- `docs/platform/PHASE3_VECTORBT_CROSSCHECK.md` — backtester vs. VectorBT
- `docs/platform/PHASE4_LEAKAGE_DETECTION.md` — walk-forward CV leakage fix
- `docs/platform/BROKER_REFERENCE_REVIEW.md` — LumiBot/LEAN IBKR comparison
- `docs/platform/BLOCKERS.md` — environment-level blockers (updated this sprint)

## 1. Which projects were integrated, and which were rejected

| # | Project | Verdict | Installed? | Why |
|---|---|---|---|---|
| 1 | Microsoft Qlib | CONCEPT | No (`pyqlib` not a dependency) | A small, hand-written Alpha158-family factor subset was built natively instead (`src/research/alpha_factors.py`) — the full package is heavy and not needed for a handful of documented formulas |
| 2 | Microsoft RD-Agent | CONCEPT | No | The idea (AI-generated hypotheses, evaluated in a sandbox) was built natively and minimally: `src/research/sandbox.py` + `hypothesis_ledger.py`, with NO LLM calls by default and NO code path that can promote a result into production |
| 3 | **VectorBT** | **INTEGRATE** | Yes, isolated venv, optional | Cross-checks `src/backtester.py`'s own stats engine against an independent one — real, verified value (see §2) |
| 4 | LumiBot | REFERENCE only | No (GPL-3.0, copyleft risk) | Read for IBKR-connectivity architecture ideas only; found to be weaker than this project's own `ibkr_client.py` in every category compared (see `BROKER_REFERENCE_REVIEW.md`) |
| 5 | **QuantStats** | **INTEGRATE** | Yes, isolated venv, optional | Real Sharpe/Sortino/drawdown/profit-factor/transaction-cost reporting on real closed trades |
| 6 | Freqtrade/FreqAI | CONCEPT | No (GPL-3.0) | Its purged/embargoed walk-forward-CV TECHNIQUE (not code) led directly to finding and fixing a real label-leakage bug in this project's own `src/ml/splits.py` (see §3) |
| 7 | FinRL | REJECT | No | Overlaps this project's own `src/ml/` pipeline without a measurable improvement; RL-based position sizing is out of scope for this sprint |
| 8 | **CCXT** | **INTEGRATE** (code) | Yes, isolated venv, optional | Real public OHLCV data now backs `data_providers/crypto_provider.py` — no API key needed, crypto TRADING still fully disabled |
| 9 | Jesse | REJECT | No | A complete, opinionated, crypto-exchange-first framework — adopting it would mean replacing this project's own strategy/backtester stack, not augmenting it |
| 10 | QuantConnect LEAN | REFERENCE only | No (different runtime: C#) | Read for IBKR-connectivity ideas; its heartbeat-thread + bounded-reconnect pattern is real and more mature than this project's prior (nonexistent) disconnect detection — documented as a candidate for a FUTURE broker-verified milestone, not built this sprint (see §3) |
| 11 | ml4t/data | REJECT | No | Requires Python 3.12+; this project is pinned to 3.11 |
| 12 | TauricResearch/TradingAgents | Already integrated (prior sprint) | Yes | Unchanged this sprint |

**3 of 12 new projects installed** (VectorBT, QuantStats, CCXT) — the
smallest combination that cleared the audit's bar of "a real, measurable
improvement, not a duplicated capability." All three live in one shared
isolated environment (`.venvs/oss_quant/`, pinned in
`requirements-oss-quant.txt`), never imported by this project's own
Python process — every call crosses a subprocess boundary
(`tools/oss_quant_runner.py`), exactly mirroring the precedent already
established for TradingAgents.

## 2. Test results and performance benchmarks

### Full regression suite (this project + dashboard backend, same pytest run)

**1215 passed, 1 skipped, zero regressions** across the whole sprint —
re-run after every commit, not just once at the end. Dashboard frontend:
**21 passed** (vitest), plus a clean `tsc -b` and `vite build`.

New tests added this sprint, by area:

| Area | New tests |
|---|---|
| QuantStats analytics (adapter, performance report) | 14 |
| VectorBT backtester cross-check | 6 |
| Walk-forward leakage fix | 7 |
| Monte Carlo stress test | 8 |
| Market-regime breakdown | 6 |
| Research sandbox + hypothesis ledger | 18 |
| Alpha factor library | 17 |
| Crypto data provider (CCXT, rewritten) | 8 |
| IBKR disconnect-detection fix | 4 |
| Dashboard (backend endpoints) | 3 |
| **Total new** | **91** |

### VectorBT cross-check (Phase 3) — the actual benchmark

Given byte-identical trades (same entries/exits/fill prices/share counts,
zero commission/slippage), `src/backtester.py`'s own stats and VectorBT's
independently-written stats engine agree on total return and max drawdown
to floating-point rounding (observed diffs <0.0001 percentage points
across every strategy tested) and on Sharpe within a documented tolerance
once the annualization basis is corrected to match (365-day vs. 252-day
convention — a real, root-caused, fixed discrepancy, not swept under a
looser tolerance). **This validates the arithmetic, not any strategy's
profitability** — see `PHASE3_VECTORBT_CROSSCHECK.md`'s explicit
"what this does and does not establish" section.

## 3. Real findings that changed production code (not just new features)

Two genuine, evidenced defects were found and fixed this sprint — both
discovered BY doing the audit/review work the sprint asked for, not
sought out separately:

1. **Label-window leakage across walk-forward CV fold boundaries**
   (`src/ml/splits.py`). `forward_{horizon}d_return` labels near the tail
   of a training fold were built from price data reaching into that same
   fold's validation window — reproduced on a synthetic dataset before
   fixing, closed with a new `embargo_rows` parameter, verified to leave
   fold cadence/validation windows unchanged. Found by applying
   Freqtrade/FreqAI's documented purged-CV technique to this project's own
   pipeline (no Freqtrade code used). See `PHASE4_LEAKAGE_DETECTION.md`.

2. **Silent IBKR disconnect-detection gap** (`src/execution/
   ibkr_client.py`). The class's own docstring described a
   `CONNECTED -> DEGRADED -> DISCONNECTED` state machine that was never
   actually implemented — no `connectionClosed()` callback existed, so a
   real TWS/Gateway drop would leave `_state` wrongly stuck at `CONNECTED`
   forever, silently defeating `circuit_breaker.check_broker_connection()`.
   Found by comparing against LumiBot's and LEAN's real IBKR broker
   source. Fixed with the smallest correct change (a passive,
   fail-closed-only detection path) — LEAN's more elaborate heartbeat
   thread + bounded auto-reconnect loop was deliberately NOT built, since
   its correctness depends on live socket timing this sandbox cannot
   verify (documented for a future, broker-verified milestone instead).
   See `BROKER_REFERENCE_REVIEW.md`.

## 4. Real-data vs. fixture-data breakdown (full honesty accounting)

| Component | Data used | Why |
|---|---|---|
| QuantStats performance report | **Real** `decision_ledger` rows | Sources from actual recorded commission/slippage/outcomes — never synthetic once real trades exist |
| Monte Carlo stress test | **Real** closed-trade `pnl_pct` | Bootstrap-resamples real trade history; explicitly documented as testing sequencing risk within existing data, not a forward guarantee |
| Regime breakdown | **Real** `decision_ledger.regime` | No re-classification after the fact — uses the regime label already recorded at decision time |
| VectorBT cross-check (Phase 3) | **Synthetic fixture**, clearly labeled (`"data_provenance": "synthetic_fixture"`) | This sandbox's egress proxy blocks `query1.finance.yahoo.com` (`403 organization policy`, confirmed directly) — no real market data could be fetched here. Re-running against real history on a network-unrestricted machine is on the roadmap (§5) |
| CCXT crypto data | **Real exchange call attempted every time**; returns a real, honest `STATUS_ERROR` in this sandbox | Same organization-policy block (`api.binance.com`/`api.coinbase.com`, both confirmed 403). A deployment with real network access gets real `STATUS_OK` candles with no further code changes |
| Alpha factor library | **Real** seeded synthetic fixture for tests (no live feed needed — these are pure functions over OHLCV) | Factor correctness (causality, formulas) doesn't depend on which prices are fed in; tests use the same synthetic fixture as other new modules |
| Research sandbox hypotheses | Caller-supplied, honestly labeled per-call | `ResearchHypothesis.data_provenance` is the caller's own responsibility to set correctly — the sandbox cannot verify which kind of data it was handed, so it requires the label explicitly rather than guessing |

**No result anywhere in this sprint is presented as real-market evidence
when it was actually synthetic**, and no synthetic result is used to
claim anything about real profitability — per the sprint's closing
instruction: *"Do not claim the bot is profitable without credible
out-of-sample and forward-test evidence."* This sprint makes no such
claim; it validates arithmetic and infrastructure correctness only.

## 5. Roadmap for autonomous strategy research

What exists today (Phase 2 concept, built this sprint):

- `src/research/alpha_factors.py` — a small library of candidate features
- `src/research/sandbox.py` — safely backtests a `ResearchHypothesis`
  (strategy + restricted config overrides) against already-loaded data
- `src/research/hypothesis_ledger.py` — records every result, structurally
  separate from the real trading ledger, with no `promote()` path

What a genuinely autonomous research LOOP would still need (none of this
exists yet, deliberately, per "no new LLM calls by default" and "do not
permit RD-Agent or other agents to promote themselves into production
trading"):

1. **A hypothesis GENERATOR.** Today a human writes each
   `ResearchHypothesis` by hand. An automated generator (parameter sweeps
   over `alpha_factors.py`'s outputs, or an LLM-proposed variant) is a
   natural next step — but must reuse the EXISTING spend-cap/isolation
   discipline `tradingagents_spend.py` already enforces, not invent a new
   one, and must stay opt-in (disabled by default, explicit cost
   acknowledgement before any paid call).
2. **Real historical data access.** The VectorBT cross-check and CCXT
   data path are both blocked on real network access in THIS sandbox only
   (§4) — re-running them for real is a pure environment change, not a
   code change, and should happen before any hypothesis's result is
   trusted beyond "the arithmetic is sound."
3. **A promotion gate with a human in the loop.** `hypothesis_ledger.py`
   intentionally has no path from "backtested well" to "live strategy."
   The right next step is a REVIEW step (a report a human reads and acts
   on manually), not an automated promotion — consistent with this
   project's existing champion/challenger model-registry pattern
   (`src/ml/model_registry.py`), which already requires a human-triggered
   promotion for ML models and should be the template for strategy
   hypotheses too.
4. **VectorBT parameter sweeps** (the other half of Phase 3's ask,
   explicitly deferred — see `PHASE3_VECTORBT_CROSSCHECK.md`'s closing
   section): wiring a real sweep over THIS project's own strategies'
   tunable thresholds (not a generic MA-crossover demo) through the
   research sandbox.

## 6. Remaining blockers and operational costs

Full detail in `docs/platform/BLOCKERS.md` (updated this sprint). Summary:

| Blocker | Cost to resolve | Blocks |
|---|---|---|
| Real IBKR TWS/Gateway paper session | $0 (user's own Mac + free TWS Paper account) | Broker-verifying the Phase 5 `connectionClosed()` fix and everything else execution-layer against a live session |
| Licensed live/delayed market data feed | Vendor-dependent (Polygon/Tradier/IEX/Alpaca, typically $0–$200/mo tiers) | Nothing in THIS sprint — existing yfinance free/delayed feed is unaffected |
| Real network access for VectorBT cross-check / CCXT crypto data | $0 — just an environment without this sandbox's egress block | Re-running §2/§4's real-data verification |
| Forex OHLCV vendor | Vendor-dependent | `forex_provider.py` remains interface-only (unchanged this sprint — crypto, not forex, was this sprint's Phase 6 scope) |
| Crypto/forex broker (for actual TRADING, not data) | N/A — deliberately out of scope | Nothing enabled this sprint; `OrderIntent` stays equity/ETF-only by design |

**This sprint's own operational cost: $0.** No new paid API calls, no new
LLM spend (the market data budget stated at the start — "$0/month for
now" — was respected throughout; QuantStats/VectorBT/CCXT are all free,
open-source, and CCXT's public OHLCV needs no paid key).

## 7. One-click deployment improvements

- `scripts/setup_oss_quant_env.sh` — one command creates/reuses the
  isolated `.venvs/oss_quant/` environment, installs the three pinned
  packages, and verifies the install by importing each (mirrors
  `setup_tradingagents_env.sh`'s exact precedent). Documented in a new
  README section ("OPTIONAL: open-source quant engineering
  integrations") with the same "Setup - one command" structure as the
  existing TradingAgents section.
- Deliberately NOT wired into `mac_launcher/Start Dashboard.command`:
  that launcher only ever sets up the dashboard's OWN venv + npm
  install, consistent with how the (pre-existing) TradingAgents venv is
  also a separate, manual, opt-in step — heavy optional dependencies
  stay opt-in by design, not silently installed when a user just wants
  to look at the dashboard.
- Every new capability (QuantStats reports, VectorBT cross-check, CCXT
  data, the research sandbox, the alpha factor library) degrades to an
  honest "unavailable"/`None` result with zero configuration — nothing
  breaks for a user who never runs the setup script.

## Safety regression check (performed before this report)

- `execution.mode` in `config/settings.yaml` is still `DRY_RUN` (default,
  unchanged).
- `git diff <sprint-start>..HEAD -- src/execution/ src/main.py` shows
  exactly ONE file touched, `ibkr_client.py`, with exactly the
  `connectionClosed()` detection fix described in §3 — no other
  execution/order-submission code was touched this sprint.
- No autonomy/spend-cap-bypass flag was introduced anywhere
  (`git diff` searched for new `autonomous.*=.*True` / `bypass` /
  `force_live`-style patterns — none found).
- `requirements-oss-quant.txt`/`requirements-tradingagents.txt` remain
  separate from this project's own `requirements.txt` — verified no
  overlap.
- `.venvs/` remains fully gitignored — verified zero tracked files under
  it.

## Final test count

**1215 passed, 1 skipped** (main project + dashboard backend, one shared
pytest run) · **21 passed** (dashboard frontend, vitest) · clean `tsc -b`
and `vite build`. Zero regressions at every commit across the sprint.
