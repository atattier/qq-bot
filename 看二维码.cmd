@echo off
chcp 65001 >nul
cd /d "%~dp0"
python "%~dp0tools\show_qr.py" %*
echo.
pause
