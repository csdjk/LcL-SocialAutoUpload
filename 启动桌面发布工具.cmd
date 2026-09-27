@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0启动桌面发布工具.ps1" %*
if errorlevel 1 pause
