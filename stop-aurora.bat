@echo off
REM Stops AURORA (see scripts\stop-aurora.ps1). Data and volumes are kept.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-aurora.ps1" %*
if errorlevel 1 pause
