@echo off
rem Removes the Torix plug-in from ASUS Aura (asks for administrator rights).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_aura_hal.ps1" -Uninstall
pause
