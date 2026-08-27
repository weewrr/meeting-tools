@echo off
chcp 65001 >nul
title LiteMeet Server Manager
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Please install Python 3.10+ from python.org
  echo [NOTE] Check "Add Python to PATH" during install
  pause
  exit /b 1
)

start "LiteMeet-ServerManager" /min pythonw server-manager\main.py
