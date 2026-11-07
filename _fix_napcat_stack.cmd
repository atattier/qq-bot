@echo off
chcp 65001 >nul
title 收拾 NapCat 容器栈（需管理员）
cd /d "%~dp0"

REM =====================================================================
REM  用法：右键本文件 ->「以管理员身份运行」
REM
REM  2026-10-26 改版：注入版 NapCat 已彻底删除（只剩 Docker 容器版），
REM  所以这里不再杀 NapCatWinBootMain.exe —— 留着那段只会让人以为还有注入版。
REM
REM  现在做三件事：
REM   1. 看看 3000 / 6099 到底谁占着（应该只有 com.docker.backend / wslrelay）
REM   2. 清 Docker Desktop 的 GPU/着色器缓存（它老崩的元凶之一）
REM   3. 把容器拉起来并报告状态
REM
REM  不会碰你自己手动登的 QQ —— 它是宿主机上的普通进程，和容器无关。
REM =====================================================================

echo.
echo [1/4] 当前端口占用（3000 = OneBot，6099 = 扫码页）：
netstat -ano ^| findstr ":3000 "
netstat -ano ^| findstr ":6099 "
echo.
echo      若出现的 PID 不是 com.docker.backend / wslrelay，用下面这条查是谁：
echo        tasklist /FI "PID eq ^<上方PID^>"
echo.

echo [2/4] 清 Docker Desktop 的 GPU 缓存（崩溃元凶之一）...
del /f /s /q "%APPDATA%\Docker Desktop\GPUCache\*" 2>nul
del /f /s /q "%APPDATA%\Docker Desktop\GPUPersistentCache\*" 2>nul
del /f /s /q "%APPDATA%\Docker Desktop\ShaderCache\*" 2>nul
del /f /s /q "%APPDATA%\Docker Desktop\GrShaderCache\*" 2>nul
echo      完成
echo.

echo [3/4] 确认 Docker 守护进程...
docker version --format "{{.Server.Version}}" 2>nul
if errorlevel 1 (
  echo      Docker 没起来。请自己双击桌面的「Docker Desktop」^（别用管理员^），等它转绿再回来。
  goto :done
)
echo      Docker 在线。
echo.

echo [4/4] 拉起 napcat 容器并看状态...
docker compose -f docker-napcat\docker-compose.yml up -d
echo.
docker ps --filter "name=napcat" --format "{{.Names}}^t{{.Status}}^t{{.Ports}}"
echo.

:done
echo 容器起来后小鲸会自动快速登录，一般不用再扫码。
echo 要看扫码页：http://127.0.0.1:6099/webui   token 在 docker-napcat\.env 的 WEBUI_TOKEN
echo.
pause
