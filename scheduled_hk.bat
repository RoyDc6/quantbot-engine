@echo off
REM QuantBot HK Trading - Scheduled Task Wrapper
REM Runs every trading day at 09:35 Beijing time
cd /d E:\quant
"C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe" E:\quant\unified_runner.py --market HK --live >> E:\quant\output\schedule_hk.log 2>&1
