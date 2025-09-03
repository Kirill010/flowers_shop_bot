#!/bin/bash

# Путь к виртуальному окружению (если используется)
VENV_PATH="/root/flowers_shop_bot/.venv"
# Команда запуска бота
BOT_CMD="python bot.py"
# Файл логов
LOG_FILE="/root/flowers_shop_bot/bot.log"
# PID файл для отслеживания основного процесса
PID_FILE="/root/flowers_shop_bot/bot.pid"

# Если используется виртуальное окружение, активируем его
if [ -d "$VENV_PATH" ]; then
    source "$VENV_PATH/bin/activate"
fi

case "$1" in
    start)
        echo "Остановка существующих экземпляров бота..."

        # Агрессивное завершение всех процессов бота
        pkill -f "$BOT_CMD" 2>/dev/null
        sleep 2

        # Дополнительная проверка и принудительное завершение
        REMAINING=$(pgrep -f "$BOT_CMD" | wc -l)
        if [ "$REMAINING" -gt 0 ]; then
            echo "Найдено $REMAINING работающих процессов, принудительное завершение..."
            pkill -9 -f "$BOT_CMD" 2>/dev/null
            sleep 1
        fi

        # Запуск нового экземпляра
        echo "Запуск нового экземпляра бота..."
        nohup $BOT_CMD > "$LOG_FILE" 2>&1 &

        # Сохраняем PID
        echo $! > "$PID_FILE"

        echo "Бот запущен с PID: $!"
        ;;

    stop)
        echo "Остановка всех процессов бота..."

        # Сначала обычное завершение
        pkill -f "$BOT_CMD" 2>/dev/null
        sleep 2

        # Проверяем оставшиеся процессы
        REMAINING=$(pgrep -f "$BOT_CMD" | wc -l)
        if [ "$REMAINING" -eq 0 ]; then
            echo "Все процессы бота остановлены."
            # Удаляем PID файл
            rm -f "$PID_FILE"
        else
            echo "Предупреждение: $REMAINING процессов все еще работают, принудительное завершение..."
            pkill -9 -f "$BOT_CMD" 2>/dev/null
            rm -f "$PID_FILE"
        fi
        ;;

    status)
        PIDS=$(pgrep -f "$BOT_CMD")
        if [ -n "$PIDS" ]; then
            COUNT=$(echo "$PIDS" | wc -l)
            echo "Бот работает, PID: $PIDS"
            echo "Количество процессов: $COUNT"

            if [ "$COUNT" -gt 1 ]; then
                echo "ВНИМАНИЕ: Обнаружено несколько экземпляров!"
            fi

            # Проверяем, соответствует ли PID в файле
            if [ -f "$PID_FILE" ]; then
                SAVED_PID=$(cat "$PID_FILE")
                if echo "$PIDS" | grep -q "$SAVED_PID"; then
                    echo "Основной процесс соответствует PID файлу."
                else
                    echo "ВНИМАНИЕ: PID в файле не соответствует работающему процессу."
                fi
            fi
        else
            echo "Бот не запущен."
        fi
        ;;

    restart)
        echo "Перезапуск бота..."
        $0 stop
        sleep 3
        $0 start
        ;;

    *)
        echo "Использование: $0 {start|stop|status|restart}"
        echo "Примеры:"
        echo "  $0 start    - Запустить бота"
        echo "  $0 stop     - Остановить бота"
        echo "  $0 status   - Проверить статус"
        echo "  $0 restart  - Перезапустить бота"
        exit 1
        ;;
esac