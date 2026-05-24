# -*- coding: utf-8 -*-
"""
xmm_backtest_unified.py — 通用 XMM 策略回测（任意港股标的）

用法:
    python xmm_backtest_unified.py --ticker 00700.HK
    python xmm_backtest_unified.py --ticker 03690.HK
    python xmm_backtest_unified.py --ticker 09988.HK  --name 阿里巴巴
"""
import sys, io, argparse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))   # E:\quant
sys.path.insert(0, str(BASE))          # E:\quant\xmm-strategy

import config
from modules.engine import XMMStrategy


# ═══════════════════════════════════════════════════════════════
# 1. 获取数据
# ═══════════════════════════════════════════════════════════════
def fetch_data(ticker: str, days: int = 730):
    """
    从 Futu 获取日 K 线数据。

    ticker: 标准格式如 00700.HK，内部转 Futu 格式 HK.00700
    """
    # 标准符号 → Futu 代码
    parts = ticker.split('.')
    if len(parts) != 2:
        raise ValueError(f"ticker 格式错误: {ticker}，应为 TICKER.MARKET")
    futu_code = f"{parts[1]}.{parts[0]}"

    from futu import OpenQuoteContext
    ctx = OpenQuoteContext(host=config.FUTU_HOST, port=config.FUTU_PORT)
    try:
        end_dt = datetime.now().strftime('%Y-%m-%d')
        start_dt = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        ret, data, page_key = ctx.request_history_kline(
            futu_code, start=start_dt, end=end_dt, ktype='K_DAY',
            autype='qfq'  # ⚠️ 必须前复权！autype=None 会导致拆股假信号
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
# 2. 全历史 XMM 信号扫描
# ═══════════════════════════════════════════════════════════════
def run_scan(df, lookback=60):
    engine = XMMStrategy(short_period=25, long_period=90)
    results = []
    for i in range(lookback, len(df)):
        window = df.iloc[:i+1].copy()
        sig = engine.analyze(window)
        sig['date'] = str(df.index[i])[:10]
        sig['close'] = float(df['close'].iloc[i])
        results.append(sig)
    return results


# ═══════════════════════════════════════════════════════════════
# 3. 持仓模拟
# ═══════════════════════════════════════════════════════════════
def simulate_trades(results):
    position = 0.0
    equity = 1.0
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
        date = r['date']

        if prev_close is not None and prev_close > 0:
            daily_ret = close / prev_close - 1
        else:
            daily_ret = 0.0

        old_position = position

        if trend == 'UP':
            if signal == 'SELL':
                reduction = position * pos_target
                position -= reduction
                action_desc = f"减仓{pos_target:.0%}"
            elif signal == 'BUY':
                position = pos_target
                action_desc = f"买入{pos_target:.0%}"
            else:
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

        # 交易记录
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
                'exit_date': date,
                'entry_price': trade_entry_price,
                'exit_price': close,
                'price_change_pct': (close / trade_entry_price - 1) * 100,
                'avg_position': avg_pos,
                'trade_return_pct': trade_return,
                'profitable': trade_return > 0,
            })

        # 净值
        if prev_close is not None:
            if position > 0:
                daily_pnl_pct = daily_ret * position
                equity *= (1 + daily_pnl_pct)
                daily_returns.append(daily_pnl_pct)
            else:
                daily_returns.append(0.0)

        equity_curve.append({'date': date, 'equity': equity, 'position': position,
                             'close': close, 'action': action_desc,
                             'signal': signal, 'trend': trend})
        prev_close = close

    return trades, equity_curve, daily_returns


# ═══════════════════════════════════════════════════════════════
# 4. 绩效统计
# ═══════════════════════════════════════════════════════════════
def calc_stats(trades, equity_curve, daily_returns, results):
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
    sharpe = (np.sqrt(252) * np.mean(excess_returns) / np.std(excess_returns)
              if len(excess_returns) > 0 and np.std(excess_returns) > 0 else 0)

    if trades:
        wins = sum(1 for t in trades if t['profitable'])
        total = len(trades)
        win_rate = wins / total * 100
        avg_win = np.mean([t['trade_return_pct'] for t in trades if t['profitable']]) if wins > 0 else 0
        avg_loss = np.mean([t['trade_return_pct'] for t in trades if not t['profitable']]) if (total - wins) > 0 else 0
        profit_factor = (abs(sum(t['trade_return_pct'] for t in trades if t['profitable']) /
                            sum(t['trade_return_pct'] for t in trades if not t['profitable']))
                         if any(not t['profitable'] for t in trades) else float('inf'))
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
# 5. 报告输出
# ═══════════════════════════════════════════════════════════════
def print_report(results, trades, eq_curve, stats, ticker, name):
    sep = '=' * 72
    print(f"\n{sep}")
    print(f"  {ticker}（{name}） XMM策略回测报告")
    print(f"{sep}")

    print(f"\n📅 回测区间: {results[0]['date']} ~ {results[-1]['date']}  ({stats['n_days']}天)")
    print(f"📊 价格: ${results[0]['close']:.2f} → ${results[-1]['close']:.2f}")
    print(f"  买入持有收益: {stats['buy_hold_return']:+.2f}%")

    print(f"\n{sep}")
    print(f"📈 策略绩效")
    print(f"{sep}")
    print(f"  策略总收益:     {stats['strategy_return']:>+8.2f}%")
    print(f"  年化收益:       {stats['annual_return']:>+8.2f}%")
    print(f"  最大回撤:       {stats['max_drawdown']:>8.2f}%")
    print(f"  夏普比率:       {stats['sharpe_ratio']:>8.2f}")
    print(f"  买入持有收益:   {stats['buy_hold_return']:>+8.2f}%   (基准)")

    print(f"\n{sep}")
    print(f"🎯 交易统计")
    print(f"{sep}")
    print(f"  交易次数:       {stats['n_trades']:>8d}")
    print(f"  胜率:           {stats['win_rate']:>8.1f}%")
    print(f"  平均盈利:       {stats['avg_win_pct']:>+8.2f}%  (每笔)")
    print(f"  平均亏损:       {stats['avg_loss_pct']:>+8.2f}%  (每笔)")
    print(f"  盈亏比:         {stats['profit_factor']:>8.2f}")

    if trades:
        print(f"\n{sep}")
        print(f"📋 交易明细 ({len(trades)}笔)")
        print(f"{sep}")
        print(f"{'入场':<12}{'出场':<12}{'入场价':>8}{'出场价':>8}{'涨幅':>8}{'仓位':>7}{'收益':>8}{'结果':>5}")
        print("-" * 72)
        for t in trades:
            result_mark = '✅' if t['profitable'] else '❌'
            print(f"{t['entry_date']:<12}{t['exit_date']:<12}{t['entry_price']:>8.2f}{t['exit_price']:>8.2f}"
                  f"{t['price_change_pct']:>+7.2f}%{t['avg_position']:>6.0%}{t['trade_return_pct']:>+7.2f}%{result_mark:>5}")

    # 月度收益表
    print(f"\n{sep}")
    print(f"📅 月度收益率")
    print(f"{sep}")
    monthly = {}
    for e in eq_curve:
        m = e['date'][:7]
        if m not in monthly:
            monthly[m] = {'first_eq': e['equity'], 'last_eq': e['equity'],
                          'first_close': e['close'], 'last_close': e['close']}
        monthly[m]['last_eq'] = e['equity']
        monthly[m]['last_close'] = e['close']
    months = sorted(monthly.keys())
    for m in months:
        d = monthly[m]
        m_ret_s = (d['last_eq'] / d['first_eq'] - 1) * 100
        m_ret_b = (d['last_close'] / d['first_close'] - 1) * 100
        mark = '🟢' if m_ret_s >= 0 else '🔴'
        print(f"  {m}  {mark} 策略:{m_ret_s:>+7.2f}%  基准:{m_ret_b:>+7.2f}%")

    # 关键事件
    print(f"\n{sep}")
    print(f"📌 关键信号与持仓变化")
    print(f"{sep}")
    key_events = [e for e in eq_curve
                  if e['position'] > 0 or '建仓' in e['action']
                  or '清仓' in e['action'] or '减仓' in e['action']]
    shown = set()
    for e in key_events:
        key = f"{e['date']}_{e['action']}"
        if key not in shown:
            shown.add(key)
            pos_sign = '⬆' if e['position'] > 0 else '⬇'
            print(f"  {e['date']}  {pos_sign} 仓位={e['position']:.0%}  "
                  f"信号={e['signal']:<5}  趋势={e['trend']:<8}  {e['action']}")
            if e['position'] > 0:
                print(f"         收盘=${e['close']:.2f}  净值={e['equity']:.4f}")


# ═══════════════════════════════════════════════════════════════
# 6. Main
# ═══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description='XMM 策略通用回测')
    parser.add_argument('--ticker', default='00700.HK', help='港股标的，如 00700.HK')
    parser.add_argument('--name', default=None, help='中文名称（可选）')
    parser.add_argument('--days', type=int, default=730, help='回测天数')
    parser.add_argument('--lookback', type=int, default=60, help='XMM 初始化窗口')
    args = parser.parse_args()

    # 名称映射
    NAME_MAP = {
        '00700.HK': '腾讯', '09988.HK': '阿里巴巴', '03690.HK': '美团',
        '01810.HK': '小米', '00388.HK': '港交所', '09618.HK': '京东',
        '01024.HK': '快手', '06055.HK': '中烟国际',
    }
    name = args.name or NAME_MAP.get(args.ticker, args.ticker)

    print(f"\n🚀 回测 {args.ticker}（{name}）XMM策略...")
    print(f"   数据窗口: {args.days}天 | 初始化: {args.lookback}天")

    print("\n📡 获取数据...")
    df = fetch_data(args.ticker, days=args.days)
    if df is None:
        print("[FAIL] 无法获取数据")
        sys.exit(1)
    print(f"   数据: {len(df)}条  {str(df.index[0])[:10]} ~ {str(df.index[-1])[:10]}")

    print("\n🔬 运行徐小明信号扫描...")
    results = run_scan(df, lookback=args.lookback)
    print(f"   扫描完成: {len(results)}天")

    print("\n💰 模拟持仓管理...")
    trades, eq_curve, daily_returns = simulate_trades(results)

    print("\n📊 计算绩效...")
    stats = calc_stats(trades, eq_curve, daily_returns, results)

    print_report(results, trades, eq_curve, stats, args.ticker, name)


if __name__ == '__main__':
    main()