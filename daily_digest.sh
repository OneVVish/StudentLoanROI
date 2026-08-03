#!/bin/zsh
# Daily traffic digest runner, invoked by the launchd agent
# com.studentloanroi.trafficdigest (~/Library/LaunchAgents).
#
#   ./daily_digest.sh              # run for yesterday
#   ./daily_digest.sh --asof 2026-08-02   # re-run a missed day
#
# Appends the digest to analysis_output/traffic_digest.log and posts a macOS
# notification. Any argument is passed straight through to analyze_traffic.py.
#
# A FAILURE MUST BE LOUD. If the database is unreachable, the secrets file is
# missing, or the script raises, a silent failure would look exactly like a day
# with no traffic -- and "we got zero visitors" is a conclusion someone might
# actually act on. So the exit status is checked, the failure is written into
# the log, and the notification says so rather than claiming a digest is ready.

set -u
cd "$(dirname "$0")" || exit 1

PY="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

LOG="analysis_output/traffic_digest.log"
mkdir -p analysis_output

STAMP="$(date '+%Y-%m-%d %H:%M %Z')"
OUT="$("$PY" analyze_traffic.py "$@" 2>&1)"
STATUS=$?

{
  echo "=================== $STAMP ==================="
  echo "$OUT"
  echo ""
} >> "$LOG"

notify() {
  # -e so a missing osascript never takes the whole run down with it.
  /usr/bin/osascript -e "display notification \"$1\" with title \"StudentLoanROI\" subtitle \"$2\"" 2>/dev/null
}

if [ $STATUS -eq 0 ]; then
  # The headline line of the digest, so the notification carries a number
  # rather than only telling you a file changed.
  HEADLINE="$(echo "$OUT" | grep -m1 'Yesterday' | sed 's/^ *//;s/  */ /g')"
  [ -n "$HEADLINE" ] || HEADLINE="digest ready"
  notify "$HEADLINE" "Traffic digest"
else
  echo "  ^^ FAILED with exit status $STATUS" >> "$LOG"
  notify "Digest FAILED - see traffic_digest.log" "Traffic digest"
fi

exit $STATUS
