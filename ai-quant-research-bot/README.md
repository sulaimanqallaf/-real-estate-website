# AI Quant Trading Research Bot (v1)

A Mac-friendly Python research tool that:

1. Downloads daily price history for a configurable ticker list (via `yfinance`).
2. Runs three price-based strategies (mean reversion, momentum breakout, trend
   following) plus a best-effort options-skew "big money" positioning layer.
3. Scores every ticker 0-100, ranks it, and labels it Strong candidate / Watchlist /
   Weak watchlist / Avoid.
4. Estimates entry/target/stop/risk-reward/position size for whatever setups clear
   every risk rule.
5. Sends a daily Telegram report and saves CSV/JSON reports plus a trade journal.
6. Sends one Approve Paper Trade / Reject / Watch Only button set per Top
   Candidate; approvals are recorded in `data/journal/paper_trades.csv`.
7. Includes a standalone backtester (1+ year, per strategy, vs. buy-and-hold).

**This version does not place trades, connect to a broker, use margin, trade
options, or short anything.** It only collects data, analyzes it, scores it, sends
an alert, and - if you tap Approve - records a PAPER trade in a CSV. Nothing here
executes a real order, on paper trades or anything else.

---

## Read this before you trust the output

A few things worth knowing up front, not buried in the code:

- **Options skew data is best-effort and often unavailable.** Yahoo Finance's free
  `impliedVolatility` field is frequently 0.0 or stale on thin strikes. When usable
  IV can't be found, the report shows `Data Unavailable` for that ticker's skew
  rather than guessing. Skew is explicitly a watchlist layer, never a trade gate,
  per the spec.
- **Every ticker is assigned to at least one strategy.** SPY/QQQ run mean reversion
  + trend following; VOO/VGT/SMH run trend following + momentum breakout;
  AAPL/MSFT/NVDA/AMD/META/TSLA/GOOGL/AMZN run momentum breakout + trend following;
  GLD/USO run trend following only. Every ticker CAN generate a trade candidate -
  whether it actually does on a given day still depends on that day's price action
  and the risk rules below. A ticker not listed under a given strategy still never
  triggers *that* strategy's candidate, and still gets scored on the universal
  indicator criteria and a skew classification regardless.
- **A ticker's 0-100 label and its "Top Candidates" trade plan are deliberately
  decoupled, but gated together.** A valid Mean Reversion setup requires price to be
  temporarily *below* its 20D MA - which by construction drags down the momentum/
  trend portion of the score. So a real, risk-manager-approved dip-buy candidate can
  coexist with a mediocre overall score. To avoid printing a contradictory "Signal:
  Avoid" directly above a detailed buy plan, **Top Candidates requires all of**: the
  risk manager approved the trade, the label is not Avoid, risk/reward is at least
  1.5, RSI is strictly below 75, and price is above SMA 200 - except for Mean
  Reversion candidates specifically, which are exempt from the SMA200 check (see
  below). A tradeable setup on an Avoid-labeled ticker still shows up in the saved
  CSV/JSON (for your own review) but is left out of the Telegram report and the
  trade journal - this applies to Aggressive mean-reversion setups too, so a deep,
  ugly-looking dip can clear every risk rule and *still* never reach Top Candidates
  if its overall score lands on Avoid.
- **Mean Reversion runs in two modes: Safe (default, always eligible) and
  Aggressive (opt-in, never eligible unless you explicitly turn it on).** Both run
  on the same tickers (SPY/QQQ) and are always computed and reported every day -
  the difference is what's allowed to become a Top Candidate / trade journal entry:

  | | `require_trend_intact` | Dip depth required | Eligible for Top Candidates/journal? |
  |---|---|---|---|
  | Safe | `true` (default, leave it) | 1.5 std devs below 20D MA | Yes, same as any other strategy |
  | Aggressive | `false` (no SMA200 gate) | 2.0 std devs below 20D MA | **Only if** `aggressive_mode.enabled: true` |

  With `aggressive_mode.enabled: false` (the default - **leave it this way unless
  you've decided otherwise**), Aggressive setups still show up every day in a
  separate **"High Risk Dip Watchlist"** report section - entry/stop/target/R:R and
  whether it *would* have passed the risk rules, purely informational - but they are
  hard-blocked from Top Candidates and the trade journal no matter how good they
  look. That block is enforced twice, independently: once in `main.py` (Aggressive
  candidates never enter the pool risk_manager picks from unless enabled) and again
  in `report_writer.select_top_candidates()` (the single choke point both the
  Telegram report and the journal draw from, which re-checks the flag itself). Flip
  `enabled: true` only once you've decided you want aggressive dip-buys to be
  tradeable - at that point an Aggressive candidate competes on equal footing with
  every other strategy's candidate and must still clear every other risk rule.

  **Enabled aggressive mode means eligible, not automatically tradable. A candidate
  can still be blocked by the overall signal score label.** Turning
  `aggressive_mode.enabled` on does not promote anything into Top Candidates by
  itself - it only lets an Aggressive candidate be *considered* alongside every
  other strategy's candidate. `select_top_candidates()` still requires all four of:
  (1) the risk manager approved the trade, (2) `aggressive_mode.enabled` is true if
  the candidate is Aggressive, (3) the ticker's overall signal label is not Avoid,
  and (4) the risk/reward threshold passes (folded into (1), since the risk manager
  itself rejects anything below `min_risk_reward_ratio`). A ticker crashing hard
  enough to trigger Aggressive mean reversion very often lands on an Avoid label on
  the universal 0-100 checklist (see the decoupled-but-gated-together bullet above)
  - enabling the flag does not change that; it can still be right there in the High
  Risk Dip Watchlist and nowhere near Top Candidates.
- **The paper-trade approval flow is two separate processes, not one.**
  `python -m src.main` is a once-a-day batch job: it sends the report AND one
  Approve/Reject/Watch Only message per Top Candidate, then exits. Nobody is
  listening for the button press yet at that point - a *separate*, continuously-
  running process, `python -m src.approval_listener`, has to be running (in its
  own terminal, or as a background launchd agent - see below) for a tap on any
  button to actually do anything. If that listener isn't running, the buttons
  just sit there inert. High Risk Dip Watchlist entries never get buttons at all,
  under any config - they're informational only, always, with no exceptions.
  Approving only ever means: append one row to `data/journal/paper_trades.csv`.
  It never places, modifies, or connects to anything resembling a real order, and
  only the chat matching your configured `TELEGRAM_CHAT_ID` is allowed to approve
  anything - a callback from any other chat is logged and ignored.
- **An Aggressive candidate's eligibility is re-checked at the moment you tap
  Approve, not just when the message was sent.** If you flip
  `aggressive_mode.enabled` back to `false` between the daily send and whenever
  you get around to checking your phone, tapping Approve on an Aggressive
  candidate will be refused right then - it does not fall back to whatever the
  flag was when the message went out.
- **Only approving needs the listener - closing an open paper trade doesn't.**
  `python -m src.approval_listener` must be running for a button tap to register
  at all. But once a trade is `OPEN`, moving it to `TARGET_HIT`/`STOPPED`/
  `TIME_EXIT` and sending the exit notification happens inside
  `python -m src.main` itself, every day it runs - no listener involvement. The
  listener's only job is turning a button tap into that first `OPEN` row.
- **The paper trade lifecycle still executes nothing.** Checking stop/target/time
  exits reads daily bars that were already fetched for the day's analysis and
  updates a CSV row - there is no broker connection anywhere in this codebase, no
  IBKR, no `ib_insync`, no order of any kind. See "Paper Trade Lifecycle" below
  for the full mechanism and the roadmap note on what (eventually) comes after it.
- **The backtester is a research approximation, not a portfolio simulator.** Each
  strategy gets its own independent capital pool; there's no shared-margin or
  cross-strategy position limit modeling. Entries fill at the next bar's open after
  a signal closes (no lookahead); a stop and target hitting the same bar assumes the
  stop wins (conservative). See `src/backtester.py`'s module docstring for the full
  list of simplifications. It only backtests Safe Mean Reversion - Aggressive mode
  isn't run through the backtester in Version 1.

---

## 1. Install on Mac

Requirements: Python 3.10+ (`python3 --version`; install via
[python.org](https://www.python.org/downloads/) or `brew install python`).

```bash
cd ai-quant-research-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 2. Create the Telegram bot

1. Message **@BotFather** on Telegram, send `/newbot`, and follow the prompts.
2. BotFather gives you a token like `123456789:AAExampleTokenHere` - this is
   `TELEGRAM_BOT_TOKEN`.
3. Message your new bot anything first, then open (replacing `<TOKEN>`):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Find `"chat":{"id": ...}` - that number is `TELEGRAM_CHAT_ID`. For a group, add
   the bot to the group, message it there, and read that chat's (negative) id the
   same way.

---

## 3. Configure `.env`

```bash
cp .env.example .env
```

Fill in the two Telegram values from step 2. Everything else (tickers, strategy
parameters, scoring weights, risk rules, account equity, backtest settings) lives in
`config/settings.yaml` - edit it directly. `risk.account_equity` and
`risk.risk_pct_per_trade` are not secrets, just defaults; set them to your real
numbers so position sizing means something.

---

## 4. Run manually

```bash
source .venv/bin/activate   # if not already active
python -m src.main
```

Each run:

- Fetches daily history for every ticker in `config/settings.yaml`; any symbol that
  fails to download is logged and skipped without crashing the run.
- **Checks every OPEN paper trade against that freshly fetched history** and
  closes anything that hit its stop, target, or max holding period - see "Paper
  Trade Lifecycle" below. This runs before today's new candidates are generated,
  using the same price data, and is gated on `paper_trading.enabled` like
  everything else paper-trading-related.
- Sends one Telegram notification per paper trade that closed *this run* (never
  a repeat for one closed earlier), before the daily report.
- Fetches a best-effort options chain per ticker for skew (also isolated - an
  options failure never blocks the price/indicator/strategy pipeline).
- Computes indicators, runs the three strategies against their assigned ticker
  universes, classifies skew, scores and ranks everything.
- Saves `data/reports/report_<date>.csv` / `.json`, appends
  `data/journal/trade_journal.csv`, and logs to `data/reports/app.log`.
- Sends the Telegram report (splitting into multiple messages if long), now
  including a "Paper Trading Performance" section. Set `telegram.enabled: false`
  in the config to skip sending while still generating reports.
- Sends one additional Approve Paper Trade / Reject / Watch Only message per Top
  Candidate and saves a pending-approval record for each in
  `data/journal/pending_approvals.json`. Set `paper_trading.enabled: false` to
  skip this (and the lifecycle check above) and only send the plain report.
  Nothing happens with a button press until `python -m src.approval_listener`
  (next section) is actually running.

Run the test suite any time with:

```bash
python -m pytest tests/ -v
```

### Backtesting

```bash
python -m src.backtester
```

This fetches history for every ticker used by any of the three strategies plus the
benchmark tickers, simulates each strategy independently over the configured
`backtest.lookback_period` (default 1 year), and prints/saves total return, win
rate, average win/loss, profit factor, max drawdown, and Sharpe ratio per strategy,
a blended "Combined" block, and SPY/QQQ buy-and-hold comparisons. Output goes to
`data/reports/backtest_<date>.csv` (summary) and
`data/reports/backtest_trades_<strategy>_<date>.csv` (every simulated trade).

---

## 5. Run the paper-trade approval listener

This is a **separate, continuously-running process** from `python -m src.main` -
it's what actually processes the Approve/Reject/Watch Only button presses on
whatever device you're reading Telegram on. Run it in its own terminal tab (or
tmux/screen session, or as a launchd agent - see below):

```bash
source .venv/bin/activate   # if not already active
python -m src.approval_listener
```

It long-polls Telegram for button presses, resolves each one against
`data/journal/pending_approvals.json`, and on Approve appends a row to
`data/journal/paper_trades.csv`. It logs to `data/reports/approval_listener.log`
and persists its Telegram update offset to
`data/journal/telegram_update_offset.txt` so restarting it never reprocesses (and
double-records) old button presses. Stop it with Ctrl+C; there is nothing to
clean up.

To keep it running in the background via launchd instead of a terminal tab,
create `~/Library/LaunchAgents/com.aiquantresearchbot.approvals.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aiquantresearchbot.approvals</string>
    <key>ProgramArguments</key>
    <array>
        <string>/full/path/to/ai-quant-research-bot/.venv/bin/python</string>
        <string>-m</string>
        <string>src.approval_listener</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/full/path/to/ai-quant-research-bot</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/full/path/to/ai-quant-research-bot/data/reports/approval_listener.out.log</string>
    <key>StandardErrorPath</key>
    <string>/full/path/to/ai-quant-research-bot/data/reports/approval_listener.err.log</string>
</dict>
</plist>
```

Note `KeepAlive`/`RunAtLoad` instead of `StartCalendarInterval` - this agent is
meant to run continuously, not on a schedule, unlike the daily report job below.

```bash
launchctl load ~/Library/LaunchAgents/com.aiquantresearchbot.approvals.plist
```

Stop it with `launchctl unload ~/Library/LaunchAgents/com.aiquantresearchbot.approvals.plist`.

---

## 6. Schedule the daily report on Mac

### cron

```bash
crontab -e
```

```
0 17 * * 1-5 cd /full/path/to/ai-quant-research-bot && /full/path/to/ai-quant-research-bot/.venv/bin/python -m src.main >> /full/path/to/ai-quant-research-bot/data/reports/cron.log 2>&1
```

### launchd

Create `~/Library/LaunchAgents/com.aiquantresearchbot.daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aiquantresearchbot.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/full/path/to/ai-quant-research-bot/.venv/bin/python</string>
        <string>-m</string>
        <string>src.main</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/full/path/to/ai-quant-research-bot</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>17</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/full/path/to/ai-quant-research-bot/data/reports/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>/full/path/to/ai-quant-research-bot/data/reports/launchd.err.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.aiquantresearchbot.daily.plist
```

Stop it with `launchctl unload ~/Library/LaunchAgents/com.aiquantresearchbot.daily.plist`.

---

## 7. How to read the reports

### Telegram / `report_<date>.json` / `.csv`

- **Market regime / SPY trend / QQQ trend**: derived from EMA50-vs-EMA200 and
  price-vs-SMA200, independent of which tickers Trend Following actually trades.
- **Best sector/ETF**: the highest-scoring ticker among `config.etf_tickers`.
- **Top Candidates**: tickers with a strategy-generated, risk-manager-approved trade
  plan AND a non-Avoid label (see the caveat above), ranked by score, capped at
  `telegram.top_candidates_limit`. Only Safe strategies count here by default (Trend
  Following, Momentum Breakout, Safe Mean Reversion); Aggressive Mean Reversion
  joins the pool only if you've set `aggressive_mode.enabled: true`. Each one is
  also sent as its own follow-up message with Approve Paper Trade / Reject / Watch
  Only buttons (see section 5 above for the listener that processes them) - set
  `paper_trading.enabled: false` to turn that off and keep only the plain report.
- **High Risk Dip Watchlist**: every ticker where Aggressive Mean Reversion
  triggered today, shown with a would-be entry/stop/target/R:R and whether it would
  have cleared the risk rules - informational only. The section header states
  whether Aggressive Mode is currently ENABLED or DISABLED so it's never ambiguous
  why something here isn't (or is) also in Top Candidates.
- **Avoid list**: every ticker whose score fell below `weak_watchlist_min`.
- **Risk warnings**: a standing disclaimer, plus any triggered-but-blocked setups
  (a real strategy signal that failed a risk rule - RSI, SMA200, R:R, or downside >
  upside) so you can see what almost fired and why it didn't.
- The CSV/JSON hold every ticker's full snapshot (indicators, score breakdown, skew,
  trade plan if any, and the Aggressive dip evaluation whether or not it's enabled)
  for your own analysis, including entries left out of Telegram.

### `data/journal/trade_journal.csv`

One append-only row per ticker actually surfaced in a day's Top Candidates -
strategy, entry/stop/target, risk/reward, position size. Aggressive Mean Reversion
never appears here while `aggressive_mode.enabled` is false, no matter how good a
dip setup looks - see the mode table above. `status`, `exit_price`, and `pnl` are
blank by design: Version 1 never executes trades, so nothing exits itself. Fill
those in yourself as you track real-world outcomes (or wait for a later version
that automates it).

### `data/journal/paper_trades.csv`

One row per candidate you explicitly tapped **Approve Paper Trade** on via
Telegram - a strict subset of `trade_journal.csv` (which logs every alert
automatically, regardless of your input) and requires `python -m
src.approval_listener` to have been running when you tapped the button. Unlike
`trade_journal.csv`, this file is **updated in place**, not just appended to:
`python -m src.main` rewrites a trade's own row as it moves from `OPEN` to a
closed status. See "Paper Trade Lifecycle" below for the full column list, the
status values, and exactly how a position gets from `OPEN` to closed. Nothing in
this file was ever a real order - it is a paper record created by tapping a
Telegram button and closed automatically by daily bar data, nothing more.

### `data/journal/pending_approvals.json`

Working state for the approval flow - one entry per Top Candidate message sent,
keyed by `report_date|symbol`, tracking PENDING / APPROVED / REJECTED /
WATCH_ONLY / BLOCKED_AGGRESSIVE_DISABLED. Entries older than
`paper_trading.pending_expiry_hours` (default 72) are silently dropped - tapping
a button on an old message past that point does nothing. You generally don't
need to look at this file directly; it exists so `approval_listener.py` can
resolve a button press back to a specific candidate's full trade detail.

### Backtest output

`backtest_<date>.csv` has one row per strategy (plus "Combined" and the buy-and-hold
benchmarks) with total return, win rate, average win/loss, profit factor, max
drawdown, and Sharpe ratio. The per-strategy `backtest_trades_<strategy>_<date>.csv`
files list every simulated trade if you want to inspect individual entries/exits.

---

## Paper Trade Lifecycle

**Roadmap context, so this phase's scope is never mistaken for more than it is:**

```
Current:  Research -> Scoring -> Risk -> Telegram Approval -> Simulated Paper Trade Tracking
Future:   IBKR Paper Execution, only after this simulated system proves stable.
```

Everything below is still simulation. There is no broker connection anywhere in
this codebase - no IBKR, no `ib_insync`, no order placement of any kind, paper or
real. "Tracking" means: read daily OHLCV bars that were already fetched for the
day's analysis, compare them to a stored stop/target, and update a CSV row. That
is the entire mechanism.

### How an approved trade becomes a tracked position

The moment you tap **Approve Paper Trade**, `paper_trades.record_paper_trade()`
writes one row to `data/journal/paper_trades.csv` with `status: OPEN` immediately
- there is no separate "pending" state for the position itself. (`PENDING` is a
status that belongs to the *approval request* in `pending_approvals.json`, before
you've tapped anything; by the time a position exists at all, that decision has
already been made, so it starts life OPEN - the paper-trade equivalent of "this
would already be live.") Each row gets a unique `trade_id`
(`<ticker>_<approval-date>_<8 hex chars>`) so later updates always target exactly
one row, never accidentally two.

**Full column list**: `trade_id`, `approved_at`, `ticker`, `strategy`, `mode`
(`Safe` / `Aggressive` / `N/A` - only Mean Reversion has a mode), `signal`,
`score`, `entry_price`, `stop_loss`, `target_price`, `risk_reward`,
`position_size`, `risk_amount`, `status`, `opened_at`, `exit_price`, `exited_at`,
`exit_reason`, `pnl_dollars`, `pnl_pct`, `holding_days`, `notes`.

**Status values**: `OPEN`, `TARGET_HIT`, `STOPPED`, `TIME_EXIT`, `CANCELLED`.
`CANCELLED` is defined for completeness (e.g. if you ever want to manually retire
a position by hand-editing the CSV) but nothing in the codebase sets it
automatically today - only `TARGET_HIT`, `STOPPED`, and `TIME_EXIT` are reachable
through normal operation.

### How `python -m src.main` tracks OPEN positions every day

Right after fetching that day's price history (before generating any *new*
candidates), `main.run()` calls `paper_trade_tracker.check_open_trades()`, which:

1. Loads every row from `paper_trades.csv` and skips anything not `status: OPEN`
   outright - a closed trade is never re-examined, by this run or any future one.
2. For each OPEN trade, walks every daily bar **strictly after** `opened_at`, in
   chronological order (oldest first) - never today's bar in isolation if the bot
   skipped a day or two; every intervening bar gets checked so a stop/target hit
   on a day nobody happened to run the bot is still caught correctly. No bar's
   own high/low/close is ever compared against information from a *later* bar -
   that's what "no lookahead" means here, concretely.
3. On each bar, in this order: **if that day's low <= stop_loss, the stop is
   considered hit; else if that day's high >= target_price, the target is
   considered hit; else if the trade has now been held >= `max_holding_days`, it
   force-exits at that day's close.** Whichever condition fires first (in bar
   order) closes the trade - a bar with neither condition leaves it OPEN and the
   walk continues to the next bar.
4. Any newly-closed trade's row is updated in place - `status`, `exit_price`,
   `exited_at`, `exit_reason`, `pnl_dollars`, `pnl_pct`, and `holding_days` are
   all filled in - and the whole file is rewritten once. If nothing closed, the
   file isn't rewritten at all.

**Same-bar conservative assumption**: if a single day's low <= stop_loss AND high
>= target_price both, the stop is assumed to have won. This is not a rule
invented specifically for paper trading - it's the exact same assumption
`backtester.py`'s `_run_strategy_backtest` already uses for the same reason (see
that module's docstring). Reusing it here means the codebase has exactly one
answer to "which one wins on a wild bar," not two different ones depending on
whether a trade was simulated historically or is live today.

**Time-based exit**: `paper_trading.max_holding_days` (default 20) in
`config/settings.yaml` - not hardcoded. A trade that's been open at least that
many calendar days without hitting stop or target exits at the latest available
close with status `TIME_EXIT`.

**Idempotency**: because only `OPEN` rows are ever touched, running
`python -m src.main` (or the tracker) twice on the same day - or twice on
identical data at any time - produces exactly the same result both times. The
second run finds every relevant trade already in a terminal status, closes
nothing new, rewrites nothing, and sends nothing. No double P&L, no duplicate
rows, no reopened trades, no repeated Telegram notification - all four are a
direct consequence of "only evaluate `OPEN` rows," not separate special-cased
guards bolted on afterward.

### Telegram exit notifications

Exactly one message per trade that closed *during that run's* tracker call -
never a trade that was already closed before the run started, since
`check_open_trades()` only ever returns the newly-closed set. Sent before the
daily report, so time-sensitive exit news isn't buried under it:

```
🎯 Paper Target Hit          🛑 Paper Stop Hit          ⏱ Paper Time Exit
```

Each includes ticker, strategy (and mode, for Mean Reversion), entry, exit, P&L
in both % and $, holding days, and the exit reason - plus a standing reminder
that it's a paper trade only.

### Performance analytics

`src/performance_tracker.py` computes everything below purely from **closed**
rows in `paper_trades.csv`, and only ever reads that file - `paper_trades.py`
creates a row on approval, `paper_trade_tracker.py` updates it as the position
resolves, and `performance_tracker.py` never writes to it at all, keeping
"creating/updating a position" and "analyzing already-closed positions" as
separate concerns. With zero closed trades, every function here returns
`has_data: False` rather than a wall of fabricated 0% metrics - callers are
expected to check that flag before showing anything.

**Portfolio-wide** (`compute_portfolio_performance`): total/open/closed trade
counts, wins, losses, win rate, average win %, average loss %, profit factor,
total P&L in $ and %, average/best/worst trade %, average holding days, and max
consecutive wins/losses. `expectancy_per_trade_dollars` only appears once a
group has at least `performance_tracker.MIN_TRADES_FOR_ADVANCED_STATS` (5)
closed trades - below that, a single trade's outcome would be presented as if it
were a stable per-trade expectation, which it isn't. `profit_factor` and
`avg_loss_pct` are `None` (never a fabricated value) when a group has zero
losing trades so far.

**Strategy-level** (`compute_strategy_breakdown`): the same metric set grouped
three ways - by strategy family (Mean Reversion / Momentum Breakout / Trend
Following, with Mean Reversion's Safe and Aggressive rows combined), by Mean
Reversion mode specifically (Safe vs. Aggressive, split back out), and by
ticker. A group only appears if it has at least one closed trade - there's no
placeholder zero-trade entry for a strategy that hasn't closed anything yet, so
code built later (deciding which strategies "don't prove an edge") can tell "no
data" apart from "proven bad" by checking membership, not trusting a value.

### Daily Telegram "Paper Trading Performance" section

Appended to the daily report (gated on `paper_trading.enabled`, same flag as
everything else paper-trading-related): open positions, closed trades, win rate,
total P&L, best strategy, worst strategy - ranked by total P&L $. With zero
closed trades it prints one clean line ("No closed paper trades yet - N open
position(s) being tracked.") instead of a block of misleading 0% figures.

---

## Project structure

```
ai-quant-research-bot/
  README.md
  requirements.txt
  .env.example
  config/settings.yaml
  data/
    raw/          cached daily OHLCV per symbol
    processed/     reserved for future intermediate outputs
    reports/        daily CSV/JSON reports, app.log, backtest output
    journal/          trade_journal.csv, paper_trades.csv, pending_approvals.json,
                        telegram_update_offset.txt, approval_listener.log
  src/
    main.py                 orchestrates the daily run
    data_collector.py        yfinance price + best-effort options fetch
    indicators.py             SMA/EMA/RSI/ATR/Bollinger/momentum/relative volume/volatility
    options_skew.py            ATM/OTM IV sampling + skew calculation
    strategies/
      mean_reversion.py         Strategy 1 (Safe + Aggressive modes)
      momentum_breakout.py       Strategy 2
      trend_following.py          Strategy 3
      skew_map.py                  Contrarian Bid / Chase / Hedged Rally / Fear classifier
    risk_manager.py            universal trade rules + position sizing
    signal_scorer.py             0-100 scoring + labels
    telegram_bot.py                generic Telegram Bot API client (incl. inline keyboards)
    paper_trades.py                 approval-flow domain logic (callback_data, pending
                                      approvals, decision processing) + creates each
                                       OPEN row and owns paper_trades.csv's read/write I/O
    approval_listener.py             standalone process: polls Telegram, resolves button
                                       presses via paper_trades.py
    paper_trade_tracker.py            paper trade lifecycle: stop/target/time-exit
                                        detection, updates paper_trades.csv rows,
                                         exit-notification formatting
    performance_tracker.py            portfolio + strategy/mode/ticker analytics,
                                        read-only from paper_trades.csv
    report_writer.py                   report text + CSV/JSON + journal
    backtester.py                       standalone 1-year+ backtest engine
    utils.py                             config/env loading, logging, error isolation
  tests/
    test_indicators.py
    test_risk_manager.py
    test_signal_scorer.py
    test_strategy_assignment.py
    test_mean_reversion_modes.py
    test_paper_trades.py
    test_approval_listener.py
    test_paper_trade_tracker.py
    test_performance_tracker.py
```

## Scoring (0-100)

| Condition | Points |
|---|---|
| Price above SMA 200 | 15 |
| Price above SMA 50 | 10 |
| 20-day momentum positive | 10 |
| 60-day momentum positive | 10 |
| RSI(14) between 45 and 68 | 10 |
| Volume above 20-day average | 10 |
| Trend Following filter positive (only for its ticker universe) | 10 |
| Momentum Breakout active (only for its ticker universe) | 10 |
| Favorable options skew (Contrarian Bid or Chase - "calls bid") | 10 |
| Best available risk/reward at or above 1.5 | 5 |

**Labels:** 80-100 Strong candidate · 65-79 Watchlist · 50-64 Weak watchlist · below 50 Avoid

## Risk rules (applied to every proposed trade)

- No trade if price is below SMA 200 - **except Mean Reversion candidates**, which
  are exempt from this specific rule (see the wrinkle noted above).
- No trade if RSI(14) is 75 or above (RSI must be strictly below 75 to pass).
- No trade if risk/reward is below 1.5 (must be at least 1.5).
- No trade if expected downside exceeds expected upside.
- Position size = `risk_pct_per_trade`% of `account_equity`, divided by the per-share
  risk (entry minus stop) - both configurable in `config/settings.yaml`.
- No margin, no options trading, no shorting - hard constraints in Version 1.

A candidate becomes a **Top Candidate** only when it clears every rule above AND the
risk manager approved it AND its overall label isn't Avoid. This is the exact same
gate that decides which candidates get an Approve Paper Trade button at all -
tapping Approve can never make a candidate "more eligible" than it already was;
it can only turn an already-eligible candidate into a row in `paper_trades.csv`.
The one thing checked again, live, at the moment you tap Approve rather than just
once at send time is `aggressive_mode.enabled` for Aggressive candidates (see
"Read this before you trust the output" above).

## Disclaimer

Research and educational tool only. Not financial advice. No trades are placed -
paper or real. Approving a candidate via Telegram only appends a row to a local
CSV file; it never connects to a broker, places an order, or risks real money.
Nothing here should be read as a recommendation to buy or sell any security. Do
your own due diligence.
