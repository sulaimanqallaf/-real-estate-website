# Platform Sprint Changelog

Append-only, newest first. One entry per commit on
`claude/platform-integration-sprint1`, in the same spirit as the rest of this
codebase's commit messages: what changed and why, not just what.

## [Unreleased]

- Added `docs/platform/{ARCHITECTURE,PROGRESS_CHECKLIST,ROADMAP,BLOCKERS,
  CHANGELOG}.md` — the persistent engineering plan for this sprint, written
  from a direct audit of both the scheduler branch and the dashboard branch
  (which already contains the scheduler branch's full history). Establishes
  that deliverables B, C, and D's deterministic cores were already
  substantially built in earlier phases of this project — this sprint's job
  on those is audit + gap-fill, not a rebuild.
