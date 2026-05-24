# -*- coding: utf-8 -*-
"""
XMM 策略执行层 — 持仓模拟 + 绩效统计 + 报告输出
=============================================
回测层与执行层严格分离：
  - 信号层  → signal_api.get_signal()（只读接口）
  - 执行层  → simulate_trades.py（本文件，纯规则，零 LLM）
  - 回测层  → backtest_*.py（调用本模块的函数）

用法:
    from simulate_trades import run_backtest, simulate_trades, calc_stats
    results = run_scan(df)  # 用 signal_api 全历史扫描
    trades, eq_curve, daily_returns = simulate_trades(results)
    stats = calc_stats(trades, eq_curve, daily_returns, results)
    print_report(results, trades, eq_curve, stats)
"""
import sys, os, math
import pandas as pd
import numpy as np
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_api import get_signal


# ═══════════════════════════════════════════════════════════════
# 数据获取
# ═══════════════════════════════════════════════════════════════

def fetch_kline(futu_code: str, days: int = 730) -> pd.DataFrame:
    """从 Futu 获取日 K 线数据，返回标准 DataFrame

    Args:
        futu_code: Futu 股票代码，如 'HK.09988' / 'US.SPY'
        days: 回溯天数，默认 730（2年）

    Returns:
        DataFrame，列: open/high/low/close/volume，index: trade_date
    """
    from futu import OpenQuoteContext
    sys.path.insert(0, 'E:/quant')
    import config
    ctx = OpenQuoteContext(host=config.FUTU_HOST, port=config.FUTU_PORT)
    try:
        end_dt = datetime.now().strftime('%Y-%m-%d')
        start_dt = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        ret, data, page_key = ctx.request_history_kline(
            futu_code, start=start_dt, end=end_dt, ktype='K_DAY', autype=None
        )
        if ret != 0 or not isinstance(data, pd.DataFrame) or len(data) < 50:
            return None
        data = data.sort_values('time_key')
        df = pd.DataFrame()
        df['trade_date'] = pd.to_datetime(data['time_key'])
        df['open'] = data['open'].astype(float)
        df['high'] = data['high'].astype(float)
        df['low'] = data['low'].astype(float)
        df['close'] = data['close'].astype(float)
        df['volume'] = data['volume'].astype(float)
        df.set_index('trade_date', inplace=True)
        return df
    finally:
        ctx.close()


# ═══════════════════════════════════════════════════════════════
# 信号扫描
# ═══════════════════════════════════════════════════════════════

def run_scan(df: pd.DataFrame, lookback: int = 60) -> list:
    """全历史逐日扫描，对每一天调用 signal_api.get_signal()

    Args:
        df: OHLCV DataFrame
        lookback: 初始数据预热天数（默认60，引擎需要至少60条）

    Returns:
        list[dict]: 每一条目包含 signal / position_size / close / date 等
    """
    results = []
    for i in range(lookback, len(df)):
        window = df.iloc[:i+1].copy()
        sig = get_signal(window)
        sig['date'] = str(df.index[i])[:10]
        sig['close'] = float(df['close'].iloc[i])
        results.append(sig)
    return results


# ═══════════════════════════════════════════════════════════════
# 持仓模拟（执行层核心）
# ═══════════════════════════════════════════════════════════════

def simulate_trades(results: list) -> tuple:
    """模拟持仓管理逻辑，逐日更新仓位和净值

    规则:
      - UP 趋势：持有 100%。SELL 信号按 position_size 比例减仓。
      - DOWN 趋势：空仓 0%。BUY 信号轻仓试错（position_size）。
      - SIDEWAYS：空仓观望。BUY/SELL 按 position_size 轻仓试探。
      - 交易定义：从建仓(>0)到清仓(=0)为一笔完整交易。

    Args:
        results: run_scan() 的输出

    Returns:
        (trades, equity_curve, daily_returns)
        - trades:       list[dict] 每笔交易记录
        - equity_curve: list[dict] 每日净值/仓位/操作
        - daily_returns: list[float] 日收益率序列
    """
    position = 0.0       # 当前仓位 0~1.0
    equity = 1.0         # 净值曲线（起始1.0）
    prev_close = None
    equity_curve = []
    trades = []
    in_trade = False
    trade_entry_idx = 0
    trade_entry_price = 0.0
    trade_entry_position = 0.0
    daily_returns = []

    for idx, r in enumerate(results):
        trend = r.get('trend_layer', {}).get('market', 'UNKNOWN')
        signal = r['signal']
        pos_target = r['position_size']
        close = r['close']

        # 日收益率计算
        if prev_close is not None and prev_close > 0:
            daily_ret = close / prev_close - 1
        else:
            daily_ret = 0.0

        # ── 仓位管理 ─────────────────────────────────
        old_position = position

        if trend == 'UP':
            if signal == 'SELL':
                reduction = position * pos_target
                position -= reduction
                action_desc = f"减仓{pos_target:.0%}"
            elif signal == 'BUY':
                position = pos_target
                action_desc = f"买入{pos_target:.0%}"
            else:  # HOLD
                if position == 0.0:
                    position = 1.0
                    action_desc = "建仓100%"
                else:
                    action_desc = "持有"

        elif trend == 'DOWN':
            if signal == 'BUY':
                position = pos_target
                action_desc = f"轻仓试错{pos_target:.0%}"
            else:
                if position > 0:
                    action_desc = "清仓(转DOWN)"
                else:
                    action_desc = "空仓"
                position = 0.0

        else:  # SIDEWAYS
            if signal == 'BUY':
                position = pos_target
                action_desc = f"震荡买入{pos_target:.0%}"
            elif signal == 'SELL':
                position = 0.0
                action_desc = "震荡清仓"
            else:
                if position > 0:
                    action_desc = "清仓(转震荡)"
                else:
                    action_desc = "空仓观望"
                position = 0.0

        # ── 交易记录 ─────────────────────────────────
        if old_position == 0.0 and position > 0.0 and not in_trade:
            in_trade = True
            trade_entry_idx = idx
            trade_entry_price = close
            trade_entry_position = position

        if old_position > 0.0 and position == 0.0 and in_trade:
            in_trade = False
            avg_pos = (trade_entry_position + old_position) / 2
            trade_return = (close / trade_entry_price - 1) * avg_pos * 100
            trades.append({
                'entry_date': results[trade_entry_idx]['date'],
                'exit_date': r['date'],
                'entry_price': trade_entry_price,
                'exit_price': close,
                'price_change_pct': (close / trade_entry_price - 1) * 100,
                'avg_position': avg_pos,
                'trade_return_pct': trade_return,
                'profitable': trade_return > 0,
            })

        # ── 净值增长 ─────────────────────────────────
        if prev_close is not None:
            if position > 0:
                daily_pnl_pct = daily_ret * position
                equity *= (1 + daily_pnl_pct)
                daily_returns.append(daily_pnl_pct)
            else:
                daily_returns.append(0.0)

        equity_curve.append({
            'date': r['date'],
            'equity': equity,
            'position': position,
            'close': close,
            'action': action_desc,
            'signal': signal,
            'trend': trend,
        })
        prev_close = close

    return trades, equity_curve, daily_returns


# ═══════════════════════════════════════════════════════════════
# 绩效统计
# ═══════════════════════════════════════════════════════════════

def calc_stats(trades: list, equity_curve: list,
               daily_returns: list, results: list) -> dict:
    """计算关键绩效指标

    Returns:
        dict: 含 buy_hold_return / strategy_return / annual_return /
              max_drawdown / sharpe_ratio / n_trades / win_rate /
              avg_win_pct / avg_loss_pct / profit_factor
    """
    first_close = results[0]['close']
    last_close = results[-1]['close']
    buy_hold_ret = (last_close / first_close - 1) * 100

    final_equity = equity_curve[-1]['equity']
    strategy_ret = (final_equity - 1.0) * 100

    eq_values = [e['equity'] for e in equity_curve]
    peak = np.maximum.accumulate(eq_values)
    drawdowns = (np.array(eq_values) / peak - 1) * 100
    max_dd = drawdowns.min()

    n_days = len(daily_returns)
    years = n_days / 252
    annual_ret = (final_equity ** (1 / years) - 1) * 100 if years > 0 else 0

    rf = 0.02 / 252
    excess_returns = np.array(daily_returns) - rf
    if len(excess_returns) > 0 and np.std(excess_returns) > 0:
        sharpe = np.sqrt(252) * np.mean(excess_returns) / np.std(excess_returns)
    else:
        sharpe = 0

    if trades:
        wins = sum(1 for t in trades if t['profitable'])
        total = len(trades)
        win_rate = wins / total * 100
        avg_win = np.mean([t['trade_return_pct'] for t in trades if t['profitable']]) if wins > 0 else 0
        avg_loss = np.mean([t['trade_return_pct'] for t in trades if not t['profitable']]) if (total - wins) > 0 else 0
        profit_factor = abs(
            sum(t['trade_return_pct'] for t in trades if t['profitable']) /
            sum(t['trade_return_pct'] for t in trades if not t['profitable'])
        ) if any(not t['profitable'] for t in trades) else float('inf')
    else:
        wins = total = win_rate = avg_win = avg_loss = profit_factor = 0

    return {
        'buy_hold_return': buy_hold_ret,
        'strategy_return': strategy_ret,
        'annual_return': annual_ret,
        'max_drawdown': max_dd,
        'sharpe_ratio': sharpe,
        'n_trades': total,
        'win_rate': win_rate,
        'avg_win_pct': avg_win,
        'avg_loss_pct': avg_loss,
        'profit_factor': profit_factor,
        'n_days': n_days,
    }


# ═══════════════════════════════════════════════════════════════
# 报告输出
# ═══════════════════════════════════════════════════════════════

def print_report(results: list, trades: list,
                 eq_curve: list, stats: dict,
                 title: str = "XMM策略回测报告") -> None:
    """打印完整回测报告"""
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)

    print(f"\n📅 回测区间: {results[0]['date']} ~ {results[-1]['date']}  ({stats['n_days']}天)")
    print(f"📊 价格: ${results[0]['close']:.2f} → ${results[-1]['close']:.2f}")
    print(f"  买入持有收益: {stats['buy_hold_return']:+.2f}%")

    print(f"\n{'='*72}")
    print(f"📈 策略绩效")
    print(f"{'='*72}")
    print(f"  策略总收益:     {stats['strategy_return']:>+8.2f}%")
    print(f"  年化收益:       {stats['annual_return']:>+8.2f}%")
    print(f"  最大回撤:       {stats['max_drawdown']:>8.2f}%")
    print(f"  夏普比率:       {stats['sharpe_ratio']:>8.2f}")
    print(f"  买入持有收益:   {stats['buy_hold_return']:>+8.2f}%   (基准)")

    print(f"\n{'='*72}")
    print(f"🎯 交易统计")
    print(f"{'='*72}")
    print(f"  交易次数:       {stats['n_trades']:>8d}")
    print(f"  胜率:           {stats['win_rate']:>8.1f}%")
    print(f"  平均盈利:       {stats['avg_win_pct']:>+8.2f}%  (每笔)")
    print(f"  平均亏损:       {stats['avg_loss_pct']:>+8.2f}%  (每笔)")
    print(f"  盈亏比:         {stats['profit_factor']:>8.2f}")

    if trades:
        print(f"\n{'='*72}")
        print(f"📋 交易明细 ({len(trades)}笔)")
        print(f"{'='*72}")
        print(f"{'入场':<12}{'出场':<12}{'入场价':>8}{'出场价':>8}{'涨幅':>8}{'仓位':>7}{'收益':>8}{'结果':>5}")
        print("-" * 72)
        for t in trades:
            result_mark = '✅' if t['profitable'] else '❌'
            print(f"{t['entry_date']:<12}{t['exit_date']:<12}{t['entry_price']:>8.2f}"
                  f"{t['exit_price']:>8.2f}{t['price_change_pct']:>+7.2f}%"
                  f"{t['avg_position']:>6.0%}{t['trade_return_pct']:>+7.2f}%{result_mark:>5}")

    # 月度收益
    print(f"\n{'='*72}")
    print(f"📅 月度收益率")
    print(f"{'='*72}")
    monthly = {}
    for e in eq_curve:
        m = e['date'][:7]
        if m not in monthly:
            monthly[m] = {
                'first_eq': e['equity'], 'last_eq': e['equity'],
                'first_close': e['close'], 'last_close': e['close'],
            }
        monthly[m]['last_eq'] = e['equity']
        monthly[m]['last_close'] = e['close']
    for m in sorted(monthly.keys()):
        d = monthly[m]
        m_ret_strategy = (d['last_eq'] / d['first_eq'] - 1) * 100
        m_ret_bh = (d['last_close'] / d['first_close'] - 1) * 100
        mark = '🟢' if m_ret_strategy >= 0 else '🔴'
        print(f"  {m}  {mark} 策略:{m_ret_strategy:>+7.2f}%  基准:{m_ret_bh:>+7.2f}%")

    # 关键信号节点
    print(f"\n{'='*72}")
    print(f"📌 关键信号与持仓变化")
    print(f"{'='*72}")
    key_events = [
        e for e in eq_curve
        if e['position'] > 0 or '建仓' in e['action']
        or '清仓' in e['action'] or '减仓' in e['action']
    ]
    shown = set()
    for e in key_events:
        key = f"{e['date']}_{e['action']}"
        if key not in shown:
            shown.add(key)
            pos_sign = '⬆' if e['position'] > 0 else '⬇'
            print(f"  {e['date']}  {pos_sign} "
                  f"仓位={e['position']:.0%}  "
                  f"信号={e['signal']:<5}  "
                  f"趋势={e['trend']:<8}  "
                  f"{e['action']}")
            if e['position'] > 0:
                print(f"         收盘=${e['close']:.2f}  净值={e['equity']:.4f}")


# ═══════════════════════════════════════════════════════════════
# 一键回测入口
# ═══════════════════════════════════════════════════════════════

def run_backtest(futu_code: str, name: str = "", days: int = 730,
                 lookback: int = 60) -> dict:
    """一键运行完整回测：获取数据 → 信号扫描 → 持仓模拟 → 绩效统计

    Args:
        futu_code: 股票代码，如 'HK.09988'
        name:      显示名称，如 '阿里巴巴'
        days:      数据回溯天数
        lookback:  信号预热天数

    Returns:
        dict: 包含 results / trades / equity_curve / stats
              或 None（数据获取失败）
    """
    code_short = futu_code.replace('.', '_')
    title = f"{code_short} ({name}) XMM策略回测" if name else f"{code_short} XMM策略回测"

    print(f"获取 {futu_code} 数据...")
    df = fetch_kline(futu_code, days=days)
    if df is None:
        print(f"[FAIL] 无法获取 {futu_code} 数据")
        return None

    print(f"数据: {len(df)}条  {str(df.index[0])[:10]} ~ {str(df.index[-1])[:10]}")

    print("运行 XMM 信号扫描...")
    results = run_scan(df, lookback=lookback)
    print(f"扫描完成: {len(results)}天")

    print("模拟持仓管理...")
    trades, eq_curve, daily_returns = simulate_trades(results)

    print("计算绩效...")
    stats = calc_stats(trades, eq_curve, daily_returns, results)

    print_report(results, trades, eq_curve, stats, title=title)

    return {
        'results': results,
        'trades': trades,
        'equity_curve': eq_curve,
        'daily_returns': daily_returns,
        'stats': stats,
    }


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='XMM 策略回测执行层')
    parser.add_argument('code', nargs='?', default='HK.09988',
                        help='Futu 股票代码，如 HK.09988')
    parser.add_argument('--name', default='', help='显示名称')
    parser.add_argument('--days', type=int, default=730, help='数据回溯天数')
    parser.add_argument('--lookback', type=int, default=60, help='信号预热天数')
    args = parser.parse_args()

    run_backtest(args.code, name=args.name,
                 days=args.days, lookback=args.lookback)