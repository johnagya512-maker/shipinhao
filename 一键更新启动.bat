@echo off
title 视频号AI - 一键更新到最新版
cls
echo ============================================================
echo  视频号图书带货AI - 一键更新启动器
echo  作用: 结束旧进程 - 覆盖最新程序 - 从正式目录启动
echo ============================================================
echo.

echo [1/4] 结束所有正在运行的旧进程...
taskkill /F /IM "视频号图书带货AI.exe" >nul 2>&1
taskkill /F /IM "shipinhao-backend.exe" >nul 2>&1
ping -n 3 127.0.0.1 >nul
echo      完成。
echo.

set "SRC=D:\chennigongzuoshi\shipinhao\desktop\release\win-unpacked"
set "DST=D:\下载\shipinhao-desktop"

echo [2/4] 检查最新构建产物...
if not exist "%SRC%\视频号图书带货AI.exe" (
    echo      [提示] 未找到打包文件，启动开发模式
    echo.
    start "后端" cmd /k "cd /d D:\chennigongzuoshi\shipinhao\backend && python -m uvicorn app.main:app --reload"
    timeout /t 2 /nobreak >nul
    start "前端" cmd /k "cd /d D:\chennigongzuoshi\shipinhao\frontend && npm run dev"
    echo      已启动后端和前端，请查看弹出的窗口
    pause
    exit /b 0
)
echo      找到最新产物。
echo.

echo [3/4] 覆盖到正式安装目录 %DST% ...
if not exist "%DST%" mkdir "%DST%"
robocopy "%SRC%" "%DST%" /MIR /NFL /NDL /NJH /NJS /NP /R:3 /W:2 >nul
if errorlevel 8 (
    echo      [错误] 复制失败，可能有文件被占用。请手动关闭程序后重试。
    pause
    exit /b 1
)
echo      覆盖完成，正式目录已是最新版。
echo.

echo [4/4] 从正式目录启动最新版...
cd /d "%DST%"
start "" "%DST%\视频号图书带货AI.exe" >nul 2>&1 <nul
echo      已发出启动指令。若没弹出窗口, 请手动双击:
echo      %DST%\视频号图书带货AI.exe
echo.
echo ============================================================
echo  完成！现在跑的是最新代码
echo  注意: 以后启动就双击这个脚本或桌面快捷方式, 别再双击Setup安装包
echo ============================================================
echo  客户端启动完成, 本窗口 2 秒后自动关闭。
timeout /t 2 >nul
exit
