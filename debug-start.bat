@echo off
setlocal enabledelayedexpansion

:: 创建调试日志
set "LOGFILE=%~dp0debug.log"
echo ========== 调试日志 %date% %time% ========== > "%LOGFILE%"

echo [检查] 源文件路径... >> "%LOGFILE%"
set "SRC=D:\chennigongzuoshi\shipinhao\desktop\release\win-unpacked"
echo SRC=%SRC% >> "%LOGFILE%"
if exist "%SRC%\视频号图书带货AI.exe" (
    echo [OK] 源exe存在 >> "%LOGFILE%"
) else (
    echo [ERROR] 源exe不存在！ >> "%LOGFILE%"
    goto :error
)

echo [检查] 目标目录... >> "%LOGFILE%"
set "DST=D:\下载\shipinhao-desktop"
echo DST=%DST% >> "%LOGFILE%"
if exist "%DST%" (
    echo [OK] 目标目录存在 >> "%LOGFILE%"
) else (
    echo [INFO] 创建目标目录... >> "%LOGFILE%"
    mkdir "%DST%" >> "%LOGFILE%" 2>&1
)

echo [执行] 关闭旧进程... >> "%LOGFILE%"
taskkill /F /IM "视频号图书带货AI.exe" >> "%LOGFILE%" 2>&1
taskkill /F /IM "shipinhao-backend.exe" >> "%LOGFILE%" 2>&1

echo [执行] 复制文件... >> "%LOGFILE%"
robocopy "%SRC%" "%DST%" /MIR /NFL /NDL /NJH /NJS >> "%LOGFILE%" 2>&1
set ROBOCOPY_EXIT=%ERRORLEVEL%
echo robocopy 退出代码: %ROBOCOPY_EXIT% >> "%LOGFILE%"

if %ROBOCOPY_EXIT% GEQ 8 (
    echo [ERROR] 复制失败！ >> "%LOGFILE%"
    goto :error
) else (
    echo [OK] 复制成功 >> "%LOGFILE%"
)

echo [执行] 启动应用... >> "%LOGFILE%"
cd /d "%DST%" >> "%LOGFILE%" 2>&1
if exist "%DST%\视频号图书带货AI.exe" (
    echo [OK] exe存在，准备启动 >> "%LOGFILE%"
    start "" "%DST%\视频号图书带货AI.exe" >> "%LOGFILE%" 2>&1
    echo [OK] 启动命令已执行 >> "%LOGFILE%"

    :: 等待3秒检查进程
    timeout /t 3 /nobreak >nul
    tasklist | findstr "视频号" >> "%LOGFILE%" 2>&1

    echo. >> "%LOGFILE%"
    echo ========== 完成 ========== >> "%LOGFILE%"

    echo.
    echo 调试完成！请查看日志文件：
    echo %LOGFILE%
    echo.
    notepad "%LOGFILE%"
    goto :end
) else (
    echo [ERROR] exe不存在！ >> "%LOGFILE%"
    goto :error
)

:error
echo.
echo [错误] 执行失败，请查看日志：
echo %LOGFILE%
echo.
notepad "%LOGFILE%"
pause
exit /b 1

:end
pause
