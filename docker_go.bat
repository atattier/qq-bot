@echo off
chcp 65001 >nul
title 方案B 一键落地（起容器 + 接线 + 切模式 + 验收）
cd /d "%~dp0"

rem 方案B的总入口：可以反复双击，已完成的阶段自动跳过。
rem 第一次会在「启用 WSL 功能」之后要求重启；重启后再双击一次继续。
net session >nul 2>&1
if %ERRORLEVEL% == 0 goto :isadmin
echo Requesting administrator rights...
powershell -Command "Start-Process -FilePath '%~f0' -Verb runAs"
exit /b

:isadmin
python -u tools\docker_bootstrap.py
echo.
echo 退出码 %errorlevel%（10 = 需要重启电脑后再双击一次）
echo 按任意键关闭这个窗口。
pause >nul
