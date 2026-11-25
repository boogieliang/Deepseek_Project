@echo off
rem Run install.ps1 under a bypassed execution policy (the default policy here blocks .ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
