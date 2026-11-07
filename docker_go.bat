@echo off
chcp 65001 >nul
title NapCat docker up + wire
cd /d "%~dp0"

rem 起 NapCat 容器，再把宿主机上的 bot 接到容器里的 OneBot。
rem 第一次用之前先在 docker-napcat\.env 填 NAPCAT_ACCOUNT。
net session >nul 2>&1
if %ERRORLEVEL% == 0 goto :isadmin
echo Requesting administrator rights...
powershell -Command "Start-Process -FilePath '%~f0' -Verb runAs"
exit /b

:isadmin
python -u tools\docker_mgr.py up
if errorlevel 1 goto :fail
python -u docker-napcat\wire.py
echo.
echo done (exit=%errorlevel%). Press any key to close this window.
pause >nul
exit /b

:fail
echo.
echo 容器没起来。看上面的报错，或用 docker_status.bat 查状态。
pause >nul
