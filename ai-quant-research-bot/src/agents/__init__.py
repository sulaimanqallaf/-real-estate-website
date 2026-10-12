"""Shared runtime for Sprint 4's new autonomous agents (Research
Scholar, News Agent, Strategy Lab) - Priority 6: "durable job
scheduling, independent agent workers, recovery checkpoints, cost
limits, daily health reporting."

This package provides the plumbing every new agent needs, so each
agent module only has to write its own actual research/analysis logic:

- `registry.py` - a durable, per-agent job-status file (RUNNING /
  SUCCESS / FAILED, start/finish time, summary, error) - what Priority
  5's dashboard reads to show "genuine activity, job status, progress,
  errors."
- `heartbeat.py` - the same crash-recovery heartbeat pattern
  `src/execution/watchdog.py` already established for
  `position_monitor.py`, generalized across any agent name (Sprint 3
  only ever had one long-running process; Sprint 4 introduces three).
- `budget.py` - a daily call-count cap per (agent, external source) -
  "cost limits" for free APIs that have no dollar cost but do have
  real daily/rate quotas worth respecting.
- `checkpoint.py` - a resumable-state file so a long paginated fetch
  job can pick up where it left off after a crash instead of
  restarting from scratch.

**No agent built on this package gets any new execution privilege.**
Nothing here imports `src.execution`, nothing here can place, cancel,
or modify a broker order, and nothing here touches
`autonomous_paper.*`/`execution.mode`. This package is pure
bookkeeping (JSON files under `data/journal/agents/`) - it has no
opinion on what an agent DOES, only on how its liveness/status/budget
is recorded.
"""
