@echo off
chcp 65001 >nul
title 方案B 1/3 装 Docker 环境（装完要重启一次）
cd /d "%~dp0"

rem 装 WSL2 + 虚拟机平台 + Docker Desktop。装完必须重启电脑。
rem 重启后再双击本文件，会自动跳过已完成的步骤，接着起容器。
net session >nul 2>&1
if %ERRORLEVEL% == 0 goto :isadmin
echo Requesting administrator rights...
powershell -Command "Start-Process -FilePath '%~f0' -Verb runAs"
exit /b

:isadmin
python -u tools\docker_install.py
echo.
echo 退出码 %errorlevel%（10 = 需要重启电脑后再双击一次）
echo 按任意键关闭这个窗口。
pause >nul
