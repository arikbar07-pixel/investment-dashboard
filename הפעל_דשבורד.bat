@echo off
chcp 65001 >nul
echo ════════════════════════════════════════
echo   דשבורד השקעות — מופעל
echo ════════════════════════════════════════
echo.
echo פותח את הדפדפן בכתובת: http://localhost:5000
echo.
echo לסגירה: לחץ Ctrl+C בחלון זה
echo.
start "" "http://localhost:5000"
cd /d "%~dp0"
python app.py
pause
