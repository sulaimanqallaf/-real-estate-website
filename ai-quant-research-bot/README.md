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
6. Includes a standalone backtester (1+ year, per strategy, vs. buy-and-hold).

**This version does not place trades, connect to a broker, use margin, trade
options, or short anything.** It only collects data, analyzes it, scores it, and
sends an alert. Nothing here executes an order.

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
  Reversion candidates specifically, which are exempt from the SMA200 check (see the
  next bullet). A tradeable setup on an Avoid-labeled ticker still shows up in the
  saved CSV/JSON (for your own review) but is left out of the Telegram report and
  the trade journal.
- **Mean Reversion's SMA200 exemption has a wrinkle worth knowing.** The risk
  manager waives the "price above SMA200" rule for Mean Reversion candidates, since
  that strategy is explicitly a dip-buy. But `strategies/mean_reversion.py` still has
  its own internal `require_trend_intact` gate (on by default in
  `config/settings.yaml`), which requires price above SMA200 before the strategy
  will even propose a candidate in the first place. Net effect: today, mean
  reversion still only fires above the 200D MA in practice - the risk-manager
  exemption is real and matters the moment you set `require_trend_intact: false`,
  but until then it's a no-op. Flip that flag if you want genuine "buy the dip
  during a longer downtrend" setups to reach the risk manager at all.
- **The backtester is a research approximation, not a portfolio simulator.** Each
  strategy gets its own independent capital pool; there's no shared-margin or
  cross-strategy position limit modeling. Entries fill at the next bar's open after
  a signal closes (no lookahead); a stop and target hitting the same bar assumes the
  stop wins (conservative). See `src/backtester.py`'s module docstring for the full
  list of simplifications.

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
- Fetches a best-effort options chain per ticker for skew (also isolated - an
  options failure never blocks the price/indicator/strategy pipeline).
- Computes indicators, runs the three strategies against their assigned ticker
  universes, classifies skew, scores and ranks everything.
- Saves `data/reports/report_<date>.csv` / `.json`, appends
  `data/journal/trade_journal.csv`, and logs to `data/reports/app.log`.
- Sends the Telegram report (splitting into multiple messages if long). Set
  `telegram.enabled: false` in the config to skip sending while still generating
  reports.

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

## 5. Schedule it daily on Mac

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

## 6. How to read the reports

### Telegram / `report_<date>.json` / `.csv`

- **Market regime / SPY trend / QQQ trend**: derived from EMA50-vs-EMA200 and
  price-vs-SMA200, independent of which tickers Trend Following actually trades.
- **Best sector/ETF**: the highest-scoring ticker among `config.etf_tickers`.
- **Top Candidates**: tickers with a strategy-generated, risk-manager-approved trade
  plan AND a non-Avoid label (see the caveat above), ranked by score, capped at
  `telegram.top_candidates_limit`.
- **Avoid list**: every ticker whose score fell below `weak_watchlist_min`.
- **Risk warnings**: a standing disclaimer, plus any triggered-but-blocked setups
  (a real strategy signal that failed a risk rule - RSI, SMA200, R:R, or downside >
  upside) so you can see what almost fired and why it didn't.
- The CSV/JSON hold every ticker's full snapshot (indicators, score breakdown, skew,
  trade plan if any) for your own analysis, including entries left out of Telegram.

### `data/journal/trade_journal.csv`

One append-only row per ticker actually surfaced in a day's Top Candidates -
strategy, entry/stop/target, risk/reward, position size. `status`, `exit_price`, and
`pnl` are blank by design: Version 1 never executes trades, so nothing exits itself.
Fill those in yourself as you track real-world outcomes (or wait for a later version
that automates it).

### Backtest output

`backtest_<date>.csv` has one row per strategy (plus "Combined" and the buy-and-hold
benchmarks) with total return, win rate, average win/loss, profit factor, max
drawdown, and Sharpe ratio. The per-strategy `backtest_trades_<strategy>_<date>.csv`
files list every simulated trade if you want to inspect individual entries/exits.

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
    journal/          trade_journal.csv
  src/
    main.py                 orchestrates the daily run
    data_collector.py        yfinance price + best-effort options fetch
    indicators.py             SMA/EMA/RSI/ATR/Bollinger/momentum/relative volume/volatility
    options_skew.py            ATM/OTM IV sampling + skew calculation
    strategies/
      mean_reversion.py         Strategy 1
      momentum_breakout.py       Strategy 2
      trend_following.py          Strategy 3
      skew_map.py                  Contrarian Bid / Chase / Hedged Rally / Fear classifier
    risk_manager.py            universal trade rules + position sizing
    signal_scorer.py             0-100 scoring + labels
    telegram_bot.py                Telegram Bot API client
    report_writer.py                report text + CSV/JSON + journal
    backtester.py                    standalone 1-year+ backtest engine
    utils.py                          config/env loading, logging, error isolation
  tests/
    test_indicators.py
    test_risk_manager.py
    test_signal_scorer.py
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
risk manager approved it AND its overall label isn't Avoid.

## Disclaimer

Research and educational tool only. Not financial advice. No trades are placed.
Nothing here should be read as a recommendation to buy or sell any security. Do
your own due diligence.
