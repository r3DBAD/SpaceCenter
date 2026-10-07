@echo off
chcp 65001 >nul
rem Запуск веб-интерфейса на Windows: двойной клик по файлу
cd /d "%~dp0"
where python >nul 2>nul && (set PY=python) || (set PY=py)
echo.
echo Логины: head, engineer, fuel, meteo, telemetry   Пароль: demo2026
echo Закройте это окно, чтобы остановить сервер.
%PY% -m lcc --db demo.db
pause
