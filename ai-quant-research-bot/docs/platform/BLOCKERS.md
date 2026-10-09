# Blockers — things no amount of coding in this sandbox can resolve

Each of these needs an action from the user (a credential, an account, a
purchase, or an explicit authorization) before the corresponding checklist
item in `PROGRESS_CHECKLIST.md` can move from "code complete, mock-verified"
to "broker/production verified." Listed in the order they'd likely unblock
the most work.

## 1. Real IBKR TWS/Gateway paper session
**Blocks**: broker-verifying ANY of deliverable D's safety checks for real
(connection handling, real fill timing, real commission reporting, real
reconnect behavior). `FakeBroker`-based tests prove the state-machine logic
is correct; they cannot prove the real `ibapi` socket client behaves the same
way against a live TWS/Gateway process, because this sandbox has no network
path to one and no running IBKR session to attach to.
**What you'd need to do**: on your Mac, start TWS or IB Gateway in Paper
Trading mode, confirm `ibapi` is installed (`pip install ibapi`), and run the
existing `python -m src.execution.circuit_breaker status` /
`position_monitor.py` against it. This is exactly what the main README's
existing "Rollout" section already walks through — nothing new is needed
there; this sprint didn't touch that path.

## 2. Licensed live/delayed market data feed
**Blocks**: deliverable A's "provider abstraction for licensed live market
data" being anything more than an abstraction. Today's data is yfinance
(free, delayed, best-effort) — fine for research/paper trading, not a
substitute for a licensed feed if you ever need real-time quotes for live
execution.
**What you'd need to do**: pick a vendor (Polygon.io, Tradier, IEX Cloud,
Alpaca Data, etc.), get an API key, and implement one more
`data_providers/*_provider.py` against the existing `ProviderResult`
contract — the interface is already there and doesn't need to change.

## 3. Forex/crypto feed and broker support
**Blocks**: any REAL forex/crypto functionality (today: interface stubs only,
honestly `STATUS_UNAVAILABLE`).
**What you'd need to do**: pick a feed/broker for each asset class; IBKR
itself supports both, so `ibkr_client.py` could eventually be extended rather
than replaced — but `order_state.py`'s `OrderIntent` is currently hard-scoped
to equity/ETF by design (a deliberate safety boundary, not an oversight) and
widening it is real work that deserves its own reviewed milestone, not a
drive-by change buried in this sprint.

## 4. Real copyrighted books/research for the RAG knowledge library
**Blocks**: the knowledge library having anything indexed beyond the
synthetic test fixtures this sprint ships. The retrieval ENGINE is real and
tested; what it retrieves is only as good as what you legally provide it.
**What you'd need to do**: drop legally-obtained text/markdown files (or
PDF-extracted text) into the directory `knowledge_library.py`'s docstring
names, and the existing ingestion pipeline picks them up — no further coding
needed for that part.

## 5. Apple Developer signing identity
**Blocks**: a signed/notarized `.app` for the Mac launcher. Without one,
Gatekeeper will show an "unidentified developer" warning on first launch of
the `.command` script this sprint ships — a one-time right-click-Open
workaround, explained in `mac_launcher/README.md`, not a security hole.
**What you'd need to do**: enroll in the Apple Developer Program ($99/yr) if
you want a fully signed/notarized app; this is a cost+account decision the
user explicitly reserved, not something to do unilaterally.

## 6. Cloud account for 24/7 deployment
**Blocks**: deliverable H actually running anywhere. This sprint prepared
Dockerfiles/compose and deployment documentation; nothing was deployed, and
no cloud account was touched, per the user's explicit "do not deploy without
permission."
**What you'd need to do**: choose a provider and give explicit go-ahead —
then the prepared containers are the starting point, not a from-scratch
effort.

## 7. The user's own $500 real-money authorization
**Blocks**: nothing in this sprint — real-money execution was never enabled,
attempted, or scaffolded as a live path. `src/utils.py`'s
`VALID_EXECUTION_MODES` has no LIVE mode; nothing added this sprint changes
that. See `docs/platform/ROADMAP.md`'s "Path to the $500 pilot" section for
what WOULD need to happen first, entirely gated on the user's own future
explicit sign-off.
