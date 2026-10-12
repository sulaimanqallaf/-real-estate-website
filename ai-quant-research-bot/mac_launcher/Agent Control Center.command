#!/bin/bash
# Sprint 5: unified Start/Stop/Status/Logs/Rollback control center for
# the autonomous research agent fleet (Research Scholar, News Agent,
# Strategy Lab, Agent Fleet Health). Distinct from Start/Stop/Status
# Dashboard.command (which control the web dashboard, a separate
# process) - this is about the AGENTS themselves.
#
# SAFE BY CONSTRUCTION:
# - "Run now" options run exactly one cycle in the foreground - plain
#   research/learning agents, no broker access, no order submission,
#   ever (none of these modules import src.execution).
# - Schedule install/uninstall always asks "[y/N]" per job - never a
#   blanket "install everything," never automatic. Enforced in Python
#   (src/agents/launchd_installer.py's RESERVED_EXISTING_LABELS), this
#   can never touch any of the 7 existing com.aiquantresearchbot.* jobs.
# - Rollback only ever points at a git tag "Check for Updates.command"
#   already created before its own last applied pull - never discards
#   anything else, never force-pushes, and refuses if your working
#   tree has uncommitted changes it would otherwise silently lose.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"

echo "=================================================================="
echo " AI Quant Research Bot - Agent Control Center (Sprint 5)"
echo "=================================================================="
echo

if [ ! -x "$PYTHON_BIN" ]; then
  echo "ERROR: $REPO_ROOT/.venv not found."
  echo "Create it once: cd '$REPO_ROOT' && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  echo
  echo "Press Enter to close this window."
  read -r _
  exit 1
fi

run_now() {
  local module="$1" label="$2"
  echo
  echo "--- Running $label now (foreground, one cycle) ---"
  (cd "$REPO_ROOT" && "$PYTHON_BIN" -m "$module")
  echo "--- $label finished (exit code $?) ---"
}

manage_schedule() {
  echo
  "$PYTHON_BIN" -m src.agents.launchd_installer list
  echo
  echo "Enter a job name to install/uninstall its schedule, or press Enter to go back:"
  echo "  (research_scholar, news_agent, strategy_lab, agent_fleet_health)"
  read -r JOB_NAME
  [ -z "$JOB_NAME" ] && return

  read -r -p "Install or uninstall $JOB_NAME's schedule? [i/u/N] " ACTION
  case "$ACTION" in
    i|I)
      read -r -p "Install the launchd schedule for $JOB_NAME now? [y/N] " CONFIRM
      if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
        (cd "$REPO_ROOT" && "$PYTHON_BIN" -m src.agents.launchd_installer install "$JOB_NAME")
      else
        echo "Not installing."
      fi
      ;;
    u|U)
      read -r -p "Uninstall the launchd schedule for $JOB_NAME now? [y/N] " CONFIRM
      if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
        (cd "$REPO_ROOT" && "$PYTHON_BIN" -m src.agents.launchd_installer uninstall "$JOB_NAME")
      else
        echo "Not uninstalling."
      fi
      ;;
    *)
      echo "No action taken."
      ;;
  esac
}

tail_log() {
  echo
  echo "Available logs in $REPO_ROOT/data/reports:"
  ls "$REPO_ROOT"/data/reports/*.log 2>/dev/null | xargs -n1 basename 2>/dev/null || true
  echo
  read -r -p "Log filename to tail (or press Enter to go back): " LOG_NAME
  [ -z "$LOG_NAME" ] && return
  if [ ! -f "$REPO_ROOT/data/reports/$LOG_NAME" ]; then
    echo "No such log file yet: $LOG_NAME"
    return
  fi
  echo "--- tailing $LOG_NAME (Ctrl+C to stop, then press Enter here to return to the menu) ---"
  tail -f "$REPO_ROOT/data/reports/$LOG_NAME"
}

rollback() {
  echo
  cd "$REPO_ROOT" || return

  if [ -n "$(git status --porcelain)" ]; then
    echo "You have uncommitted local changes - refusing to roll back automatically"
    echo "so nothing of yours is silently discarded. Commit or 'git stash' first,"
    echo "then run this again."
    return
  fi

  LAST_TAG="$(git tag --list 'pre-update-*' --sort=-creatordate | head -1)"
  if [ -z "$LAST_TAG" ]; then
    echo "No rollback point found - 'Check for Updates.command' creates one"
    echo "automatically before every update it actually applies."
    return
  fi

  echo "Most recent rollback point: $LAST_TAG"
  git log -1 --oneline "$LAST_TAG"
  echo
  read -r -p "Reset this checkout to $LAST_TAG now? This discards any commits made since. [y/N] " CONFIRM
  if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
    git reset --hard "$LAST_TAG"
    echo "Rolled back to $LAST_TAG."
  else
    echo "Not rolling back."
  fi
}

while true; do
  echo
  echo "------------------------------------------------------------------"
  echo "1) Show agent fleet status (registry/heartbeat/budget for all agents)"
  echo "2) Run Research Scholar now"
  echo "3) Run News Agent now"
  echo "4) Run Strategy Lab now"
  echo "5) Manage agent schedules (install/uninstall - always opt-in)"
  echo "6) Tail a log file"
  echo "7) Rollback to the last 'Check for Updates' backup point"
  echo "8) Exit"
  read -r -p "Choose an option [1-8]: " CHOICE
  case "$CHOICE" in
    1) (cd "$REPO_ROOT" && "$PYTHON_BIN" -m src.agents.health_report) ;;
    2) run_now "src.agents.research_scholar" "Research Scholar" ;;
    3) run_now "src.agents.news_agent" "News Agent" ;;
    4) run_now "src.agents.strategy_lab" "Strategy Lab" ;;
    5) manage_schedule ;;
    6) tail_log ;;
    7) rollback ;;
    8) break ;;
    *) echo "Invalid choice." ;;
  esac
done

echo
echo "Press Enter to close this window."
read -r _
