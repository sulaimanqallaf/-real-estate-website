# Open-Source Quant Engineering Integration — Audit

Every claim below was verified against real package metadata (PyPI JSON
API), real LICENSE files fetched from each project's GitHub repo, and -
where installable in this sandbox - real imports and real function
calls (not assumed from memory). Network access to crypto exchange
hosts is blocked by this sandbox's own egress policy (confirmed by a
`403` from the agent proxy on both `api.coinbase.com` and
`api.binance.com`, not a certificate issue) - noted explicitly wherever
that limited what could be verified live.

Verdict legend: **INTEGRATE** (adopted as a real, tested dependency) ·
**CONCEPT** (the idea is adopted, native code written, the package
itself is NOT installed) · **REFERENCE** (read for architecture lessons
only, never installed, never had code copied from it) · **REJECT** (not
used in any form, with reasons).

## 1. Microsoft Qlib — **CONCEPT**, package not installed

- PyPI name is `pyqlib` (the literal `qlib` package on PyPI is an
  unrelated abandoned placeholder, versions `0.0.2.dev*` only — verified
  by fetching both PyPI JSON records directly).
- License: MIT (verified via `raw.githubusercontent.com/microsoft/qlib`
  `LICENSE`).
- **Core (non-extra) dependencies include `mlflow`, `redis`, `pymongo`,
  `cvxpy`, `gym`, `lightgbm`, `jupyter`, `nbconvert`** — verified by
  reading `pyqlib`'s real `requires_dist` list from PyPI. This is
  server/infrastructure-oriented tooling (a message-tracking server, a
  document database, a convex-optimization solver, a Jupyter stack) for
  what our use case needs to be a batch research step.
- **Overlap with what we already have**: our `ml/` package already
  does chronological/walk-forward splitting (`splits.py`), drift
  detection (`drift.py`), calibration (`calibration.py`), and
  champion/challenger promotion (`model_registry.py`) — the exact
  capabilities Qlib's own research-workflow pitch leads with. Installing
  Qlib to get them again, with a 10x heavier dependency footprint and a
  Redis/MongoDB-shaped service architecture we have nowhere to run
  cheaply, is not a measurable improvement.
- **Decision**: do not install `pyqlib`. Instead, adopt a handful of its
  publicly-documented Alpha158-family factor formulas as small, native
  functions — see deliverable in `src/indicators_alpha.py`.

## 2. Microsoft RD-Agent — **CONCEPT**, package not installed

- PyPI name `rdagent`, MIT license (verified). Real version history
  exists (0.1.0 → 1.0.0), so it's a real, maintained project.
- RD-Agent's actual design is an LLM-driven, multi-iteration
  hypothesis → experiment → feedback loop — each iteration is itself
  one or more LLM calls, by design (it IS an "AI agent" framework, not
  a library of functions). Installing it would mean either (a) wiring
  it to a paid LLM provider and incurring real per-iteration cost with
  no cap we control, directly contradicting "$0/month budget" and
  "don't launch paid model calls without permission," or (b) installing
  dead weight that never actually runs its own core loop.
- **Decision**: do not install `rdagent`. Adopt the CONCEPT — a
  structured hypothesis → experiment → result record, evaluated against
  historical fixtures only, in an isolated sandbox with no broker
  credentials and no network access — as a new, lightweight,
  zero-additional-cost module. See `src/research/` below. Hypothesis
  TEXT can optionally be LLM-generated later, gated behind the exact
  same `tradingagents_spend.py` spend caps already enforced elsewhere in
  this codebase — not built or enabled this sprint.

## 3. VectorBT — **INTEGRATE**

- PyPI name `vectorbt`, version `1.1.1`, `requires_python: >=3.11,<3.15`
  — compatible with this project's Python 3.11.
- **License: Apache 2.0 WITH the Commons Clause** (verified by fetching
  the real `LICENSE.md` from `github.com/polakowo/vectorbt`). The
  Commons Clause only restricts SELLING the software or hosting it as a
  paid service to third parties — it does not restrict internal,
  non-commercial research use, which is the only way this project uses
  it. Documented here so the constraint is visible before anyone
  considers productizing this dashboard commercially.
- Core dependencies: numpy, pandas, scipy, matplotlib, plotly,
  numba, scikit-learn, and a few small utilities — all either already
  in our `requirements.txt` or small, permissively-licensed additions.
- **Verified by real execution in this sandbox** (not just read about):
  installed into an isolated venv, ran a real MA-crossover backtest with
  fees and slippage on synthetic price data, got real Sharpe/return/
  trade-count numbers back.
- **Decision**: integrate as an OPTIONAL, isolated-venv dependency for
  fast parameter sweeps and walk-forward validation, cross-checked
  against `backtester.py` on identical data/assumptions — never as a
  replacement for the existing backtester, which stays the system of
  record for any real promotion decision. See `tools/vectorbt_sweep.py`
  and the benchmark comparison below.

## 4. LumiBot — **REFERENCE ONLY**, not installed, no code reused

- PyPI name `lumibot`, version `4.6.11`.
- **License: GPL-3.0** (verified via PyPI classifier AND by fetching the
  real `LICENSE` text). Copyleft - importing it as a runtime dependency,
  or copying any of its code into this codebase, would create a real
  licensing obligation this project does not want. This alone is
  sufficient to reject it as a dependency regardless of technical merit.
- **51 core (non-extra) dependencies**, verified from the real
  `requires_dist` list — including `openai`, `google-adk`, `litellm`,
  `boto3`, `psycopg2-binary`, `duckdb`, `databento`, `schwab-py`,
  `py-clob-client-v2`. This is an enormous, multi-broker, multi-LLM-
  provider, multi-cloud-service dependency surface for what the user
  asked for here: "examine as reference for robust IBKR Paper
  connectivity." Installing it to read one subsystem would pull in
  unrelated attack surface, license exposure, and maintenance burden
  utterly disproportionate to the ask.
- **Decision**: read LumiBot's publicly available IBKR broker adapter
  source on GitHub (no install, no clone, no code copied — GPL-3.0
  means even partial reuse would need this codebase to go GPL-3.0 too)
  for architectural lessons only. See `docs/platform/BROKER_REFERENCE_REVIEW.md`.

## 5. QuantStats — **INTEGRATE**

- PyPI name `quantstats`, version `0.0.86`, `requires_python: >=3.10`.
- **License: Apache 2.0** (verified via PyPI classifier — the real,
  current license; older forks/mirrors have sometimes shown different
  metadata, so this was checked directly, not assumed).
- Core dependencies: matplotlib, numpy, pandas, python-dateutil, scipy,
  seaborn, tabulate, yfinance — only `seaborn` and `tabulate` are new to
  this project; both are small, mature, permissively-licensed plotting/
  formatting utilities.
- **Verified by real execution**: installed, computed real Sharpe
  (1.579), Sortino (2.503), max drawdown (-11.6%), and win rate (54.8%)
  on a synthetic return series in this sandbox.
- **Decision**: integrate as an optional dependency. See
  `src/analytics/performance_report.py`.

## 6. Freqtrade / FreqAI — **CONCEPT**, package not installed, no code reused

- PyPI name `freqtrade`, actively maintained (monthly releases through
  `2026.9` — the most recently released version as of this sprint).
- **License: GPL-3.0** (verified via the real `LICENSE` file) — same
  reasoning as LumiBot: no code reuse, no dependency installation,
  regardless of technical quality.
- It is also a full, opinionated, crypto-exchange-only trading bot with
  its own config/strategy-class system that doesn't map onto this
  project's equity/ETF-focused, IBKR-targeted architecture — installing
  it would duplicate, not extend, existing capability even before the
  license question.
- **Decision**: read Freqtrade's PUBLICLY DOCUMENTED approach to
  lookahead-bias/leakage detection (their own docs describe the
  technique: checking that a strategy's indicator values at bar N don't
  depend on data only knowable after bar N, by shifting data forward and
  comparing outputs) and implement an INDEPENDENT, original test suite
  against our own `ml/features.py`/`ml/labels.py`/`ml/splits.py`/
  `backtester.py` — no Freqtrade code read line-by-line for copying,
  only the documented CONCEPT. See `tests/test_ml_no_lookahead_leakage.py`.

## 7. FinRL — **REJECT**

- PyPI name `finrl`, MIT license (verified) — license is not the issue.
- Deep reinforcement learning for trading (`stable-baselines3`,
  `gymnasium`/`gym`, PyTorch under the hood). Rejected because:
  1. **Doesn't fit this project's validated discipline.** Every existing
     strategy here goes through chronological/walk-forward splitting
     with explicit no-look-ahead guarantees (`ml/splits.py`). RL training
     loops are notoriously prone to a DIFFERENT, harder-to-detect form of
     overfitting (reward-shaping artifacts, non-stationarity exploitation)
     that this project has no existing tooling to catch, and building
     that tooling is a separate, large effort with no evidence yet that
     it's worth it.
  2. **Compute cost.** RL training is materially heavier than the
     gradient-boosted/linear models `ml/trainer.py` already trains in
     seconds-to-minutes — disproportionate for a $0/month budget sprint
     with no GPU infrastructure.
  3. **No measurable improvement demonstrated.** Nothing about our
     current strategy set is known to be RL-shaped (none of trend-
     following/momentum/mean-reversion/breakout are naturally RL
     problems) - adopting FinRL would be technology-first, not
     problem-first.

## 8. CCXT — **INTEGRATE** (code), live network connectivity **BLOCKED** in this sandbox

- PyPI name `ccxt`, version `4.5.85` — extremely actively maintained
  (near-daily point releases).
- **License: MIT** (verified via the real `LICENSE.txt` on GitHub).
- 23 core dependencies, all HTTP/crypto-networking utilities (requests,
  aiohttp, cryptography, orjson, etc.) — no heavy ML/data-science deps.
- **Verified by real import and version check** in this sandbox.
  **Live data fetch could NOT be verified**: this sandbox's own egress
  policy returns a `403 Forbidden` on the CONNECT tunnel to BOTH
  `api.coinbase.com` and `api.binance.com` (confirmed via the agent
  proxy's own status endpoint — not a certificate problem, a deliberate
  policy block on crypto-exchange hosts). This is an environment
  limitation, not a CCXT defect - see `docs/platform/BLOCKERS.md`
  (updated) for what the user needs to verify on their own Mac.
- **Decision**: integrate `ccxt` as the real backing for
  `data_providers/crypto_provider.py` (replacing its previous always-
  `UNAVAILABLE` stub with a genuine, rate-limited, free-public-data
  implementation using a no-API-key-required exchange endpoint), tested
  with ccxt's own exchange classes mocked/monkeypatched against
  realistic fixture OHLCV responses - never against a real network call
  in CI, consistent with "use offline fixtures for automated testing."
  Crypto TRADING remains fully disabled; this only ever returns data.

## 9. Jesse — **REJECT**

- PyPI name `jesse`, version `3.2.4`. **License: MIT** (verified - this
  corrects an initial assumption of a more restrictive license; the
  real `LICENSE` file on GitHub is plain MIT).
- Rejected anyway: Jesse is a complete, opinionated, crypto-exchange-
  only trading framework with its OWN strategy-class API, backtester,
  and live-trading engine - a direct architectural competitor to, not
  an extension of, this project's existing equity/ETF + IBKR-focused
  execution stack. Adopting it would mean running two parallel trading
  systems side by side for no capability this project doesn't already
  have (deterministic strategies, backtesting, paper execution) in a
  shape that already fits our IBKR/Telegram/safety-breaker architecture.
  Crypto trading itself also stays disabled per this sprint's explicit
  scope, removing Jesse's one genuinely distinct value-add.

## 10. QuantConnect LEAN — **REFERENCE ONLY**, not installed

- **License: Apache 2.0** (verified via the real `LICENSE` file) — no
  licensing blocker, unlike LumiBot/Freqtrade.
- Rejected as an installable dependency purely on fit: LEAN is a
  C#/.NET engine (the Python "Lean CLI" is a thin wrapper that still
  needs a local Docker-based LEAN engine container, or QuantConnect's
  cloud, to actually run anything) - installing the real engine here
  would mean a .NET runtime + Docker + an entirely separate data-
  subscription model, for the single, narrow purpose the user asked
  for: "examine as reference for robust IBKR connectivity."
- **Decision**: read LEAN's public, Apache-2.0-licensed
  `Brokerages/InteractiveBrokers` source on GitHub for IBKR reconnect/
  order-state lessons (permitted to read and even reference small
  patterns under Apache 2.0, though none was copied verbatim - this
  project's order state machine is Python, not C#, so there is nothing
  to directly port). See `docs/platform/BROKER_REFERENCE_REVIEW.md`.

## 11. ml4t/data (`ml4t-data` on PyPI) — **REJECT**

- Real, currently-maintained project (confirmed via its own README on
  `raw.githubusercontent.com`), MIT license, version `0.2.0`.
- **`requires_python: >=3.12,<3.15`** — this sandbox (and this
  project's existing tooling) runs **Python 3.11**. `ml4t-data` is
  simply not installable without upgrading the whole project's Python
  version first, which is a separate, much bigger decision this sprint
  doesn't make unilaterally.
- Even setting the version aside: its dependency set (`polars`,
  `pydantic-settings`, `structlog`, `tenacity`) introduces three new
  architectural patterns (a second dataframe library alongside pandas, a
  pydantic validation layer, a structured-logging framework) that
  diverge from this codebase's existing, consistent conventions
  (pandas everywhere, stdlib `logging`, plain dict-based config) for a
  very young (`0.2.0`) package with no track record yet.
- **Decision**: reject. Our existing `data_providers/` + `data_collector.py`
  + this sprint's own `universe.py` already cover the same conceptual
  ground (provenance, freshness, multi-asset-class provider interface)
  in an already-integrated, already-tested way.

## 12. TauricResearch/TradingAgents — **ALREADY INTEGRATED** (prior work)

Confirmed still in place and unaffected by this sprint:
`src/intelligence/tradingagents_adapter.py` (real upstream package,
isolated subprocess + venv, spend-capped, shadow-mode only, cached). No
further action needed here.

## Summary table

| # | Project | Verdict | Installed as dependency? |
|---|---|---|---|
| 1 | Qlib | CONCEPT | No |
| 2 | RD-Agent | CONCEPT | No |
| 3 | VectorBT | **INTEGRATE** | Yes (isolated venv, optional) |
| 4 | LumiBot | REFERENCE | No (GPL-3.0 + 51 deps) |
| 5 | QuantStats | **INTEGRATE** | Yes (isolated venv, optional) |
| 6 | Freqtrade/FreqAI | CONCEPT | No (GPL-3.0) |
| 7 | FinRL | REJECT | No |
| 8 | CCXT | **INTEGRATE** (code) | Yes (isolated venv, optional) — live network unverified in sandbox |
| 9 | Jesse | REJECT | No |
| 10 | LEAN | REFERENCE | No |
| 11 | ml4t/data | REJECT | No (Python 3.12+ required) |
| 12 | TradingAgents | Already done | Yes (prior sprint) |

**3 of 12 projects installed** (the smallest combination the audit
found to offer measurable, license-clean, dependency-light improvement),
**3 adopted as concepts only** (native code, zero new runtime
dependency, zero new cost), **2 used as read-only architecture
references**, **3 rejected outright** with documented reasons, **1
already integrated** in earlier work.
