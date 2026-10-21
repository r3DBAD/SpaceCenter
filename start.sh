#!/bin/sh
# Запуск веб-интерфейса на Linux / macOS: ./start.sh
cd "$(dirname "$0")" || exit 1
echo "Логины: head, director, director2, inspector, engineer, fuel, meteo, telemetry   Пароль: demo2026"
python3 -m lcc --db demo.db --meteo-stub
