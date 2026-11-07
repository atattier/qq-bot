@echo off
chcp 65001 >nul
title NapCat docker status
cd /d "%~dp0"

net session >nul 2>&1
if %ERRORLEVEL% == 0 goto :isadmin
echo Requesting administrator rights...
powershell -Command "Start-Process -FilePath '%~f0' -Verb runAs"
exit /b

:isadmin
python -u tools\docker_mgr.py status
echo.
echo done (exit=%errorlevel%). Press any key to close this window.
pause >nul
