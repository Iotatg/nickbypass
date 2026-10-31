#!/data/data/com.termux/files/usr/bin/bash
# Keeps the bot alive inside one tmux pane.
#
#   python -m nickbot   exits  ->  start it again
#   exits 5 times fast         ->  give up, leave the reason in the log
#
# /restart replaces the process with os.execv, so the supervisor never notices
# and there is no gap. Android killing the app (low RAM, battery saver) is the
# case this file exists for.
set -u
cd "$(dirname "$0")/.."

LOG=data/bot.log
STOPPED=data/stopped
fails=0

while :; do
  python -m nickbot >> "$LOG" 2>&1
  code=$?
  [ -f "$STOPPED" ] && exit 0
  [ "$code" -eq 0 ] && fails=0 || fails=$((fails + 1))
  if [ "$fails" -ge 5 ]; then
    printf '%s [supervisor] stopped after 5 quick exits, last code %s\n' \
      "$(date '+%Y-%m-%d %H:%M:%S')" "$code" >> "$LOG"
    exit 1
  fi
  [ "$fails" -gt 0 ] && sleep $((fails * 5)) || sleep 2
done
