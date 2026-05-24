"""P0 fix 验证脚本 — 运行此文件确认 5 项修改生效"""
import sys
sys.path.insert(0, 'E:/quant/fusion_framework')
sys.path.insert(0, 'E:/quant')

# 先导入 fusion_engine（触发 else 分支的绝对导入），再获取 signal_types
# 这样可以避免相对导入的 parent package 错误
import fusion_engine
from fusion_framework.signal_types import SignalLevel, POSITION_MAP

# === P0-2: POSITION_MAP ===
print('=== P0-2: POSITION_MAP ===')
assert POSITION_MAP[SignalLevel.SELL] == 0.0, f'SELL should be 0.0, got {POSITION_MAP[SignalLevel.SELL]}'
assert POSITION_MAP[SignalLevel.STRONG_SELL] == 0.0, f'STRONG_SELL should be 0.0, got {POSITION_MAP[SignalLevel.STRONG_SELL]}'
print(f'  SELL: {POSITION_MAP[SignalLevel.SELL]} ✅')
print(f'  STRONG_SELL: {POSITION_MAP[SignalLevel.STRONG_SELL]} ✅')

# === P0-3: LLM Level Adjustment ===
engine = fusion_engine.FusionEngine()
print('\n=== P0-3: LLM Level Adjustment ===')

tests = [
    (SignalLevel.HOLD, 30.0, SignalLevel.BUY, 'upgrade'),
    (SignalLevel.BUY, -30.0, SignalLevel.HOLD, 'downgrade'),
    (SignalLevel.STRONG_BUY, 50.0, SignalLevel.STRONG_BUY, 'ceiling-no-beyond'),
    (SignalLevel.STRONG_SELL, -50.0, SignalLevel.STRONG_SELL, 'floor-no-beyond'),
    (SignalLevel.HOLD, 10.0, SignalLevel.HOLD, 'below-threshold-noop'),
    (SignalLevel.REDUCED, 30.0, SignalLevel.HOLD, 'REDUCED-to-HOLD'),
    (SignalLevel.SELL, 30.0, SignalLevel.REDUCED, 'SELL-to-REDUCED'),
    (SignalLevel.BUY, 40.0, SignalLevel.STRONG_BUY, 'BUY-to-STRONG_BUY'),
    (SignalLevel.HOLD, -30.0, SignalLevel.REDUCED, 'HOLD-to-REDUCED'),
]

all_pass = True
for level_in, llm_score, expected, name in tests:
    result = engine._apply_llm_level_adjustment(level_in, llm_score)
    ok = result == expected
    status = 'PASS' if ok else 'FAIL'
    if not ok:
        all_pass = False
    print(f'  [{status}] {name}: {level_in.value} + {llm_score:+.0f}LLM -> {result.value} (expect {expected.value})')

print(f'\n  LLM adjustment: {"ALL PASS ✅" if all_pass else "SOME FAILED ❌"}')

# === P0-1: daily_runner.py 语法检查 ===
print('\n=== P0-1: daily_runner.py syntax ===')
import py_compile
try:
    py_compile.compile('E:/quant/paper_trading/daily_runner.py', doraise=True)
    print('  Syntax OK ✅')
except py_compile.PyCompileError as e:
    print(f'  Syntax ERROR ❌: {e}')
    all_pass = False

# === P0-5: atr_adaptive ===
print('\n=== P0-5: atr_adaptive in daily_runner.py ===')
with open('E:/quant/paper_trading/daily_runner.py') as f:
    content = f.read()
if "atr_adaptive': False" in content:
    print('  atr_adaptive = False ✅')
else:
    print('  atr_adaptive NOT changed ❌')
    all_pass = False

# === P0-4: stop-loss in full_backtest_v2.py ===
print('\n=== P0-4: stop-loss in full_backtest_v2.py ===')
with open('E:/quant/fusion_framework/full_backtest_v2.py') as f:
    content = f.read()
if "STOP_LOSS_PCT     = -0.12" in content:
    print('  stop_loss = -0.12 ✅')
else:
    print('  stop_loss NOT -0.12 ❌')
    all_pass = False

print(f'\n{"="*50}')
print(f'OVERALL: {"ALL 5 FIXES CONFIRMED ✅" if all_pass else "SOME FAILED ❌"}')
print(f'{"="*50}')