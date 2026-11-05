@echo off
rem LinguaTUI launcher for Windows (double-click or run from cmd/PowerShell)
setlocal
set PYTHONUTF8=1
chcp 65001 >nul
cd /d "%~dp0"
python -m linguatui %*
if errorlevel 1 pause
