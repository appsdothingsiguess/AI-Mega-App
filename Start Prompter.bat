@echo off
title Prompter
echo [Prompter] Starting...
cd /d "%~dp0"

python scripts\start_prompter.py %*
if errorlevel 1 (
    echo.
    echo [Prompter] Something went wrong. See output above.
    pause
)
