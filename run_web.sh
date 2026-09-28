#!/bin/sh
# Запуск веб-интерфейса на Linux / macOS: ./run_web.sh
cd "$(dirname "$0")" || exit 1
[ -f demo.db ] || python3 -m lcc --db demo.db --demo
echo "Логины: head, engineer, fuel, meteo, telemetry   Пароль: demo2026"
python3 -m lcc --db demo.db --web --open
