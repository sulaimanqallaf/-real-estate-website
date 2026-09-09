# AI Quant Data Collector and Signal Engine (v1)

A Mac-friendly Python tool that:

1. Downloads daily historical price data for a configurable list of tickers (via `yfinance`).
2. Computes quantitative indicators (momentum, moving averages, RSI, ATR, volume, volatility).
3. Scores each asset 0-100 and classifies it as **Strong Buy Setup**, **Watchlist**, or **Avoid**.
4. Estimates an entry zone, target, stop loss, and risk/reward — for informational purposes only.
5. Sends a daily report to a Telegram chat, and saves it locally as CSV and JSON.

**This version does not place trades, connect to a broker, or use real money.** It only
collects data, analyzes it, scores it, and sends an alert. Nothing in this project executes
orders or touches IBKR/margin.

---

## 1. Install on Mac

Requirements: Python 3.10+ (check with `python3 --version`; install via
[python.org](https://www.python.org/downloads/) or `brew install python` if needed).

```bash
cd ai-quant-bot

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 2. Create the Telegram bot

1. Open Telegram and message **@BotFather**.
2. Send `/newbot` and follow the prompts (choose a name and a unique username ending in `bot`).
3. BotFather replies with a token that looks like `123456789:AAExampleTokenHere`. This is your
   `TELEGRAM_BOT_TOKEN`.
4. Get your chat id:
   - Message your new bot anything (e.g. "hi") first, so it can message you back.
   - Then open this URL in a browser (replace `<TOKEN>`):
     `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Find `"chat":{"id": ...}` in the JSON response — that number is your `TELEGRAM_CHAT_ID`.
   - If you want reports in a group instead, add the bot to the group, send a message there,
     and read the group's id (it will be negative) the same way.

---

## 3. Configure the `.env` file

```bash
cp .env.example .env
```

Edit `.env` and fill in the two values from step 2:

```
TELEGRAM_BOT_TOKEN=123456789:AAExampleTokenHere
TELEGRAM_CHAT_ID=123456789
```

Non-secret settings (tickers, scoring thresholds, risk rules, indicator windows) live in
`config/settings.yaml` — edit that file directly to change the watchlist or tune scoring.

---

## 4. Run the app manually

```bash
source .venv/bin/activate   # if not already active
python -m src.main
```

What happens on each run:

- Downloads daily history for every ticker in `config/settings.yaml` (`tickers:`).
- Any symbol that fails to download or is missing data is logged and skipped — one bad
  symbol never crashes the whole run.
- Computes indicators, scores, and ranks every successfully-fetched symbol.
- Saves `data/reports/report_<date>.csv` and `data/reports/report_<date>.json`.
- Sends the formatted report to your Telegram chat (splitting into multiple messages if the
  report is too long for a single Telegram message).
- Logs everything to `data/reports/app.log` as well as the console.

To test without sending to Telegram, set `telegram.enabled: false` in
`config/settings.yaml`. The CSV/JSON report is still generated either way.

Run the test suite any time with:

```bash
python -m pytest tests/ -v
```

---

## 5. Schedule it daily on Mac

### Option A: cron

```bash
crontab -e
```

Add a line to run every weekday at 5:00 PM (after US market close), adjusting the path:

```
0 17 * * 1-5 cd /full/path/to/ai-quant-bot && /full/path/to/ai-quant-bot/.venv/bin/python -m src.main >> /full/path/to/ai-quant-bot/data/reports/cron.log 2>&1
```

### Option B: launchd (more native to macOS)

Create `~/Library/LaunchAgents/com.aiquantbot.daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aiquantbot.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/full/path/to/ai-quant-bot/.venv/bin/python</string>
        <string>-m</string>
        <string>src.main</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/full/path/to/ai-quant-bot</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>17</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/full/path/to/ai-quant-bot/data/reports/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>/full/path/to/ai-quant-bot/data/reports/launchd.err.log</string>
</dict>
</plist>
```

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.aiquantbot.daily.plist
```

To stop it later: `launchctl unload ~/Library/LaunchAgents/com.aiquantbot.daily.plist`

---

## Project structure

```
ai-quant-bot/
  README.md
  requirements.txt
  .env.example
  config/
    settings.yaml        # tickers, indicator windows, scoring weights, risk rules
  data/
    raw/                  # cached daily OHLCV per symbol
    processed/            # reserved for future intermediate outputs
    reports/              # daily CSV/JSON reports + app.log
  src/
    main.py                # orchestrates the daily run
    data_fetcher.py         # yfinance download + local caching
    indicators.py            # momentum, SMA, RSI, ATR, volume ratio, volatility
    scoring.py                # 0-100 scoring system
    signals.py                 # signal classification + entry/target/stop estimation
    telegram_bot.py             # Telegram Bot API client
    report_writer.py             # report text formatting + CSV/JSON persistence
    utils.py                      # config/env loading, logging, per-symbol error isolation
  tests/
    test_scoring.py                # unit tests for scoring + signal logic
```

## Scoring logic (0-100)

| Condition                                   | Points |
|----------------------------------------------|--------|
| Price above 200D moving average               | 20     |
| Price above 50D moving average                | 10     |
| 20D momentum positive                          | 15     |
| 60D momentum positive                          | 15     |
| RSI(14) between 45 and 68                       | 15     |
| Volume above 20D average                        | 10     |
| ATR-based volatility not extreme (ATR/price ≤ 4%)| 10     |
| Price not more than 8% above 50D MA               | 5      |

**Signal classification:** 75-100 → Strong Buy Setup · 50-74 → Watchlist · below 50 → Avoid

## Risk rules for the estimated trade plan

- Stop loss = entry zone low − 1.5× ATR(14) (configurable).
- Target is set so the reward is at least 1.5× the risk.
- A trade plan is **not** proposed (shown as "No trade recommended" in the report) if:
  - Risk/reward would be below 1.2, or
  - RSI(14) is above 75 (overbought), or
  - Price is below the 200D moving average.
- The `signal` classification (Strong Buy / Watchlist / Avoid) is driven purely by the 0-100
  score, per the spec. A symbol can score as "Watchlist" or even "Strong Buy Setup" on trend/
  momentum quality while still having no trade plan proposed if it fails a risk rule (e.g.
  RSI just spiked above 75) — the report's "Reason" line explains why in that case.

## Disclaimer

This tool is for research and educational purposes only. It does not constitute financial
advice, and it does not execute trades. Nothing here should be interpreted as a
recommendation to buy or sell any security. Always do your own due diligence.
