"""Fault-injection tests: real network failures via Toxiproxy (Sprint 3,
Reliability milestone: "test internet outages... use Toxiproxy...
where beneficial").

**Why Toxiproxy here, evaluated directly rather than assumed**: a real
TCP connection reset behaves differently from a Python mock that just
raises `ConnectionError` - the mock proves our EXCEPTION-HANDLING logic
is correct, but says nothing about whether the exception a real broken
socket produces is actually the type/shape that logic expects. Tested
directly in this sandbox (curl + a real `toxiproxy-server` binary,
confirmed reachable from GitHub releases despite the org network
policy blocking financial-data vendor hosts specifically): injecting a
real `reset_peer` toxic against a real local HTTP server produces
`requests.exceptions.ConnectionError(ConnectionResetError(104, ...))`
- the exact exception `src/reliability.py`'s retry policy is written
to catch. These two tests exercise that REAL path.

**Why NOT used for every fault-injection scenario in this file's
sibling tests** (`test_fault_injection_*.py` for stale data, broker
disconnects, duplicate orders, DB errors): those are pure in-process
state-machine edge cases with no TCP connection involved at all -
Toxiproxy has nothing to inject there, and a fake/mock is not a
lesser-effort shortcut for them, it is the CORRECT tool (same
reasoning `docs/platform/BROKER_REFERENCE_REVIEW.md` already used to
decide what Sprint 3 built vs. deliberately didn't).

**Optional, not a hard dependency**: `toxiproxy-server` is an external
Go binary, not a PyPI package - these tests SKIP cleanly wherever it
isn't installed (this project's own CI/dev sandbox here included,
unless `scripts/setup_toxiproxy.sh` has been run). Nothing else in
this project's test suite depends on it.
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import requests

from src import reliability

LOGGER = logging.getLogger("test")
REPO_ROOT = Path(__file__).resolve().parent.parent
_FAST = dict(max_attempts=4, min_wait_seconds=0.05, max_wait_seconds=0.15)


def _find_toxiproxy_server() -> str | None:
    env_path = os.environ.get("TOXIPROXY_SERVER_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    bundled = REPO_ROOT / ".bin" / "toxiproxy-server"
    if bundled.exists():
        return str(bundled)
    return shutil.which("toxiproxy-server")


TOXIPROXY_SERVER = _find_toxiproxy_server()
pytestmark = pytest.mark.skipif(
    TOXIPROXY_SERVER is None,
    reason="toxiproxy-server not installed - run scripts/setup_toxiproxy.sh to enable these real-socket fault-injection tests",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _OkHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A002 - silence per-request logging
        pass


@pytest.fixture
def toxiproxy_env():
    """Starts a real local HTTP server, a real toxiproxy-server process
    proxying to it, and one configured proxy route. Yields the proxy's
    base URL and a helper to add/remove toxics via toxiproxy's own
    control REST API. Tears both real processes down afterward."""
    upstream_port = _free_port()
    listen_port = _free_port()
    control_port = _free_port()

    upstream = http.server.HTTPServer(("127.0.0.1", upstream_port), _OkHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()

    proc = subprocess.Popen(
        [TOXIPROXY_SERVER, "-host", "127.0.0.1", "-port", str(control_port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    control_url = f"http://127.0.0.1:{control_port}"
    try:
        for _ in range(50):
            try:
                if requests.get(f"{control_url}/version", timeout=0.2).status_code == 200:
                    break
            except requests.exceptions.ConnectionError:
                pass
            time.sleep(0.1)
        else:
            pytest.fail("toxiproxy-server did not become ready in time")

        requests.post(
            f"{control_url}/proxies",
            json={"name": "test_proxy", "listen": f"127.0.0.1:{listen_port}", "upstream": f"127.0.0.1:{upstream_port}"},
            timeout=2,
        ).raise_for_status()

        class _Env:
            base_url = f"http://127.0.0.1:{listen_port}"

            @staticmethod
            def add_toxic(name: str, toxic_type: str, **attributes):
                requests.post(
                    f"{control_url}/proxies/test_proxy/toxics",
                    json={"name": name, "type": toxic_type, "stream": "downstream", "toxicity": 1.0, "attributes": attributes},
                    timeout=2,
                ).raise_for_status()

            @staticmethod
            def remove_toxic(name: str):
                requests.delete(f"{control_url}/proxies/test_proxy/toxics/{name}", timeout=2)

        yield _Env()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        upstream.shutdown()
        upstream_thread.join(timeout=5)


def test_a_real_connection_reset_is_retried_and_recovers(toxiproxy_env):
    """Injects a REAL reset_peer toxic (the proxy's socket actually
    resets the connection), then removes it from a background timer
    shortly after - the retried call must recover once the real
    network condition clears, exactly like a transient outage ending
    mid-retry-loop."""
    toxiproxy_env.add_toxic("reset", "reset_peer", timeout=0)

    def _clear_toxic_shortly():
        time.sleep(0.02)
        toxiproxy_env.remove_toxic("reset")

    threading.Thread(target=_clear_toxic_shortly, daemon=True).start()

    @reliability.retrying(LOGGER, **_FAST)
    def _do_fetch():
        resp = requests.get(toxiproxy_env.base_url, timeout=2)
        resp.raise_for_status()
        return resp

    response = _do_fetch()
    assert response.json() == {"ok": True}


def test_a_persistent_connection_reset_raises_after_exhausting_retries(toxiproxy_env):
    """The toxic is never removed - this must exhaust all retry
    attempts and reraise the REAL ConnectionError, never silently
    succeed or hang."""
    toxiproxy_env.add_toxic("reset", "reset_peer", timeout=0)

    @reliability.retrying(LOGGER, **_FAST)
    def _do_fetch():
        resp = requests.get(toxiproxy_env.base_url, timeout=2)
        resp.raise_for_status()
        return resp

    with pytest.raises(requests.exceptions.ConnectionError):
        _do_fetch()
