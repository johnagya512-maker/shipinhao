@echo off
chcp 65001 >nul
title Shipinhao AI - Full Build
echo ============================================================
echo  Shipinhao AI - Full Build (backend + frontend + desktop)
echo ============================================================
echo.
rem === 1. Stop old processes ===
echo [1/5] Stopping old processes...
taskkill /F /IM "shipinhao-backend.exe" >nul 2>&1
taskkill /F /IM "shipinhao-desktop.exe" >nul 2>&1
echo      Done.
echo.
rem === 2. Build backend (PyInstaller) ===
echo [2/5] Building backend (PyInstaller)...
cd /d D:\chennigongzuoshi\shipinhao\backend
if exist "dist\shipinhao-backend" (
    rd /s /q "dist\shipinhao-backend"
)
call pyinstaller shipinhao-backend.spec --clean
if errorlevel 1 (
    echo      [ERROR] Backend build failed
    pause
    exit /b 1
)
echo      Done.
echo.
rem === 3. Build frontend (Vite) ===
echo [3/5] Building frontend (React + Vite)...
cd /d D:\chennigongzuoshi\shipinhao\frontend
call npm run build
if errorlevel 1 (
    echo      [ERROR] Frontend build failed
    pause
    exit /b 1
)
echo      Done.
echo.
rem === 4. Copy frontend to desktop app ===
echo [4/5] Copying frontend to desktop app...
robocopy "D:\chennigongzuoshi\shipinhao\frontend\dist" "D:\chennigongzuoshi\shipinhao\desktop\frontend" /MIR /NFL /NDL /NJH /NJS /NP >nul
echo      Done.
echo.
rem === 5. Build desktop app (Electron) ===
echo [5/5] Building desktop app (Electron)...
cd /d D:\chennigongzuoshi\shipinhao\desktop
call npm run build
if errorlevel 1 (
    echo      [ERROR] Desktop build failed
    pause
    exit /b 1
)
echo      Done.
echo.
echo ============================================================
echo  Build complete! Run deploy script to install.
echo ============================================================
pause
