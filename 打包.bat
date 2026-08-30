@echo off
rem ============================================================
rem  本地打包：先把 tools/ 打成 7z 发布包（可选先下载最新版）
rem  用法：双击本文件，或命令行加参数，如：
rem     pack.bat --download    先全量下载/更新再打包
rem     pack.bat --mx 9        高压缩
rem     pack.bat --name mybox  自定义包名
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 uv，请先安装 https://docs.astral.sh/uv/
    pause
    exit /b 1
)

uv run pack %*
set RC=%ERRORLEVEL%

echo.
if "%RC%"=="0" (
    echo 打包完成，压缩包在项目根目录。
) else (
    echo 打包失败或已取消，退出码 %RC%。
)
pause
exit /b %RC%
