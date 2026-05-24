@echo off
REM QuantBot US Trading - Scheduled Task Wrapper
REM Runs every trading day at 21:35 Beijing time
cd /d E:\quant
"C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe" E:\quant\unified_runner.py --market US --live >> E:\quant\output\schedule_us.log 2>&1
