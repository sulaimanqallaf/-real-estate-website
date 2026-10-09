#!/bin/bash
# Checks the current git branch against its GitHub remote for new commits
# (this project doesn't yet use formal GitHub Releases - see README.md's
# "Honest limitations" section) and, ONLY with your explicit confirmation,
# pulls them in - never automatically, never silently.
#
# SAFE BY CONSTRUCTION:
# - Never applies anything without you typing 'y' at the prompt below.
# - Never discards uncommitted local changes - stashes them first (with
#   a timestamped, recoverable message) if any exist.
# - Always creates a timestamped git tag BEFORE pulling, so rollback is
#   always exactly `git reset --hard <that tag>` - printed for you below.
# - Never touches data/, .env, or anything not tracked by git (those are
#   gitignored already and git operations never touch gitignored files).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=============================================="
echo " AI Quant Research Bot - Check for Updates"
echo "=============================================="
echo

cd "$REPO_ROOT" || { echo "ERROR: could not find the repository root."; exit 1; }

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git not found. Install Xcode Command Line Tools (xcode-select --install) or Git from git-scm.com."
  echo
  echo "Press Enter to close this window."
  read -r _
  exit 1
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Current branch: $BRANCH"
echo "Fetching from GitHub..."
if ! git fetch origin "$BRANCH" --quiet; then
  echo "ERROR: could not reach GitHub. Check your internet connection."
  echo
  echo "Press Enter to close this window."
  read -r _
  exit 1
fi

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH" 2>/dev/null || echo "$LOCAL")"

if [ "$LOCAL" = "$REMOTE" ]; then
  echo
  echo "Already up to date."
  echo
  echo "Press Enter to close this window."
  read -r _
  exit 0
fi

COMMIT_COUNT="$(git rev-list --count "$LOCAL..$REMOTE")"
echo
echo "Update available: $COMMIT_COUNT new commit(s) on origin/$BRANCH:"
git log --oneline "$LOCAL..$REMOTE" | sed 's/^/  /'
echo

read -r -p "Apply this update now? [y/N] " answer
if [[ ! "$answer" =~ ^[Yy]$ ]]; then
  echo "Not updating. Nothing was changed."
  echo
  echo "Press Enter to close this window."
  read -r _
  exit 0
fi

if [ -n "$(git status --porcelain)" ]; then
  STASH_MSG="mac_launcher auto-stash before update $(date -u +%Y%m%dT%H%M%SZ)"
  echo "You have uncommitted local changes - stashing them first (recoverable with: git stash pop)."
  git stash push -m "$STASH_MSG"
fi

BACKUP_TAG="pre-update-$(date -u +%Y%m%dT%H%M%SZ)"
git tag "$BACKUP_TAG"
echo
echo "Rollback point created: $BACKUP_TAG"
echo "  To undo this update later, run in Terminal:"
echo "    cd '$REPO_ROOT' && git reset --hard $BACKUP_TAG"
echo

echo "Pulling update..."
if git pull --ff-only origin "$BRANCH"; then
  echo
  echo "Update applied. Restart the dashboard (Stop Dashboard.command, then"
  echo "Start Dashboard.command) to run the new version - dependencies will"
  echo "be re-checked and updated automatically on the next start if needed."
else
  echo
  echo "ERROR: the update could not be fast-forwarded (your branch may have"
  echo "diverged). No changes were applied. Rollback tag $BACKUP_TAG is"
  echo "still available if you resolve this manually and need it."
fi

echo
echo "Press Enter to close this window."
read -r _
