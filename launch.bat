@echo off
cd /d "%~dp0"
git pull
python accelerated_reactor.py
pause
