# -*- coding: utf-8 -*-
"""
tickflow_env.py - TickFlow 环境变量自动加载
导入本模块后，TICKFLOW_API_KEY 会自动注入 os.environ
用法：import tickflow_env  # 放在 from tickflow import TickFlow 之前

加载优先级：
  1. 环境变量（已存在则跳过）
  2. Windows 注册表 HKCU\Environment（用户账户可用）
  3. 配置文件 config/tickflow_key.txt（SYSTEM 账户回退）
"""
import os
from pathlib import Path

if not os.environ.get('TICKFLOW_API_KEY'):
    # 方式 1: Windows 注册表（用户账户）
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment')
        v, _ = winreg.QueryValueEx(k, 'TICKFLOW_API_KEY')
        winreg.CloseKey(k)
        if v:
            os.environ['TICKFLOW_API_KEY'] = v
    except Exception:
        pass

if not os.environ.get('TICKFLOW_API_KEY'):
    # 方式 2: 配置文件回退（SYSTEM 账户或注册表不可用时）
    key_file = Path(__file__).resolve().parent / 'config' / 'tickflow_key.txt'
    if key_file.exists():
        try:
            v = key_file.read_text(encoding='utf-8').strip()
            if v:
                os.environ['TICKFLOW_API_KEY'] = v
        except Exception:
            pass
