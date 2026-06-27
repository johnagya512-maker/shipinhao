@echo off
chcp 65001 >nul
title Shipinhao AI - Deploy
echo ============================================================
echo  Shipinhao AI - Deploy to Desktop
echo ============================================================
echo.

echo [1/4] Stopping old processes...
taskkill /F /IM "shipinhao-backend.exe" >nul 2>&1
ping -n 3 127.0.0.1 >nul
echo      Done.
echo.

set "SRC=D:\chennigongzuoshi\shipinhao\desktop\release\win-unpacked"
set "DST=C:\Users\Administrator\Desktop\shipinhao-desktop"

echo [2/4] Checking build output...
if not exist "%SRC%" (
    echo      [ERROR] Build output not found. Run build.bat first.
    pause
    exit /b 1
)
echo      Build found.
echo.

echo [3/4] Copying to desktop directory...
robocopy "%SRC%" "%DST%" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo      [ERROR] Copy failed. Close the app and try again.
    pause
    exit /b 1
)
echo      Copy done.
echo.

echo [4/4] Launching app...
cd /d "%DST%"
start "" "%DST%\*.exe"
echo      App launched.
echo.
echo ============================================================
echo  Done!
echo ============================================================
pause
