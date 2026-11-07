@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  qq-bot 推送 GitHub
echo ============================================
echo.

REM 清除可能干扰的环境变量代理（避免走 WorkBuddy 内部服务代理 65472）
set HTTPS_PROXY=
set HTTP_PROXY=
set https_proxy=
set http_proxy=

echo [1/2] 尝试直连推送...
git push origin master
if errorlevel 1 (
    echo.
    echo [2/2] 直连失败，尝试通过本地代理 (socks5://127.0.0.1:10808) 推送...
    git -c http.proxy=socks5://127.0.0.1:10808 push origin master
    if errorlevel 1 (
        echo.
        echo 推送失败。请确认代理开启或检查远端网络权限。
        pause
        exit /b 1
    )
)

echo.
echo 推送完成！
pause

