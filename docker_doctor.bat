@echo off
chcp 65001 >nul
title 方案B 体检：QQ - 容器 - bot 全链路
cd /d "%~dp0"

rem 只读体检，不需要管理员。断了哪一环它直接告诉你怎么修。
python -u tools\docker_doctor.py %*
echo.
echo 按任意键关闭这个窗口。
pause >nul
