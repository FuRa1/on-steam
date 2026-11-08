@echo off
rem Switch the LG TV on or off through the helper. Usage: tv-power.cmd on|off
rem The helper's ssh alias defaults to "l590"; override with the TVWAKE_HELPER_SSH environment variable.
setlocal
set "ACTION=%~1"
if /i not "%ACTION%"=="on" if /i not "%ACTION%"=="off" (
    echo Usage: %~nx0 on^|off
    pause
    exit /b 2
)
set "HELPER=%TVWAKE_HELPER_SSH%"
if "%HELPER%"=="" set "HELPER=l590"

title TV %ACTION%
echo TV %ACTION% via %HELPER% ...
ssh -o BatchMode=yes -o ConnectTimeout=5 %HELPER% "cd ~/tv-wake-launcher && bash scripts/tv-%ACTION%.sh"
if errorlevel 1 (
    echo.
    echo Failed. Is the helper on? See docs\OPERATIONS.md
    pause
    exit /b 1
)
rem Leave the result visible for a moment, then close.
timeout /t 3 >nul
