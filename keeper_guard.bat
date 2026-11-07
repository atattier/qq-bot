@echo off
chcp 65001 >nul
title keeper 守卫（keeper 死了自动拉起）
cd /d "%~dp0"

rem 不带参数 = 只检查不动作；install/status/remove = 注册/查看/注销计划任务。
set ARG=%1
if "%ARG%"=="" set ARG=--dry
if "%ARG%"=="install" set ARG=--install
if "%ARG%"=="status" set ARG=--status
if "%ARG%"=="remove" set ARG=--remove

net session >nul 2>&1
if %ERRORLEVEL% == 0 goto :isadmin
echo Requesting administrator rights...
powershell -Command "Start-Process -FilePath '%~f0' -Verb runAs"
exit /b

:isadmin
python -u tools\keeper_guard.py %ARG%
echo.
echo 按任意键关闭这个窗口。
pause >nul
