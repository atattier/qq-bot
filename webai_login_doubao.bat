@echo off
chcp 65001 >nul
title webai doubao login
rem ======================================================================
rem Open Doubao in the SAME Chrome profile the bot uses. Log in once.
rem Close the window afterwards - the profile saves automatically.
rem
rem FIXED 2026-10-25 (user logged in but bot still said "not logged in"):
rem   `start "" chrome.exe --user-data-dir=...` while Chrome is ALREADY
rem   running just forwards the URL to the existing instance and IGNORES
rem   --user-data-dir. The user then logs into his daily profile and the
rem   bot profile stays anonymous (decoded proof: doubao get_web_anon_id
rem   still returned uid="anon").
rem Now: only launch when no Chrome is already using this profile.
rem We never kill the user's own Chrome - that would drop his tabs.
rem ======================================================================

set "PROFILE=%%LOCALAPPDATA%%\webai-agent\doubao_profile"
set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
set "URL=https://www.doubao.com/chat/"

if not exist "%CHROME%" (
  echo [x] Chrome not found: %CHROME%
  echo     Edit the CHROME variable in this .bat.
  pause
  exit /b 1
)

set "RUNNING="
for /f "tokens=*" %%i in ('wmic process where "name='chrome.exe'" get CommandLine /value 2^>nul ^| findstr /i "doubao_profile"') do set "RUNNING=1"

if defined RUNNING (
  echo [i] A Chrome window is already using the bot profile.
  echo     Switch to it and log in there.
  echo     If you cannot find it, close ALL Chrome windows and run this again.
  pause
  exit /b 0
)

echo [i] Opening Doubao with the bot profile...
echo     profile: %PROFILE%
echo.
start "" "%CHROME%" --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check "%URL%"

echo Opened. Log in, then CLOSE that Chrome window, then press any key here.
echo.
echo Self-check: python _diag_login_state.py  (result in data\_diag_login.json)
pause >nul
