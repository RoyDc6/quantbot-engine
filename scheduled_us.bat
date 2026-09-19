@echo off
REM QuantBot US Signal Report - Scheduled Task Wrapper
REM Runs every trading day at 21:35 Beijing time; no account or order access
@chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set QUANT_LIVE_CONFIRM=
cd /d E:\quant
"C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe" E:\quant\unified_runner.py --market US --research-report >> E:\quant\output\schedule_us.log 2>&1
