# -*- coding: utf-8 -*-
"""Sonnet 三周期回测 v2 — 方向A：扩充数据 + 去掉EMA缩短"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json, pandas as pd
import numpy as np
sys.path.insert(0, r'E:\quant\xmm-strategy')
from xmm_sonnet_model import XMMSonnetPrecomputed, TREND_LONG, TD_COMPLETE, TD_REF

CACHE = r'E:\quant\scanner\cache'

# ═══ 加载10年日线（周/月由模型内部重采样） ═══
with open(f'{CACHE}/SPY_daily_2500.json', 'r', encoding='utf-8') as f:
    df_daily = pd.DataFrame(json.load(f))
df_daily['trade_date'] = pd.to_datetime(df_daily['trade_date'])
df_daily.set_index('trade_date', inplace=True)
df_daily.sort_index(inplace=True)
# 确保数值类型
for c in ['open', 'high', 'low', 'close', 'volume']:
    df_daily[c] = pd.to_numeric(df_daily[c], errors='coerce')
df_daily = df_daily.dropna()
print(f"日线: {len(df_daily)}条, {df_daily.index[0].date()} ~ {df_daily.index[-1].date()}")

# ═══ 预计算 ═══
pc = XMMSonnetPrecomputed(df_daily, verbose=True)

WARMUP = max(TREND_LONG, TD_COMPLETE + TD_REF)
print(f"\n预热期: {WARMUP}天, 实际回测: {len(df_daily)-WARMUP}天")

# ═══ 信号统计 ═══
sig_counts = {}
monthly_status = {}
for i in range(WARMUP, len(df_daily)):
    sig = pc.get(i)
    s = sig['signal']
    sig_counts[s] = sig_counts.get(s, 0) + 1
    m = sig['monthly_trend']
    monthly_status[m] = monthly_status.get(m, 0) + 1

print(f"\n=== 信号分布 ===")
for k, v in sorted(sig_counts.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")
print(f"\n月线状态分布:")
for k, v in sorted(monthly_status.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

# ═══ 三重止损参数 ═══
STOP_LOSS_PCT     = -0.08   # 单笔固定止损 -8%
TRAILING_STOP_PCT = -0.08   # 移动止损 -8%（从持仓最高点回撤）
MAX_DRAWDOWN_PCT  = -0.25   # 组合最大回撤 -25%

# ── 可选冷却期（默认0=不启用）──
# 经 HK 回测验证：冷却期对所有标的都是负贡献，不建议启用
STOP_COOLDOWN_DAYS = 0  # 单笔止损后冷却天数

# ═══ 多笔持仓回测引擎 ═══
MAX_TOTAL_POSITION = 0.80   # 总仓位上限80%
MAX_SINGLE_TRADE   = 0.30   # 单笔上限30%

cash = 100000.0
positions = []     # [{entry_price, shares, highest, limit, date}]
trades = []
portfolio = []
portfolio_peak = 100000.0
stop_loss_count = 0
stop_cooldown_until = 0  # 止损后冷却期索引

def total_pos_value(positions, price):
    return sum(p['shares'] * price for p in positions)

def total_pos_ratio(positions, price, total_value):
    return total_pos_value(positions, price) / total_value if total_value > 0 else 0

for i in range(WARMUP, len(df_daily)):
    sig = pc.get(i)
    price = float(df_daily['close'].iloc[i])
    date = df_daily.index[i].date()

    pos_value = total_pos_value(positions, price)
    total = cash + pos_value
    portfolio_peak = max(portfolio_peak, total)
    portfolio.append({'date': str(date), 'value': total, 'signal': sig['signal'],
                      'limit': sig['position_limit'], 'price': price})

    # ── 止损检查（优先级最高）──
    stopped_positions = []
    remaining = []
    for p in positions:
        p['highest'] = max(p['highest'], price)
        pnl = (price - p['entry_price']) / p['entry_price']
        dd_from_peak = (price - p['highest']) / p['highest']
        should_stop = False
        reason = ''

        if pnl <= STOP_LOSS_PCT:
            should_stop = True
            reason = f'固定止损 PnL={pnl:.1%}'
        elif dd_from_peak <= TRAILING_STOP_PCT:
            should_stop = True
            reason = f'移动止损 从高点回撤={dd_from_peak:.1%}'

        if should_stop:
            cash += p['shares'] * price
            pnl_pct = (price - p['entry_price']) / p['entry_price'] * 100
            trades.append({'date': str(date), 'action': 'STOP_LOSS', 'price': price,
                           'shares': int(p['shares']), 'pnl_pct': round(pnl_pct, 2),
                           'reason': reason})
            stopped_positions.append(p)
            stop_loss_count += 1
            if STOP_COOLDOWN_DAYS > 0:
                stop_cooldown_until = i + STOP_COOLDOWN_DAYS
        else:
            remaining.append(p)

    # 组合回撤止损（不触发冷却期——这是系统性风险信号，恢复后应能立即重新入场）
    if remaining and portfolio_peak > 0:
        dd = (total - portfolio_peak) / portfolio_peak
        if dd <= MAX_DRAWDOWN_PCT:
            for p in remaining:
                cash += p['shares'] * price
                pnl_pct = (price - p['entry_price']) / p['entry_price'] * 100
                trades.append({'date': str(date), 'action': 'STOP_LOSS', 'price': price,
                               'shares': int(p['shares']), 'pnl_pct': round(pnl_pct, 2),
                               'reason': f'组合回撤止损 DD={dd:.1%}'})
                stop_loss_count += 1
            remaining = []
            # 不设冷却期（组合回撤是系统性信号，恢复后应能立即重新入场）

    positions = remaining

    # ── 信号驱动交易 ──
    action = sig['signal']
    limit = sig['position_limit']
    in_cooldown = STOP_COOLDOWN_DAYS > 0 and i < stop_cooldown_until

    if action in ('BUY', 'STRONG_BUY') and limit >= 0.05 and not in_cooldown:
        current_ratio = total_pos_ratio(positions, price, total)
        if current_ratio + min(limit, MAX_SINGLE_TRADE) <= MAX_TOTAL_POSITION:
            budget = total * min(limit, MAX_SINGLE_TRADE)
            buy_shares = int(budget / price)
            if buy_shares >= 1:
                cost = buy_shares * price
                cash -= cost
                positions.append({
                    'entry_price': price, 'shares': buy_shares,
                    'highest': price, 'limit': limit, 'date': str(date)
                })
                trades.append({'date': str(date), 'action': 'BUY', 'price': price,
                               'shares': buy_shares, 'limit': limit, 'reason': sig['layer2']})

    elif action in ('EXIT',) and positions:
        for p in positions:
            cash += p['shares'] * price
            pnl_pct = (price - p['entry_price']) / p['entry_price'] * 100
            trades.append({'date': str(date), 'action': 'SELL', 'price': price,
                           'shares': int(p['shares']), 'pnl_pct': round(pnl_pct, 2),
                           'reason': sig['layer2']})
        positions.clear()

    elif action == 'REDUCE' and positions:
        # 每笔持仓减半
        remaining = []
        for p in positions:
            sell_shares = int(p['shares'] * 0.5)
            if sell_shares >= 1:
                cash += sell_shares * price
                trades.append({'date': str(date), 'action': 'REDUCE', 'price': price,
                               'shares': sell_shares, 'reason': sig['layer2']})
                p['shares'] -= sell_shares
            if p['shares'] > 0:
                remaining.append(p)
        positions = remaining

# 最终平仓
if positions:
    price = float(df_daily['close'].iloc[-1])
    date = df_daily.index[-1].date()
    for p in positions:
        pnl_pct = (price - p['entry_price']) / p['entry_price'] * 100
        cash += p['shares'] * price
        trades.append({'date': str(date), 'action': 'SELL', 'price': price,
                       'shares': int(p['shares']), 'pnl_pct': round(pnl_pct, 2), 'reason': '期末平仓'})
    positions.clear()

# ═══ 结果统计 ═══
final = cash
ret = (final - 100000) / 100000 * 100

# 最大回撤
max_dd = 0.0
peak = 100000.0
for p in portfolio:
    if p['value'] > peak:
        peak = p['value']
    dd = (peak - p['value']) / peak * 100
    if dd > max_dd:
        max_dd = dd

# 基准（买入持有）
entry = float(df_daily['close'].iloc[WARMUP])
exit_p = float(df_daily['close'].iloc[-1])
benchmark = (exit_p - entry) / entry * 100

# 夏普比率（日收益）
vals = [p['value'] for p in portfolio]
daily_rets = np.diff(vals) / vals[:-1]
sharpe = np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252) if np.std(daily_rets) > 0 else 0

# 胜率（含止损平仓）
sell_trades = [t for t in trades if t['action'] in ('SELL', 'STOP_LOSS') and 'pnl_pct' in t]
wins = sum(1 for t in sell_trades if t['pnl_pct'] > 0)
win_rate = wins / len(sell_trades) * 100 if sell_trades else 0
stop_loss_trades = [t for t in trades if t['action'] == 'STOP_LOSS']

print(f'\n{"="*60}')
print(f'=== Sonnet 三周期回测 (止损版 + 多笔持仓) ===')
print(f'{"="*60}')
print(f'策略收益:     {ret:+.2f}%')
print(f'基准收益:     {benchmark:+.2f}%')
print(f'超额Alpha:    {ret - benchmark:+.2f}%')
print(f'夏普比率:     {sharpe:.2f}')
print(f'最大回撤:     -{max_dd:.2f}%')
print(f'交易次数:     {len([t for t in trades if t["action"]=="BUY"])} 买 / {len(sell_trades)} 卖')
print(f'其中止损:     {stop_loss_count}次')
print(f'胜率:         {win_rate:.1f}%')

if sell_trades:
    pnls = [t['pnl_pct'] for t in sell_trades]
    avg_win = np.mean([p for p in pnls if p > 0]) if any(p > 0 for p in pnls) else 0
    avg_loss = np.mean([p for p in pnls if p < 0]) if any(p < 0 for p in pnls) else 0
    print(f'平均盈利:   +{avg_win:.2f}%')
    print(f'平均亏损:   {avg_loss:.2f}%')
    print(f'盈亏比:     {abs(avg_win/avg_loss):.2f}' if avg_loss != 0 else '盈亏比: N/A')

print(f'\n--- 交易明细 ---')
for t in trades:
    sh = int(t['shares'])
    if 'pnl_pct' in t:
        print(f"  {t['date']} {t['action']:6s} {sh:>5d}股 @{t['price']:>8.2f}  PnL:{t['pnl_pct']:+.2f}%  {t['reason']}")
    else:
        print(f"  {t['date']} {t['action']:6s} {sh:>5d}股 @{t['price']:>8.2f}  limit={t.get('limit','?')}  {t.get('reason','')}")

# 保存结果
result = {
    'strategy_return': round(ret, 2),
    'benchmark_return': round(benchmark, 2),
    'alpha': round(ret - benchmark, 2),
    'sharpe': round(sharpe, 2),
    'max_drawdown': round(max_dd, 2),
    'trades': trades,
    'sig_counts': sig_counts,
    'monthly_status': monthly_status,
}
with open(r'E:\quant\output\sonnet_backtest_v2.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f'\n结果已保存: E:\\quant\\output\\sonnet_backtest_v2.json')
