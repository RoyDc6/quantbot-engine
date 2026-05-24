@echo off
:: 快速执行脚本模板 - 绕过Git Bash开销
:: 使用前请确保运行环境已预热

setlocal enabledelayedexpansion

:: 设置Python路径和工作目录
set PYTHONPATH=E:\quant\paper_trading;%PYTHONPATH%
cd /d E:\quant\paper_trading

:: 使用直接调用，避免环境检查
"C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe" ^
    -W ignore ^
    -c "import sys; print(f'Python {sys.version}'); import daily_runner; daily_runner.main()"

if %errorlevel% neq 0 (
    echo ❌ 执行失败，错误码: %errorlevel%
    pause
) else (
    echo ✅ 执行完成
)