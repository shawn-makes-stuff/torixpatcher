@echo off
rem Fallback if TorixInstaller.exe is blocked on your PC: runs the same installer with Python (needs Python 3 and: pip install hidapi)
python "%~dp0installer.py"
pause
