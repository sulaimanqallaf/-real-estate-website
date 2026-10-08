"""Shared single-instance file lock, used by every long-running process in
this system that must never have two copies running at once against the
same resource: `approval_listener.py` (Telegram's `getUpdates` allows only
one poller per bot token) and `position_monitor.py` (two copies would
double-submit/race protective stop/target orders against the same broker
account).
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Any


class ProcessAlreadyRunningError(Exception):
    """Raised by `acquire_singleton_lock()` when another process already
    holds the named lock."""


def acquire_singleton_lock(path: Path, process_label: str, extra_hint: str = "") -> Any:
    """Takes an exclusive, non-blocking `flock` on `path`. Returns the open
    file object - the CALLER must keep a reference to it alive for the
    life of the process (closing it, or the process exiting/crashing for
    any reason, releases the lock automatically; the OS owns this, unlike
    a hand-rolled PID file that can go stale after an unclean exit).
    Raises `ProcessAlreadyRunningError` if another process already holds
    it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(path, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock_file.close()
        hint = f" {extra_hint}" if extra_hint else ""
        raise ProcessAlreadyRunningError(
            f"Another {process_label} is already running (lock held on {path}).{hint}"
        ) from exc
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file
