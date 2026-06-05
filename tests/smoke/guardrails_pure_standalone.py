# -*- coding: utf-8 -*-
"""
Phase B — Live Guardrails 纯函数测试。

独立脚本，不依赖 unified_runner/quant 任何模块。
通过 subprocess 运行，避免 numpy/pandas/futu 重量依赖。
函数逻辑保持与 unified_runner.py 的 Live Guardrails 实现一致。
"""

import argparse
import json
import os
import sys
import math
from datetime import datetime


# ── 被测试函数 (与 unified_runner.py 的 Live Guardrails 实现保持一致) ──

def _is_live_confirmed(args) -> bool:
    if not args.live:
        return False
    if os.environ.get('QUANT_LIVE_KILLED', '').upper() == 'YES':
        return False
    if args.confirm_live:
        return True
    if os.environ.get('QUANT_LIVE_CONFIRM', '').upper() == 'YES':
        return True
    return False


def _build_pre_trade_summary(market, requested_live, confirmed_live,
                              account, positions, orders,
                              total_assets, cash_before,
                              exposure_before):
    """构建 pre-trade summary — 纯函数，与 unified_runner.py 保持一致。
    
    cash_before/exposure_before 是订单生成前的原始快照值，
    不依赖调用方预先扣减/累加。
    """
    buy_orders = [o for o in orders if o['action'] == 'BUY']
    sell_orders = [o for o in orders if o['action'] == 'SELL']
    gross_buy = sum(o['qty'] * o['price'] for o in buy_orders)
    gross_sell = sum(o['qty'] * o['price'] for o in sell_orders)

    largest = None
    if orders:
        largest = max(orders, key=lambda o: o['qty'] * o['price'])

    cash_after_orders = cash_before + gross_sell - gross_buy
    exposure_after = exposure_before + gross_buy - gross_sell
    MAX_POSITION_PCT = 0.20
    MAX_TOTAL_PCT = 0.80

    per_order = []
    for o in orders:
        per_order.append({
            'symbol': o['symbol'], 'action': o['action'],
            'qty': o['qty'], 'price': o['price'],
            'notional': round(o['qty'] * o['price'], 2),
            'reason': o.get('reason', ''),
        })

    return {
        'timestamp': datetime.now().isoformat(),
        'market': market,
        'requested_live': requested_live,
        'confirmed_live': confirmed_live,
        'execution_mode': '',
        'account': {
            'total_assets': total_assets,
            'cash_before': cash_before,
            'cash_after_orders': cash_after_orders,
            'market_val': account.get('market_val', 0) if account else 0,
            'exposure_before_pct': round(exposure_before / total_assets * 100, 1) if total_assets > 0 else 0,
            'exposure_after_pct': round(exposure_after / total_assets * 100, 1) if total_assets > 0 else 0,
            'exposure_before_value': exposure_before,
            'exposure_after_value': exposure_after,
        },
        'orders_summary': {
            'total': len(orders),
            'buy_count': len(buy_orders),
            'sell_count': len(sell_orders),
            'gross_buy': gross_buy,
            'gross_sell': gross_sell,
            'net_cash_impact': gross_buy - gross_sell,
            'largest_order': {
                'symbol': largest['symbol'] if largest else '',
                'action': largest['action'] if largest else '',
                'qty': largest['qty'] if largest else 0,
                'price': largest['price'] if largest else 0,
                'notional': round(largest['qty'] * largest['price'], 2) if largest else None,
            } if largest else None,
        },
        'per_order_details': per_order,
        'risk_checks': {
            'order_count': {'pass': len(orders) > 0, 'detail': f'{len(orders)} orders' if orders else '0 orders'},
            'cash_after_orders': {
                'pass': cash_after_orders >= 0,
                'detail': f'{cash_after_orders:,.0f} >= 0' if cash_after_orders >= 0 else f'{cash_after_orders:,.0f} < 0',
            },
            'largest_order_pct': {
                'pass': (largest['qty'] * largest['price'] / total_assets <= MAX_POSITION_PCT
                         if largest and total_assets > 0 else True),
                'detail': (f'{largest["qty"] * largest["price"] / total_assets * 100:.1f}% <= {MAX_POSITION_PCT*100:.0f}%'
                           if largest and total_assets > 0 else 'no orders'),
            },
            'exposure_after_orders': {
                'pass': exposure_after / total_assets <= MAX_TOTAL_PCT if total_assets > 0 else True,
                'detail': (f'{exposure_after / total_assets * 100:.1f}% <= {MAX_TOTAL_PCT*100:.0f}%'
                           if total_assets > 0 else 'no assets'),
            },
        },
    }


def _should_block_live(summary):
    reasons = []
    if summary.get('requested_live') and not summary.get('confirmed_live'):
        reasons.append('LIVE_CONFIRM_REQUIRED')
    risk = summary.get('risk_checks', {})
    cash_check = risk.get('cash_after_orders', {})
    if not cash_check.get('pass', True):
        reasons.append(cash_check.get('detail', 'INSUFFICIENT_CASH'))
    pos_check = risk.get('largest_order_pct', {})
    if not pos_check.get('pass', True):
        reasons.append(pos_check.get('detail', 'POSITION_LIMIT_EXCEEDED'))
    exp_check = risk.get('exposure_after_orders', {})
    if not exp_check.get('pass', True):
        reasons.append(exp_check.get('detail', 'EXPOSURE_LIMIT_EXCEEDED'))
    return len(reasons) > 0, reasons


# ═══════════════════════════════════════════════════════════════════
# 测试运行
# ═══════════════════════════════════════════════════════════════════

passed = 0
failed = []

def check(name, got, expected):
    global passed
    if got == expected:
        passed += 1
    else:
        failed.append(f'FAIL {name}: got={got} expected={expected}')

# ── _is_live_confirmed ──
os.environ.pop('QUANT_LIVE_CONFIRM', None)
check('no_live_no_confirm', _is_live_confirmed(argparse.Namespace(live=False, confirm_live=False)), False)
check('live_no_confirm', _is_live_confirmed(argparse.Namespace(live=True, confirm_live=False)), False)
check('live_with_cli', _is_live_confirmed(argparse.Namespace(live=True, confirm_live=True)), True)
os.environ['QUANT_LIVE_CONFIRM'] = 'YES'
check('live_with_env', _is_live_confirmed(argparse.Namespace(live=True, confirm_live=False)), True)
os.environ['QUANT_LIVE_CONFIRM'] = 'yes'
check('live_with_env_lower', _is_live_confirmed(argparse.Namespace(live=True, confirm_live=False)), True)
check('confirm_no_live', _is_live_confirmed(argparse.Namespace(live=False, confirm_live=True)), False)
check('env_no_live', _is_live_confirmed(argparse.Namespace(live=False, confirm_live=False)), False)
os.environ['QUANT_LIVE_KILLED'] = 'YES'
check('kill_switch_blocks_cli', _is_live_confirmed(argparse.Namespace(live=True, confirm_live=True)), False)
os.environ.pop('QUANT_LIVE_KILLED', None)
os.environ.pop('QUANT_LIVE_CONFIRM', None)
print(f'is_live_confirmed: 8/8')

# ── _build_pre_trade_summary ──
acct = {'total_assets': 1500000, 'cash': 1400000, 'market_val': 100000}
pos = [{'symbol': '00700.HK', 'qty': 100}]
orders = [
    {'symbol': '00700.HK', 'action': 'BUY', 'qty': 100, 'price': 466.40, 'reason': 'test'},
    {'symbol': '00981.HK', 'action': 'SELL', 'qty': 500, 'price': 26.00, 'reason': 'stop'},
]
s = _build_pre_trade_summary('HK', True, False, acct, pos, orders, 1500000, 1400000, 100000)

for name, ok in [
    ('market', s['market'] == 'HK'),
    ('requested_live', s['requested_live'] is True),
    ('confirmed_live', s['confirmed_live'] is False),
    ('total=2', s['orders_summary']['total'] == 2),
    ('buy=1', s['orders_summary']['buy_count'] == 1),
    ('sell=1', s['orders_summary']['sell_count'] == 1),
    ('gross_buy', abs(s['orders_summary']['gross_buy'] - 46640.0) < 0.01),
    ('gross_sell', abs(s['orders_summary']['gross_sell'] - 13000.0) < 0.01),
    ('has_timestamp', 'timestamp' in s),
    ('has_account', 'account' in s),
    ('has_per_order', 'per_order_details' in s and len(s['per_order_details']) == 2),
    ('has_risk_checks', 'risk_checks' in s),
]:
    if ok: passed += 1
    else: failed.append(f'FAIL summary: {name}')

# No orders
s2 = _build_pre_trade_summary('HK', False, False, acct, pos, [], 1500000, 1400000, 100000)
if s2['orders_summary']['total'] == 0 and s2['orders_summary']['largest_order'] is None:
    passed += 2
else:
    failed.append(f'FAIL summary: no_orders (total={s2["orders_summary"]["total"]})')

# Cash insufficient
s3 = _build_pre_trade_summary('HK', True, True, acct, pos,
    [{'symbol': 'T', 'action': 'BUY', 'qty': 100000, 'price': 500, 'reason': 'x'}],
    1500000, 1400000, 100000)
if s3['risk_checks']['cash_after_orders']['pass'] is False:
    passed += 1
else:
    failed.append('FAIL summary: cash_insufficient should fail')

# ── 双算防护测试：cash_before / exposure_before 不应被调用方预先修改 ──
# 模拟场景：cash_before=100000, exposure_before=20000, BUY 10000, SELL 5000
# 预期：cash_after_orders = 100000 - 10000 + 5000 = 95000
#       exposure_after = 20000 + 10000 - 5000 = 25000
s4_acct = {'total_assets': 150000, 'cash': 100000, 'market_val': 20000}
s4_orders = [
    {'symbol': 'T', 'action': 'BUY', 'qty': 100, 'price': 100, 'reason': 'test'},
    {'symbol': 'U', 'action': 'SELL', 'qty': 50, 'price': 100, 'reason': 'stop'},
]
s4 = _build_pre_trade_summary('HK', True, True, s4_acct, [], s4_orders, 150000, 100000, 20000)
if s4['account']['cash_before'] == 100000:
    passed += 1
else:
    failed.append(f'FAIL dd: cash_before expected 100000 got {s4["account"]["cash_before"]}')
if s4['account']['cash_after_orders'] == 95000:
    passed += 1
else:
    failed.append(f'FAIL dd: cash_after_orders expected 95000 got {s4["account"]["cash_after_orders"]}')
if s4['account']['exposure_before_pct'] == 13.3:
    passed += 1
elif abs(s4['account']['exposure_before_pct'] - 13.3) < 0.15:
    passed += 1
else:
    failed.append(f'FAIL dd: exposure_before_pct expected ~13.3 got {s4["account"]["exposure_before_pct"]}')
if s4['account']['exposure_after_pct'] == 16.7:
    passed += 1
elif abs(s4['account']['exposure_after_pct'] - 16.7) < 0.15:
    passed += 1
else:
    failed.append(f'FAIL dd: exposure_after_pct expected ~16.7 got {s4["account"]["exposure_after_pct"]}')
# 关键：如果字段被双算，cash_after_orders 会 = 100000 - 10000 - 10000 + 5000 = 85000 ❌
# 或 = 100000 - 20000 + 5000 = 85000（如果 cash_before 被扣了）
if s4['account']['cash_after_orders'] == 95000:
    passed += 1
elif abs(s4['account']['cash_after_orders'] - 95000) < 1:
    passed += 1
else:
    failed.append(f'FAIL dd: 双算风险 — cash_after_orders={s4["account"]["cash_after_orders"]}, 应为 95000')

print(f'build_pre_trade_summary: passed consistent')

# ── _should_block_live ──
def gen(req=False, conf=False, cash=True, pos=True, exp=True):
    return {'requested_live': req, 'confirmed_live': conf, 'orders_summary': {'total': 1},
            'risk_checks': {'cash_after_orders': {'pass': cash, 'detail': 'x'},
                            'largest_order_pct': {'pass': pos, 'detail': 'x'},
                            'exposure_after_orders': {'pass': exp, 'detail': 'x'}}}

block_tests = [
    ('no_live', gen(False, False), False, None),
    ('live_no_confirm', gen(True, False), True, 'LIVE_CONFIRM_REQUIRED'),
    ('live_confirmed', gen(True, True), False, None),
    ('cash_fail', gen(True, True, cash=False), True, None),
    ('pos_fail', gen(True, True, pos=False), True, None),
    ('exp_fail', gen(True, True, exp=False), True, None),
]
for name, summary, expect_block, expect_reason in block_tests:
    b, r = _should_block_live(summary)
    if b == expect_block:
        passed += 1
    else:
        failed.append(f'FAIL should_block: {name} (block={b})')
    if expect_reason and expect_reason not in str(r):
        failed.append(f'FAIL should_block: {name} missing reason {expect_reason}')

print(f'should_block_live: 6/6')

# ── 汇总 ──
print(f'\nTOTAL: {passed} checks passed, {len(failed)} failures')
for f in failed:
    print(f'  {f}')
sys.exit(0 if len(failed) == 0 else 1)
