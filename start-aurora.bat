@echo off
REM Starts AURORA (see scripts\start-aurora.ps1). Arguments are passed through, e.g. -NoBrowser.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-aurora.ps1" %*
if errorlevel 1 pause
