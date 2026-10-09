"""`python -m src.execution.after_close` - the production launchd entry
point for the daily research run (Daily Reliability & Safe Automation
milestone, "final production-safe daily scheduling setup" follow-up).

Meant to be invoked FREQUENTLY by launchd (`StartInterval`, e.g. every 5
minutes, all day) - see README for why. Each invocation is near-zero-cost
when the answer is "skip" (`market_calendar.decide_after_close_run()` is
pure datetime/calendar math, no network call), and only calls
`src.main.run()` - which already owns its own singleton lock,
same-day-already-succeeded guard, health-status bookkeeping, and
Telegram failure alert, see `main.py`'s `run()` docstring - once the
real decision is "run."

**`--simulate`/`--dry-run`** (identical, both accepted): prints the
decision and exits, WITHOUT importing `src.main` (so no OpenAI, no
Telegram, no IBKR, no price-data fetch is even importable along that
path - see `tests/test_execution_after_close_safety.py`'s grep-based
guardrail) and without writing anything to the health-status file. Safe
to run any number of times, any time, on any machine.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone


def _run_real(logger: logging.Logger) -> int:
    from . import run_health

    config = _load_config()
    already = run_health.already_succeeded_today(config)
    decision, reason = _decide(already)
    logger.info("after_close decision: %s (%s)", decision, reason)
    if decision == "skip":
        return 0

    from .. import main as main_module

    return main_module.run()


def _load_config():
    from ..utils import load_config, load_env

    load_env()
    return load_config(None)


def _decide(already_succeeded_today: bool) -> tuple[str, str]:
    from . import market_calendar

    now = datetime.now(timezone.utc)
    return market_calendar.decide_after_close_run(now, already_succeeded_today)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="NYSE-calendar-aware launchd entry point for the daily research run."
    )
    parser.add_argument(
        "--simulate", "--dry-run", dest="simulate", action="store_true",
        help="Print the run/skip decision and exit. Never imports src.main - no OpenAI, Telegram, or IBKR call is possible along this path.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("after_close")

    if args.simulate:
        config = _load_config()
        from . import run_health

        already = run_health.already_succeeded_today(config)
        decision, reason = _decide(already)
        print(f"Decision: {decision.upper()} - {reason}")
        print("(--simulate: no data fetch, no LLM call, no Telegram message, no IBKR order - src.main was never imported)")
        return 0

    return _run_real(logger)


if __name__ == "__main__":
    sys.exit(main())
