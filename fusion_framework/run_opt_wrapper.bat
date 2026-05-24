@echo off
chcp 65001 >nul
cd /d E:\quant\fusion_framework
"C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe" -X utf8 weight_optimization.py > E:\quant\fusion_framework\opt_output_utf8.txt 2>&1
