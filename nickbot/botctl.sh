#!/usr/bin/env bash
# Start / stop / inspect Nick Bypass Bot (nohup + pidfile, so pkill cannot hit
# the wrong process).
set -u
cd "$(dirname "$0")"

PIDFILE=data/bot.pid
LOG=data/bot.log
mkdir -p data

running() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "${1:-start}" in
  start)
    if running; then echo "already running (pid $(cat "$PIDFILE"))"; exit 0; fi
    PYTHONUNBUFFERED=1 nohup python3 -m nickbot >>"$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    sleep 5
    if running; then
      echo "started (pid $(cat "$PIDFILE")), log: $LOG"
    else
      echo "failed to start, tail of the log:"; tail -25 "$LOG"; exit 1
    fi
    ;;
  stop)
    if running; then kill "$(cat "$PIDFILE")"; sleep 1; echo "stopped"; else echo "not running"; fi
    rm -f "$PIDFILE"
    ;;
  restart) "$0" stop; "$0" start ;;
  status)
    if running; then echo "running (pid $(cat "$PIDFILE"))"; else echo "not running"; fi
    echo "--- log tail ---"; tail -12 "$LOG" 2>/dev/null
    ;;
  log) tail -f "$LOG" ;;
  *) echo "usage: $0 {start|stop|restart|status|log}"; exit 1 ;;
esac
