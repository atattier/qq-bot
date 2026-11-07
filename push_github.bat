@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  qq-bot 推送到 GitHub
echo ============================================
echo.

git push
if errorlevel 1 (
    echo.
    echo 推送失败：请检查网络、代理设置与远端权限。
    pause
    exit /b 1
)

echo.
echo 推送完成！
pause
