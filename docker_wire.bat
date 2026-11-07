@echo off
chcp 65001 >nul
title Wire bot to NapCat container
cd /d "%~dp0"

net session >nul 2>&1
if %ERRORLEVEL% == 0 goto :isadmin
echo Requesting administrator rights...
powershell -Command "Start-Process -FilePath '%~f0' -Verb runAs"
exit /b

:isadmin
python -u docker-napcat\wire.py
echo.
echo done (exit=%errorlevel%). Press any key to close this window.
pause >nul
