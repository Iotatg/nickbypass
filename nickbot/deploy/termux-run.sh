#!/data/data/com.termux/files/usr/bin/bash
# Start the bot inside tmux, so it survives closing the Termux session.
#   bash deploy/termux-run.sh          start
#   bash deploy/termux-run.sh stop     stop (and skip auto-start at next boot)
#   bash deploy/termux-run.sh status   is it up?
#   bash deploy/termux-run.sh log      follow the log
set -e
cd "$(dirname "$0")/.."

SESSION=nickbot
LOG=data/bot.log
STOPPED=data/stopped
mkdir -p data

case "${1:-start}" in
  stop)
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "stopped" || echo "not running"
    # The Termux:Boot hook checks this file, so a deliberate stop sticks.
    : > "$STOPPED"
    ;;
  log)
    tail -f "$LOG"
    ;;
  status)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "running (tmux: $SESSION)"
    else
      echo "not running"
      exit 1
    fi
    ;;
  start)
    termux-wake-lock 2>/dev/null || true
    rm -f "$STOPPED"
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "already running — attach with: tmux attach -t $SESSION"
      exit 0
    fi
    tmux new-session -d -s "$SESSION" "bash deploy/termux-supervise.sh"
    sleep 3
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "started (tmux session: $SESSION)"
      echo "attach: tmux attach -t $SESSION   ·   log: bash deploy/termux-run.sh log"
    else
      echo "failed to start — last log lines:"
      tail -20 "$LOG"
      exit 1
    fi
    ;;
  *)
    echo "usage: bash deploy/termux-run.sh [start|stop|status|log]"
    ;;
esac
