# -*- coding: utf-8 -*-
"""
unified_runner.py - QuantBot 统一港股+美股 Futu 模拟交易运行器
单一入口，Futu 为唯一持仓真相源，所有量化模型参与信号生成。

用法:
  python unified_runner.py                  # 自动判断时段
  python unified_runner.py --market HK      # 只跑港股
  python unified_runner.py --market US      # 只跑美股
  python unified_runner.py --both           # 强制双线
  python unified_runner.py --live           # 实际下单（默认 DRY-RUN）
  python unified_runner.py --signal-only    # 只生成信号，不交易
  python unified_runner.py --no-stop        # 不执行止损检查
"""
import sys
import os
import json
import argparse
import warnings
from datetime import datetime, date
from pathlib import Path

warnings.filterwarnings('ignore')

# === 路径 =========================================================
BASE = Path('E:/quant')
sys.path.insert(0, str(BASE))

# === 导入核心模块 ==================================================
from core.futu_adapter import FutuAdapter
from core.fusion_controller import FusionController
from core.stop_loss import RiskManager
from core.position_manager import (
    StagedEntryManager, SignalReversalChecker, SignalFilter,
    calc_buy_shares, calc_signal_budget,
)
from core.order_executor import OrderExecutor
from core.utils import dict_json_safe, to_futu_code, to_standard_symbol

import config

# === 交易日历 ======================================================
HK_HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 2, 17), date(2026, 2, 18), date(2026, 2, 19),
    date(2026, 4, 3), date(2026, 4, 4), date(2026, 4, 6),
    date(2026, 5, 1), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 1), date(2026, 10, 1), date(2026, 10, 22),
    date(2026, 12, 25), date(2026, 12, 26),
}

US_HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 7, 3),
    date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
}


def is_hk_trading_day(d=None):
    d = d or date.today()
    return d.weekday() < 5 and d not in HK_HOLIDAYS_2026


def is_us_trading_day(d=None):
    d = d or date.today()
    return d.weekday() < 5 and d not in US_HOLIDAYS_2026


def get_market_to_run(force_market=None):
    """根据当前时间和参数决定运行哪个市场。"""
    now = datetime.now()
    hour_min = now.hour * 100 + now.minute

    if force_market:
        return force_market.upper()

    # 自动判断
    # 美股时段: 21:30-次日4:00
    if hour_min >= 2130 or hour_min < 400:
        if is_us_trading_day():
            return 'US'
    # 港股时段: 9:30-16:00
    elif 930 <= hour_min <= 1600:
        if is_hk_trading_day():
            return 'HK'

    # 非交易时段：根据日期判断，优先港股
    if is_hk_trading_day():
        return 'HK'
    elif is_us_trading_day():
        return 'US'
    return 'NONE'


# === 主流程 ========================================================
def run(market='HK', dry_run=True, signal_only=False, no_stop=False):
    today = datetime.now().strftime('%Y-%m-%d')
    ts_start = datetime.now()

    print('=' * 65)
    print(f'  QuantBot Unified Runner v2.2')
    print(f'  时间: {ts_start.strftime("%Y-%m-%d %H:%M:%S")} | 市场: {market}')
    print(f'  模式: {"DRY-RUN" if dry_run else ">>> LIVE <<<"}')
    print('=' * 65)

    # Step 0: Futu 连接测试
    adapter = FutuAdapter(host=config.FUTU_HOST, port=config.FUTU_PORT)
    ok, msg = adapter.test_connection()
    print(f'\n  [连接] {msg}')
    if not ok:
        print('  [FATAL] Futu OpenD 不可达，中止运行')
        return

    # Step 1: 加载 VIX + 初始化 FusionController（战术中控台）
    vix_map = adapter.fetch_vix_data()
    print(f'  [VIX] 加载 {len(vix_map)} 条数据')

    fc = FusionController(config={
        'futu_host': config.FUTU_HOST,
        'futu_port': config.FUTU_PORT,
    })

    # Step 1.5: 市场状态分类（v2.2 — 必须在信号生成之前）
    market_state = 'CRAB'
    market_report = None
    try:
        from market_state.classifier import MarketStateClassifier
        clf = MarketStateClassifier('SPY.US')
        clf.load_data()
        market_report = clf.analyze()
        market_state = market_report['market_state']
        print(f'  [Market State] {market_state} (VIX: {market_report["vix_regime"]} | Trend: {market_report["trend"]} | Momentum: {market_report["momentum"]})')
    except Exception as e:
        print(f'  [WARN] MarketStateClassifier 失败, 回退 CRAB: {e}')
        print(f'  Market State: {market_state}')

    # Step 2: 确定标的（统一转换为标准符号格式: 00700.HK / AAPL.US）
    if market == 'HK':
        targets = list(config.UNIVERSE_HK.keys())  # 已是标准格式: '00700.HK'
        lot_sizes = {s: config.UNIVERSE_HK[s]['lot_size'] for s in config.UNIVERSE_HK}
        mkt_label = '港股'
        take_profit_pct = config.TAKE_PROFIT_PCT_HK
    elif market == 'US':
        targets = [to_standard_symbol(s) for s in config.UNIVERSE_US.keys()]  # 'US.AAPL' → 'AAPL.US'
        lot_sizes = {to_standard_symbol(s): 1 for s in config.UNIVERSE_US}  # 美股无整手限制
        mkt_label = '美股'
        take_profit_pct = config.TAKE_PROFIT_PCT_US
    else:
        print(f'  [ERROR] 不支持的市场: {market}')
        return

    print(f'  [{mkt_label}] 标的: {len(targets)} 只')

    # Step 3: 信号扫描 — 通过 FusionController 战术中控台
    print(f'\n{"="*65}')
    print(f'  Step 1: FusionController 信号扫描 ({len(targets)} stocks)')
    print(f'{"="*65}')

    signals = []
    for sym in targets:
        print(f'  {sym}...', end=' ', flush=True)
        try:
            result = fc.analyze_ticker(
                ticker=sym,
                market_state=market_state,
                vix_data=vix_map,
            )
            sig = _fc_result_to_signal(result, market)
            signals.append(sig)
            print(f'{sig["fusion_level"]} ({sig["fusion_score"]:+.1f}) [{sig["data_source"]}]')
        except Exception as e:
            print(f'FAILED')
            import traceback; traceback.print_exc()
            signals.append({
                'symbol': sym, 'market': market, 'date': today,
                'close': 0, 'data_source': 'ERROR',
                'fusion_level': 'HOLD', 'fusion_score': 0.0,
                'fusion_confidence': 0.0, 'target_position': 0.0,
                'risk': 'MEDIUM', 'warnings': [f'analyze_ticker failed: {e}'],
                'reasoning': '', 'rsi_daily': 50, 'rsi_weekly': 50,
                'market_state': market_state,
            })

    signals.sort(key=lambda x: x['fusion_score'], reverse=True)
    buys = [s for s in signals if s['fusion_level'] in ('STRONG_BUY', 'BUY')]
    sells = [s for s in signals if s['fusion_level'] in ('STRONG_SELL', 'SELL')]
    print(f'\n  信号: 总{len(signals)} | BUY:{len(buys)} | SELL:{len(sells)}')

    if signal_only:
        _save_signals(today, market, signals)
        print(f'\n  [信号模式] 仅生成信号，不执行交易')
        _print_summary(signals, market_state=market_state)
        return

    # Step 4: 查询 Futu 账户（唯一持仓真相源）
    print(f'\n{"="*65}')
    print(f'  Step 2: Futu 账户查询')
    print(f'{"="*65}')

    account = adapter.get_account_info(market)
    if account is None:
        print(f'  [ERROR] 无法查询{mkt_label}账户')
        return

    total_assets = account['total_assets']
    cash = account['cash']
    print(f'  总资产: {total_assets:,.0f} | 现金: {cash:,.0f} | 持仓市值: {account["market_val"]:,.0f}')

    positions = adapter.get_positions(market)
    held_map = {}  # {std_symbol: pos_dict}
    print(f'  持仓: {len(positions)} 只')
    for p in positions:
        std_sym = _futu_to_std(p['code'])
        pnl = (p['current_price'] / p['cost_price'] - 1) * 100 if p['cost_price'] > 0 else 0
        held_map[std_sym] = p
        print(f'    {std_sym:<12} qty={p["qty"]:.0f} cost={p["cost_price"]:.2f} cur={p["current_price"]:.2f} pnl={pnl:+.1f}%')

    # Step 5: 风控检查（止损 + 信号反转清仓）
    state_dir = BASE / f'{market.lower()}_trader' / 'state'
    os.makedirs(state_dir, exist_ok=True)

    risk_mgr = RiskManager(
        state_file=str(state_dir / 'risk_state.json'),
        fixed_stop_pct=config.FIXED_STOP_PCT,
        trailing_stop_pct=config.TRAILING_STOP_PCT,
        portfolio_dd_pct=config.PORTFOLIO_DD_PCT,
        take_profit_pct_hk=config.TAKE_PROFIT_PCT_HK,
        take_profit_pct_us=config.TAKE_PROFIT_PCT_US,
        max_position_pct=config.MAX_POSITION_PCT,
        max_total_pct=config.MAX_TOTAL_PCT,
        atr_low_vol_thresh=config.ATR_LOW_VOL_THRESH,
        atr_low_stop_pct=config.ATR_LOW_STOP_PCT,
        stop_cooldown_days=config.STOP_COOLDOWN_DAYS,
    )

    # 初始化持仓追踪
    current_exposure = sum(p['market_val'] for p in positions)
    for sym, p in held_map.items():
        risk_mgr.init_position(p['code'], p['cost_price'], p['current_price'])
        risk_mgr.update_highest(p['code'], p['current_price'])
    risk_mgr.update_portfolio_peak(total_assets)

    # 清理已平仓标的
    active_codes = {p['code'] for p in positions}
    risk_mgr.cleanup_closed(active_codes)

    orders = []
    sold_codes = set()
    signal_map = {s['symbol']: s for s in signals}

    if not no_stop:
        print(f'\n{"="*65}')
        print(f'  Step 3: 风控检查')
        print(f'{"="*65}')

        # 信号反转清仓
        reversal_checker = SignalReversalChecker(
            trigger_levels=config.SIGNAL_REVERSAL_CONFIG['trigger_levels'],
            exclude_if_pnl_above=config.SIGNAL_REVERSAL_CONFIG['exclude_if_pnl_above'],
        )

        for sym, p in held_map.items():
            sig = signal_map.get(sym)
            if not sig:
                continue
            pnl_pct = (p['current_price'] / p['cost_price'] - 1) if p['cost_price'] > 0 else 0
            should_exit, reason = reversal_checker.should_exit(sig['fusion_level'], pnl_pct)
            if should_exit:
                qty = int(p['can_sell_qty'])
                lot = lot_sizes.get(sym, 100)
                qty = (qty // lot) * lot
                if qty > 0:
                    orders.append({
                        'symbol': sym, 'action': 'SELL', 'qty': qty,
                        'price': p['current_price'], 'lot_size': lot,
                        'reason': reason,
                    })
                    sold_codes.add(p['code'])
                    print(f'  [信号反转] {sym} {qty}股 @ {p["current_price"]:.2f} ({reason})')

        # 三重止损
        for sym, p in held_map.items():
            if p['code'] in sold_codes:
                continue
            if p['can_sell_qty'] <= 0:
                continue

            stop_result = risk_mgr.check_all_stops(p['code'], p['current_price'], market)
            if stop_result['triggered']:
                qty = int(p['can_sell_qty'])
                lot = lot_sizes.get(sym, 100)
                qty = (qty // lot) * lot
                if qty > 0:
                    risk_mgr.record_stop(p['code'])
                    orders.append({
                        'symbol': sym, 'action': 'SELL', 'qty': qty,
                        'price': p['current_price'], 'lot_size': lot,
                        'reason': stop_result['reason'],
                    })
                    sold_codes.add(p['code'])
                    print(f'  [{stop_result["action"]}] {sym} {qty}股 @ {p["current_price"]:.2f} ({stop_result["reason"]})')

    # Step 6: 建仓决策
    print(f'\n{"="*65}')
    print(f'  Step 4: 建仓决策')
    print(f'{"="*65}')

    sig_filter = SignalFilter()
    staged_mgr = StagedEntryManager(
        total_tranches=config.STAGED_ENTRY_CONFIG['total_tranches'],
        first_pct=config.STAGED_ENTRY_CONFIG['first_pct'],
        min_gap_days=config.STAGED_ENTRY_CONFIG['min_gap_days'],
        pnl_above=config.STAGED_ENTRY_CONFIG['confirm_conditions']['pnl_above'],
        signal_still_buy=config.STAGED_ENTRY_CONFIG['confirm_conditions']['signal_still_buy'],
    )

    # 已持有（排除已卖出）
    held_syms = {s for s, p in held_map.items() if p['code'] not in sold_codes}
    available_cash = cash
    max_positions = 5

    # 卖出先释放现金（估算）
    for o in orders:
        if o['action'] == 'SELL':
            available_cash += o['qty'] * o['price']
            current_exposure -= o['qty'] * o['price']

    print(f'  可用现金: {available_cash:,.0f} | 持仓数: {len(held_syms)}/{max_positions}')
    print(f'  信号: STRONG_BUY={len([s for s in buys if s["fusion_level"]=="STRONG_BUY"])} BUY={len([s for s in buys if s["fusion_level"]=="BUY"])}')

    # 新建仓信号（排除已持有 + 止损冷却期）
    buy_candidates = [
        s for s in buys
        if s['symbol'] not in held_syms
        and not risk_mgr.is_in_cooldown(to_futu_code(s['symbol']))
    ]

    for sig in buy_candidates:
        if len(held_syms) >= max_positions:
            break

        symbol = sig['symbol']
        level = sig['fusion_level']
        price = sig['close']
        lot = lot_sizes.get(symbol, 100)

        # 信号质量过滤
        passed, reason = sig_filter.check(level, sig['fusion_score'], sig['fusion_confidence'])
        if not passed:
            print(f'  [SKIP] {symbol}: {reason}')
            continue

        # 计算预算
        budget = calc_signal_budget(level, sig['fusion_score'], total_assets, len(held_syms), max_positions)
        budget = min(budget, available_cash * 0.8)  # 保留 20% 现金

        # 分批建仓
        total_shares = calc_buy_shares(price, budget, lot)
        if total_shares <= 0:
            print(f'  [SKIP] {symbol}: 预算不足 (budget={budget:,.0f}, price={price:.2f})')
            continue

        # 首批只建 1/3
        first_shares = int(total_shares * config.STAGED_ENTRY_CONFIG['first_pct'] / lot) * lot
        if first_shares < lot:
            first_shares = min(lot, total_shares)

        # 仓位限制检查
        trade_val = first_shares * price
        ok, reason = risk_mgr.check_position_limit(to_futu_code(symbol), trade_val, total_assets)
        if not ok:
            print(f'  [SKIP] {symbol}: {reason}')
            continue

        ok, reason = risk_mgr.check_total_exposure(current_exposure, trade_val, total_assets)
        if not ok:
            print(f'  [SKIP] {symbol}: {reason}')
            continue

        staged_info = f' [分批 1/{config.STAGED_ENTRY_CONFIG["total_tranches"]} 总目标{total_shares}股]'

        orders.append({
            'symbol': symbol, 'action': 'BUY', 'qty': first_shares,
            'price': price, 'lot_size': lot,
            'reason': f'{level} score={sig["fusion_score"]:+.0f}{staged_info}',
        })

        held_syms.add(symbol)
        available_cash -= trade_val
        current_exposure += trade_val
        print(f'  [BUY] {symbol} {first_shares}股 @ {price:.2f} = {trade_val:,.0f} ({level} score={sig["fusion_score"]:+.0f}){staged_info}')

    # Step 7: 执行订单
    print(f'\n{"="*65}')
    print(f'  Step 5: 订单执行 ({len(orders)} 笔)')
    print(f'{"="*65}')

    if not orders:
        print('  无订单需要执行')
    else:
        executor = OrderExecutor(
            host=config.FUTU_HOST, port=config.FUTU_PORT,
            dry_run=dry_run,
        )
        log_dir = BASE / 'output'
        os.makedirs(log_dir, exist_ok=True)
        ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_path = str(log_dir / f'trades_{market}_{ts_str}.json')
        results = executor.execute_orders(orders, log_path=log_path)

    # 保存风控状态
    risk_mgr.save_state()

    # 保存信号报告
    _save_signals(today, market, signals, orders)

    # 打印摘要
    _print_summary(signals, market_state=market_state)

    # 生成日报
    try:
        from reports.reporter import generate_daily_report
        # 将 Futu 持仓转换为 reporter 兼容格式
        _reporter_positions = []
        for sym, p in held_map.items():
            cur_price = p.get('current_price', 0)
            cost_price = p.get('cost_price', 0) or 1
            _reporter_positions.append({
                'symbol': sym,
                'shares': int(p.get('qty', 0)),
                'qty': int(p.get('qty', 0)),
                'entry_price': cost_price,
                'current_price': cur_price,
                'pnl': (cur_price / cost_price - 1) * 100,
                'triggered_stop': False,
            })
        generate_daily_report(
            date=today,
            signals=signals,
            orders=orders,
            positions=_reporter_positions,
            account=account,
            market_report=market_report,
        )
    except Exception as e:
        print(f'  [WARN] 日报生成失败: {e}')
        import traceback; traceback.print_exc()

    duration = (datetime.now() - ts_start).total_seconds()
    print(f'\n  耗时: {duration:.1f}s')
    print(f'{"="*65}')


# === 辅助函数 =====================================================

def _fc_result_to_signal(fc_result: dict, market: str) -> dict:
    """将 FusionController.analyze_ticker() 输出映射为标准信号格式。"""
    fc_fusion = fc_result.get('fusion', {})
    fc_dir = fc_result.get('directive', {})
    return {
        'symbol': fc_result.get('ticker', ''),
        'market': market,
        'date': fc_result.get('date', ''),
        'close': fc_result.get('close', 0),
        'data_source': fc_result.get('data_source', 'Fusion'),
        'fusion_level': fc_dir.get('level', fc_fusion.get('level', 'HOLD')),
        'fusion_score': fc_fusion.get('score', 0),
        'fusion_confidence': fc_fusion.get('confidence', 0),
        'target_position': fc_fusion.get('position_pct', fc_dir.get('target_pct', 0)),
        'risk': fc_fusion.get('risk_level', 'MEDIUM'),
        'warnings': fc_result.get('warnings', []) + fc_fusion.get('warnings', []),
        'reasoning': fc_fusion.get('reasoning', ''),
        'rsi_daily': fc_result.get('rsi_daily', 50),
        'rsi_weekly': fc_result.get('rsi_weekly', 50),
        'market_state': fc_result.get('market_state', 'CRAB'),
        'weights_used': fc_fusion.get('weights_used', {}),
        'raw_scores': fc_fusion.get('raw_scores', {}),
    }


def _futu_to_std(futu_code):
    parts = futu_code.split('.')
    if len(parts) == 2:
        return f'{parts[1]}.{parts[0]}'
    return futu_code


def _save_signals(today, market, signals, orders=None):
    """保存信号和订单到 JSON 文件。"""
    sig_dir = BASE / 'paper_trading' / 'signals'
    os.makedirs(sig_dir, exist_ok=True)
    sig_file = sig_dir / f'{today}_{market}.json'

    report = {
        'date': today,
        'market': market,
        'generated_at': datetime.now().isoformat(),
        'signals': [dict_json_safe(s) for s in signals],
        'orders': [dict_json_safe(o) for o in (orders or [])],
        'summary': {
            'total': len(signals),
            'strong_buy': sum(1 for s in signals if s['fusion_level'] == 'STRONG_BUY'),
            'buy': sum(1 for s in signals if s['fusion_level'] == 'BUY'),
            'hold': sum(1 for s in signals if s['fusion_level'] == 'HOLD'),
            'sell': sum(1 for s in signals if s['fusion_level'] in ('SELL', 'STRONG_SELL')),
            'reduced': sum(1 for s in signals if s['fusion_level'] == 'REDUCED'),
        },
    }
    with open(sig_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f'\n  信号报告: {sig_file}')


def _print_summary(signals, market_state='CRAB'):
    """打印信号摘要。"""
    print(f'\n{"="*65}')
    print(f'  信号摘要 ({len(signals)} stocks) | 市场状态: {market_state}')
    print(f'{"="*65}')

    for sig in signals:
        level = sig['fusion_level']
        if level in ('STRONG_BUY', 'BUY'):
            ms = sig.get('market_state', '?')
            event = sig.get('event_sentiment_score', 0)
            event_str = f' event={event:+.0f}' if event else ''
            print(f'  {sig["symbol"]:<12} {level:<12} score={sig["fusion_score"]:+.1f} '
                  f'conf={sig["fusion_confidence"]:.0%} '
                  f'rsi_d={sig["rsi_daily"]:.0f} state={ms}{event_str} [{sig["data_source"]}]')

    print(f'\n--- REDUCED ---')
    for sig in signals:
        level = sig['fusion_level']
        if level == 'REDUCED':
            ms = sig.get('market_state', '?')
            raw = sig.get('raw_scores', {})
            rs = f' xmm={raw.get("xmm",0):+.0f} vp={raw.get("vp",0):+.0f} llm={raw.get("llm",0):+.0f}' if raw else ''
            print(f'  {sig["symbol"]:<12} {level:<12} score={sig["fusion_score"]:+.1f} state={ms} conf={sig["fusion_confidence"]:.0%}{rs} [{sig["data_source"]}]')

    print(f'\n--- HOLD ---')
    for sig in signals:
        level = sig['fusion_level']
        if level == 'HOLD':
            ms = sig.get('market_state', '?')
            print(f'  {sig["symbol"]:<12} {level:<12} score={sig["fusion_score"]:+.1f} state={ms} [{sig["data_source"]}]')

    print(f'\n--- SELL ---')
    for sig in signals:
        level = sig['fusion_level']
        if level in ('STRONG_SELL', 'SELL'):
            ms = sig.get('market_state', '?')
            print(f'  {sig["symbol"]:<12} {level:<12} score={sig["fusion_score"]:+.1f} state={ms} [{sig["data_source"]}]')


# === CLI ===========================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='QuantBot 统一交易运行器')
    parser.add_argument('--market', type=str, default=None, help='HK / US（默认自动判断）')
    parser.add_argument('--both', action='store_true', help='强制港股+美股双线运行')
    parser.add_argument('--live', action='store_true', help='实际下单（默认 DRY-RUN）')
    parser.add_argument('--signal-only', action='store_true', help='只生成信号，不交易')
    parser.add_argument('--no-stop', action='store_true', help='不执行止损检查')
    args = parser.parse_args()

    dry_run = not args.live
    signal_only = args.signal_only
    no_stop = args.no_stop

    if args.both:
        # 双线运行：先港股后美股
        if is_hk_trading_day():
            run('HK', dry_run=dry_run, signal_only=signal_only, no_stop=no_stop)
        if is_us_trading_day():
            run('US', dry_run=dry_run, signal_only=signal_only, no_stop=no_stop)
    else:
        market = args.market or get_market_to_run()
        if market == 'NONE':
            print('  今天非交易日，无需运行')
        else:
            run(market, dry_run=dry_run, signal_only=signal_only, no_stop=no_stop)
