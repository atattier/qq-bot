@echo off
cd /d "%~dp0"
python patrol_bot.py
if errorlevel 1 pause
