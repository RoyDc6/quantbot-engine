# -*- coding: utf-8 -*-
import re

path = r'E:\quant\fusion_framework\full_backtest_v2.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 替换：融合框架只用STRONG_BUY买入 → 放宽到BUY
old = "if fusion.level == SignalLevel.STRONG_BUY and s['pos'] == 0:"
new = "if fusion.level in (SignalLevel.STRONG_BUY, SignalLevel.BUY) and s['pos'] == 0:"
if old in content:
    content = content.replace(old, new)
    print(f"修复: STRONG_BUY -> STRONG_BUY|BUY")
else:
    print("未找到目标文本，可能已修复或路径不对")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("保存完成")
