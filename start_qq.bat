@echo off
chcp 65001 >nul
title qq-bot one-click start
cd /d "%~dp0"

rem One-click: ensure NapCat (QQ gateway) -> start bot -> open admin console with token.
rem The whole flow lives in _oneclick.py; keepalive is delegated to _keeper.py.
rem
rem KEEP THIS FILE IN CRLF. cmd.exe mis-parses LF-only .bat files: on 2026-09-13 an LF
rem version of start_all.bat made the double-clicked window loop forever printing
rem "The system cannot find the path specified." and the bot never started.
rem All Chinese text lives in _oneclick.py / _keeper.py, never in this .bat.
python -u _oneclick.py

echo.
echo one-click start exited (exit=%errorlevel%). Press any key to close this window.
pause >nul
