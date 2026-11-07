@echo off
chcp 65001 >nul
title qq-bot keeper
cd /d "%~dp0"

rem Keeper logic lives in _keeper.py: it probes NapCat with a python port check,
rem so it does not depend on curl or on the PATH of whatever window launched it.
rem
rem KEEP THIS FILE IN CRLF. cmd.exe mis-parses LF-only .bat files: on 2026-09-13 an
rem LF version made the double-clicked window loop forever printing
rem "The system cannot find the path specified." and the bot never started.
rem All Chinese text goes through _keeper.py (UTF-8 python), never into this .bat.
rem
rem Elevation note (2026-10-26): the old reason is GONE. It used to be that
rem launcher-win10.bat relaunched NapCat through UAC, so a non-elevated keeper
rem got "Access denied" when taskkilling it. That whole inject path was removed
rem on 2026-10-26 - NapCat now only runs as a Docker container, and keeper talks
rem to it through `docker` (no taskkill of a UAC-elevated process any more).
rem Elevation is KEPT anyway for one practical reason: on some machines the
rem Docker Desktop named pipe is only reachable from the Administrators group.
rem If you ever want to try without it, just comment out the net session block.
net session >nul 2>&1
if %ERRORLEVEL% == 0 goto :isadmin
echo Requesting administrator rights (Docker Desktop pipe needs it on some setups)...
powershell -Command "Start-Process -FilePath '%~f0' -Verb runAs"
exit /b

:isadmin
python -u _keeper.py

echo.
echo keeper exited (exit=%errorlevel%). Press any key to close this window.
pause >nul
