@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stand.ps1" %*
exit /b %ERRORLEVEL%
