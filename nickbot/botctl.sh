#!/usr/bin/env bash
# 启动 / 停止 / 查看 Nick Bypass Bot（nohup + pidfile，避免 pkill 误杀）
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
    if running; then echo "已在运行 (pid $(cat "$PIDFILE"))"; exit 0; fi
    PYTHONUNBUFFERED=1 nohup python3 -m nickbot >>"$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    sleep 5
    if running; then
      echo "已启动 (pid $(cat "$PIDFILE"))，日志：$LOG"
    else
      echo "启动失败，日志末尾："; tail -25 "$LOG"; exit 1
    fi
    ;;
  stop)
    if running; then kill "$(cat "$PIDFILE")"; sleep 1; echo "已停止"; else echo "未运行"; fi
    rm -f "$PIDFILE"
    ;;
  restart) "$0" stop; "$0" start ;;
  status)
    if running; then echo "运行中 (pid $(cat "$PIDFILE"))"; else echo "未运行"; fi
    echo "--- 日志末尾 ---"; tail -12 "$LOG" 2>/dev/null
    ;;
  log) tail -f "$LOG" ;;
  *) echo "用法: $0 {start|stop|restart|status|log}"; exit 1 ;;
esac
