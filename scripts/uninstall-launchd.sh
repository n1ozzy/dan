#!/usr/bin/env bash
# Manual launchd uninstall for the official com.dan.dand agent
# (LAUNCH_SUPERVISION.md §5, FROZEN). Unloads the agent and removes the
# plist and wrapper. It NEVER deletes the database, logs, or the API token.
# Without --yes it only prints the plan and exits.
set -euo pipefail

LABEL="com.dan.dand"
DAN_HOME="$HOME/.dan"
WRAPPER_DST="$DAN_HOME/bin/dand"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

APPLY=0
if [ "${1:-}" = "--yes" ]; then
  APPLY=1
elif [ -n "${1:-}" ]; then
  echo "Usage: $0 [--yes]" >&2
  exit 2
fi

cat <<PLAN
DAN launchd uninstall plan (label: $LABEL)
==============================================
This will do EXACTLY the following, nothing else:

  1. launchctl bootout $DOMAIN/$LABEL   (only if the agent is loaded)
  2. rm $PLIST_DST
  3. rm $WRAPPER_DST

It NEVER deletes:
  - the database  $DAN_HOME/dan.db
  - the logs      $DAN_HOME/logs/
  - the API token $DAN_HOME/runtime/api-token
PLAN

if [ "$APPLY" -ne 1 ]; then
  echo ""
  echo "Dry run only - nothing was changed. Re-run with --yes to apply."
  exit 0
fi

echo ""
echo "Applying..."

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "$DOMAIN/$LABEL"
  echo "Agent booted out of $DOMAIN."
else
  echo "Agent not loaded in $DOMAIN - nothing to boot out."
fi

if [ -f "$PLIST_DST" ]; then
  rm "$PLIST_DST"
  echo "Removed: $PLIST_DST"
else
  echo "No plist at $PLIST_DST"
fi

if [ -f "$WRAPPER_DST" ]; then
  rm "$WRAPPER_DST"
  echo "Removed: $WRAPPER_DST"
else
  echo "No wrapper at $WRAPPER_DST"
fi

echo ""
echo "Done. Database, logs and token were left untouched."
