@echo off
chcp 65001 >nul
title restart qq-bot (bot only)
cd /d "%~dp0"

rem Restart ONLY the bot process. The keeper (started by start_all.bat) sees the
rem bot exit and starts a fresh one by itself, so no second keeper is created.
rem Why this file exists: start_all.bat IS the keeper launcher. If a keeper is
rem already running it refuses to start a second one, so double-clicking it does
rem not reload bot.py. Editing bot.py therefore needs this helper instead.
rem ASCII only, CRLF only (see the LF note in start_all.bat).

net session >nul 2>&1
if %ERRORLEVEL% == 0 goto :isadmin
echo Requesting administrator rights (the bot runs elevated)...
powershell -Command "Start-Process -FilePath '%~f0' -Verb runAs"
exit /b

:isadmin
echo [1/3] stopping the running bot on port 8088...
powershell -NoProfile -Command "$p = Get-NetTCPConnection -LocalPort 8088 -State Listen -ErrorAction SilentlyContinue; if ($p) { $p | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { try { Stop-Process -Id $_ -Force -ErrorAction Stop; Write-Host ('  stopped PID ' + $_) } catch { Write-Host ('  failed to stop PID ' + $_) } } } else { Write-Host '  no bot is listening on 8088' }"

echo [2/3] stopping a leftover TTS helper on port 9880 (the bot restarts it on demand)...
powershell -NoProfile -Command "$p = Get-NetTCPConnection -LocalPort 9880 -State Listen -ErrorAction SilentlyContinue; if (-not $p) { Write-Host '  none' } else { $p | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { $cl = (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_) -ErrorAction SilentlyContinue).CommandLine; if ($cl -and $cl -match 'api_v2\.py') { Stop-Process -Id $_ -Force; Write-Host ('  stopped TTS PID ' + $_) } else { Write-Host ('  port 9880 held by PID ' + $_ + ' (not api_v2.py), left alone') } } }"

echo [3/3] waiting for the keeper to start a new bot...
powershell -NoProfile -Command "$deadline = (Get-Date).AddSeconds(120); while ((Get-Date) -lt $deadline) { Start-Sleep -Seconds 2; $p = Get-NetTCPConnection -LocalPort 8088 -State Listen -ErrorAction SilentlyContinue; if ($p) { Write-Host ('  bot is back, PID ' + $p[0].OwningProcess); exit 0 } }; Write-Host '  TIMEOUT: bot did not come back'; exit 1"
if errorlevel 1 goto :fail

echo.
echo Done. New code is live. Try the voice / cover commands in QQ now.
echo.
pause
exit /b

:fail
echo.
echo The keeper did not restart the bot.
echo Close this window, then double-click start_all.bat.
echo.
pause
exit /b
