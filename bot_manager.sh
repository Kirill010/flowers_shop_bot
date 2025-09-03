#!/bin/bash
  BOT_CMD="python bot.py"
  LOG FILE="bot.log"
case "$1" in
  start)
    echo "Stopping any existing bots…
    # Более агрессивное завершение
    pkill -9 -f "$BOT_CMD" 2>/dev/null
    sleep 3
    # проверяем, что все процессы убиты
    REMAINING=$ (pgrep -f "$BOT CMD" | wC -w)
    if [ "$REMAINING" -gt 0 ]; then
      echo "WARNING: $REMAINING processes still running, killing forceful
      pkill -9 -f "$BOT CMD"
      sleep 2
    fi
    # Запускаем новый
    echo "Starting new bot…"
    nohup $BOT_CMD > $LOG_FILE 2>&1 &
    echo "Bot started with PID: $!"
    ;;
  stop)
    echo "Stopping all bot processes…"
    pkill -9 -f "$BOT CMD" 2>/dev/null
    REMAINING=$ (pgrep -f "$BOT_CMD" | wC -w)
    if [ "$REMAINING" -eq 0 ]; then
      echo "All bot processes stopped"
    else
      echo "Warning: $REMAINING processes still running"
    fi
    ;;
  status)
    PIDS=$ (pgrep -f "$BOT_CMD")
    if [ -n "$PIDS" ]; then
      COUNT=$(echo "$PIDS" | wc -w)
      echo "Bot is running with PIDs: $PIDS"
      echo "Total processes: $COUNT"
    if [ "$COUNT" -gt 1 ]; then
      echo "WARNING: Multiple instances detected!"
    fi
  else
    echo "Bot is not running"
  fi
  ;;
  restart)
    $0 stop
    sleep 3
    $0 start
    ;;
  *)
    echo "Usage: $0 {start|stop|status|restart}"
    exit 1
esac