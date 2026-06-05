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

# stdout 编码修正（Windows GBK 环境下 emoji 会炸）
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

warnings.filterwarnings('ignore')

# === 路径 =========================================================
from core.paths import PROJECT_ROOT
BASE = PROJECT_ROOT
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
from core.order_journal import OrderStatus
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
def run(market='HK', dry_run=True, signal_only=False, no_stop=False,
        requested_live=False, live_confirmed=False):
    today = datetime.now().strftime('%Y-%m-%d')
    ts_start = datetime.now()

    # 确定 execution_mode
    should_block, _block_reasons = False, []
    if signal_only:
        execution_mode = 'DRY_RUN'
    elif requested_live and live_confirmed:
        execution_mode = 'LIVE_CONFIRMED'
    elif requested_live and not live_confirmed:
        execution_mode = 'LIVE_BLOCKED_BY_CONFIRM'
    else:
        execution_mode = 'DRY_RUN'

    print('=' * 65)
    print(f'  QuantBot Unified Runner v2.2')
    print(f'  Execution Mode: {execution_mode}')
    print(f'  时间: {ts_start.strftime("%Y-%m-%d %H:%M:%S")} | 市场: {market}')
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
        # 信号模式下也生成 v3 报告
        try:
            from reports.fusion_report_v3 import generate_v3_report
            generate_v3_report(
                date=today,
                market=market,
                signals=signals,
                orders=[],
                positions=[],
                account=None,
                market_report=market_report,
            )
        except Exception as e:
            print(f'  [WARN] v3日报生成失败: {e}')
        return

    # Step 4: 查询 Futu 账户（唯一持仓真相源）
    print(f'\n{"="*65}')
    print(f'  Step 2: Futu 账户查询')
    print(f'{"="*65}')

    try:
        account = _require_query_result(
            adapter.get_account_info(market),
            f'{mkt_label}账户',
        )
    except RuntimeError as e:
        print(f'  [ERROR] {e}')
        return

    total_assets = account['total_assets']
    cash = account['cash']
    print(f'  总资产: {total_assets:,.0f} | 现金: {cash:,.0f} | 持仓市值: {account["market_val"]:,.0f}')

    try:
        positions = _require_query_result(
            adapter.get_positions(market),
            f'{mkt_label}持仓',
        )
    except RuntimeError as e:
        print(f'  [ERROR] {e}')
        return
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
                        'intent_type': 'REVERSAL_SELL',
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
                    orders.append({
                        'symbol': sym, 'action': 'SELL', 'qty': qty,
                        'price': p['current_price'], 'lot_size': lot,
                        'intent_type': 'STOP_SELL',
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

    # Snapshot 原始值（订单生成前），供 pre-trade summary 使用
    cash_before = cash
    exposure_before = current_exposure

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
            'intent_type': 'SIGNAL_BUY',
            'reason': f'{level} score={sig["fusion_score"]:+.0f}{staged_info}',
        })

        held_syms.add(symbol)
        available_cash -= trade_val
        current_exposure += trade_val
        print(f'  [BUY] {symbol} {first_shares}股 @ {price:.2f} = {trade_val:,.0f} ({level} score={sig["fusion_score"]:+.0f}){staged_info}')

    # Step 4.5: Pre-Trade Summary + Live Gate (Phase B)
    log_dir = BASE / 'output'
    os.makedirs(log_dir, exist_ok=True)

    if not signal_only:
        pre_summary = _build_pre_trade_summary(
            market=market,
            requested_live=requested_live,
            confirmed_live=live_confirmed,
            account=account,
            positions=positions,
            orders=orders,
            total_assets=total_assets,
            cash_before=cash_before,
            exposure_before=exposure_before,
        )
        should_block, block_reasons = _should_block_live(pre_summary)

        # 填充最终 execution_mode
        if requested_live and live_confirmed and not should_block:
            execution_mode = 'LIVE_CONFIRMED'
        elif requested_live and not live_confirmed:
            execution_mode = 'LIVE_BLOCKED_BY_CONFIRM'
        elif requested_live and live_confirmed and should_block:
            execution_mode = 'LIVE_BLOCKED_BY_RULES'
        else:
            execution_mode = 'DRY_RUN'

        pre_summary['execution_mode'] = execution_mode
        _print_pre_trade_summary(pre_summary)
        _save_guardrail_log(pre_summary, log_dir)

        if execution_mode == 'LIVE_BLOCKED_BY_CONFIRM':
            print(f'\n  [GUARDRAIL] Live 执行被拦截 (LIVE_BLOCKED_BY_CONFIRM)')
            print(f'  [GUARDRAIL] 订单已生成但不执行。使用 --confirm-live 确认后重试。')
        elif execution_mode == 'LIVE_BLOCKED_BY_RULES':
            print(f'\n  [GUARDRAIL] Live 执行被风控规则拦截: {"; ".join(block_reasons)}')
            print(f'  [GUARDRAIL] 请检查风险提示后重试。')

    # Step 7: 执行订单（根据 execution_mode 决定）
    print(f'\n{"="*65}')
    print(f'  Step 5: 订单执行 ({len(orders)} 笔)')
    print(f'  Mode: {execution_mode}')
    print(f'{"="*65}')

    if not orders:
        print('  无订单需要执行')
    elif execution_mode == 'DRY_RUN':
        executor = OrderExecutor(
            host=config.FUTU_HOST, port=config.FUTU_PORT,
            dry_run=True,
        )
        ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_path = str(log_dir / f'trades_{market}_{ts_str}.json')
        results = executor.execute_orders(orders, log_path=log_path, risk_manager=risk_mgr)
    elif execution_mode == 'LIVE_CONFIRMED':
        executor = OrderExecutor(
            host=config.FUTU_HOST, port=config.FUTU_PORT,
            dry_run=False,
        )
        ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_path = str(log_dir / f'trades_{market}_{ts_str}.json')
        results = _execute_live_confirmed_orders(
            executor=executor,
            adapter=adapter,
            risk_mgr=risk_mgr,
            market=market,
            orders=orders,
            log_path=log_path,
        )
    elif execution_mode in ('LIVE_BLOCKED_BY_CONFIRM', 'LIVE_BLOCKED_BY_RULES'):
        print(f'  [GUARDRAIL] 订单已生成但未执行 (mode={execution_mode})')
        print(f'  [GUARDRAIL] 共 {len(orders)} 笔订单，详情见上方 pre-trade summary')

    # 保存风控状态
    risk_mgr.save_state()

    # 保存信号报告
    _save_signals(today, market, signals, orders)

    # 打印摘要
    _print_summary(signals, market_state=market_state)

    # 生成 v3.0 日报
    try:
        from reports.fusion_report_v3 import generate_v3_report
        # 将 Futu 持仓转换为报告兼容格式
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
        generate_v3_report(
            date=today,
            market=market,
            signals=signals,
            orders=orders,
            positions=_reporter_positions,
            account=account,
            market_report=market_report,
        )
    except Exception as e:
        print(f'  [WARN] v3日报生成失败: {e}')
        import traceback; traceback.print_exc()

    duration = (datetime.now() - ts_start).total_seconds()
    print(f'\n  耗时: {duration:.1f}s')
    print(f'{"="*65}')


def _require_query_result(value, label):
    """Return query data or fail closed.

    Real FutuAdapter methods return QueryResult.  Existing tests sometimes mock
    raw dict/list values, so this helper accepts both shapes.
    """
    if hasattr(value, 'require'):
        return value.require(label)
    if value is None:
        raise RuntimeError(f'{label}查询失败')
    return value


def _execute_live_confirmed_orders(executor, adapter, risk_mgr, market, orders, log_path):
    """LIVE_CONFIRMED execution: SELL first, re-query, then BUY."""
    sell_orders = [o for o in orders if o['action'].upper() == 'SELL']
    buy_orders = [o for o in orders if o['action'].upper() == 'BUY']
    results = []

    if sell_orders:
        sell_log_path = log_path.replace('.json', '_SELL.json')
        sell_results = executor.execute_orders(
            sell_orders, log_path=sell_log_path, risk_manager=risk_mgr,
        )
        results.extend(sell_results)
        if any(r.get('status') != OrderStatus.FILLED_ALL.value for r in sell_results):
            for order in buy_orders:
                skipped = {
                    **order,
                    'status': 'SKIP',
                    'message': 'LIVE BUY skipped because SELL did not reach FILLED_ALL',
                }
                results.append(skipped)
            return results

    if not buy_orders:
        return results

    account, positions = _requery_after_sell(adapter, market)
    filtered_buys = _filter_live_buy_orders(
        buy_orders=buy_orders,
        account=account,
        positions=positions,
        risk_mgr=risk_mgr,
    )
    skipped = [o for o in buy_orders if o not in filtered_buys]
    for order in skipped:
        results.append({
            **order,
            'status': 'SKIP',
            'message': 'LIVE BUY skipped by post-SELL realtime risk check',
        })

    if filtered_buys:
        buy_log_path = log_path.replace('.json', '_BUY.json')
        results.extend(executor.execute_orders(
            filtered_buys, log_path=buy_log_path, risk_manager=risk_mgr,
        ))
    return results


def _requery_after_sell(adapter, market):
    """Re-query live account and positions before any BUY after SELL."""
    account = _require_query_result(
        adapter.get_account_info(market),
        f'{market}账户再查询',
    )
    positions = _require_query_result(
        adapter.get_positions(market),
        f'{market}持仓再查询',
    )
    return account, positions


def _filter_live_buy_orders(buy_orders, account, positions, risk_mgr):
    """Apply realtime cash/exposure limits and decrement cash per BUY."""
    total_assets = float(account.get('total_assets', 0) or 0)
    running_cash = float(account.get('cash', 0) or 0)
    current_exposure = sum(float(p.get('market_val', 0) or 0) for p in positions)
    approved = []

    for order in buy_orders:
        trade_val = float(order['qty']) * float(order['price'])
        if trade_val > running_cash:
            print(f'  [LIVE SKIP] {order["symbol"]}: 现金不足 {trade_val:,.0f} > {running_cash:,.0f}')
            continue
        ok, reason = risk_mgr.check_position_limit(
            to_futu_code(order['symbol']),
            trade_val,
            total_assets,
        )
        if not ok:
            print(f'  [LIVE SKIP] {order["symbol"]}: {reason}')
            continue
        ok, reason = risk_mgr.check_total_exposure(
            current_exposure,
            trade_val,
            total_assets,
        )
        if not ok:
            print(f'  [LIVE SKIP] {order["symbol"]}: {reason}')
            continue
        approved.append(order)
        running_cash -= trade_val
        current_exposure += trade_val
    return approved


# === Live Guardrails (Phase B) ===========================================

def _is_live_confirmed(args) -> bool:
    """
    判断是否获得了 live 确认。
    - --live 是必要条件（无 --live 则无需确认）
    - --confirm-live CLI flag 或 QUANT_LIVE_CONFIRM=YES 均可
    - 同时设置时以 --confirm-live 为准
    """
    if not args.live:
        return False
    if os.environ.get('QUANT_LIVE_KILLED', '').upper() == 'YES':
        return False
    if (BASE / 'output' / 'LIVE_DISABLED_FLAG').exists():
        return False
    if args.confirm_live:
        return True
    if os.environ.get('QUANT_LIVE_CONFIRM', '').upper() == 'YES':
        return True
    return False


def _build_pre_trade_summary(market, requested_live, confirmed_live,
                              account, positions, orders,
                              total_assets, cash_before,
                              exposure_before) -> dict:
    """构建 pre-trade summary（符合设计文档 JSON 结构）。

    cash_before / exposure_before 是订单生成前的原始账户快照值，
    summary 内通过 gross_buy/gross_sell 推导 cash_after_orders / exposure_after，
    不会重复扣减建仓循环中已修改的 available_cash / current_exposure。
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

    per_order = []
    for o in orders:
        per_order.append({
            'symbol': o['symbol'],
            'action': o['action'],
            'qty': o['qty'],
            'price': o['price'],
            'notional': round(o['qty'] * o['price'], 2),
            'reason': o.get('reason', ''),
        })

    summary = {
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
            'order_count': {
                'pass': len(orders) > 0,
                'detail': f'{len(orders)} orders' if orders else '0 orders (skip)',
            },
            'cash_after_orders': {
                'pass': cash_after_orders >= 0,
                'detail': f'{cash_after_orders:,.0f} >= 0' if cash_after_orders >= 0
                          else f'{cash_after_orders:,.0f} < 0',
            },
            'largest_order_pct': {
                'pass': (largest['qty'] * largest['price'] / total_assets <= config.MAX_POSITION_PCT
                         if largest and total_assets > 0 else True),
                'detail': (f'{largest["qty"] * largest["price"] / total_assets * 100:.1f}% <= {config.MAX_POSITION_PCT*100:.0f}%'
                           if largest and total_assets > 0 else 'no orders'),
            },
            'exposure_after_orders': {
                'pass': exposure_after / total_assets <= config.MAX_TOTAL_PCT if total_assets > 0 else True,
                'detail': (f'{exposure_after / total_assets * 100:.1f}% <= {config.MAX_TOTAL_PCT*100:.0f}%'
                           if total_assets > 0 else 'no assets'),
            },
        },
    }
    summary['execution_mode'] = ''
    return summary


def _print_pre_trade_summary(summary: dict):
    """打印 pre-trade summary 到控制台（box 格式）。"""
    mode = summary.get('execution_mode', 'DRY_RUN')
    market = summary['market']
    acct = summary['account']
    os_ = summary['orders_summary']
    risk = summary['risk_checks']

    total_orders = os_['total']
    if total_orders == 0:
        return

    print()
    print('╔' + '═' * 61 + '╗')
    print(f'║{"PRE-TRADE SUMMARY":^59}║')
    print('╠' + '═' * 61 + '╣')
    print(f'║ Mode:           {mode:<43}║')
    print(f'║ Market:         {market:<43}║')
    print(f'║ Timestamp:      {summary["timestamp"]:<31}║')
    print('╠' + '═' * 61 + '╣')
    print(f'║ Total Assets:   {acct["total_assets"]:>12,.0f} {"":29}║')
    print(f'║ Cash Before:    {acct["cash_before"]:>12,.0f} {"":29}║')
    print(f'║ Cash After:     {acct["cash_after_orders"]:>12,.0f} {"":29}║')
    print(f'║ Market Value:   {acct["market_val"]:>12,.0f} {"":29}║')
    print(f'║ Exposure Before:{acct["exposure_before_pct"]:>5.1f}% {"":24}║')
    print(f'║ Exposure After: {acct["exposure_after_pct"]:>5.1f}% {"":24}║')
    print('╠' + '═' * 61 + '╣')
    print(f'║ Orders: {total_orders}  BUY: {os_["buy_count"]}  SELL: {os_["sell_count"]} {"":26}║')
    print(f'║ Gross BUY:  {os_["gross_buy"]:>12,.0f} {"":23}║')
    print(f'║ Gross SELL: {os_["gross_sell"]:>12,.0f} {"":23}║')
    print(f'║ Net Impact: {os_["net_cash_impact"]:>+12,.0f} {"":23}║')
    if os_['largest_order']:
        lo = os_['largest_order']
        print(f'║ Largest: {lo["symbol"]} {lo["action"]} {lo["qty"]} @ {lo["price"]:.2f} = {lo["notional"]:,.0f} {"":10}║')
    print('╠' + '═' * 61 + '╣')
    print(f'║ Per-Order Details{"":44}║')
    for i, od in enumerate(summary.get('per_order_details', []), 1):
        sym = od['symbol']
        act = od['action']
        qty = od['qty']
        prv = od['price']
        notional = od['notional']
        reason = od.get('reason', '')[:28]
        print(f'║ {i}. {sym:<10} {act:<4} {qty:>6} @ {prv:<8.2f} {notional:>10,.0f} {reason:<28} ║')
    print('╠' + '═' * 61 + '╣')
    orders_pass = risk.get('order_count', {}).get('pass', True)
    cash_pass   = risk.get('cash_after_orders', {}).get('pass', True)
    pos_pass    = risk.get('largest_order_pct', {}).get('pass', True)
    exp_pass    = risk.get('exposure_after_orders', {}).get('pass', True)
    print(f'║ Risk Checks{"":51}║')
    print(f'║   {"Order Count":20} {"✅ PASS" if orders_pass else "❌ FAIL":<10} {risk["order_count"]["detail"]:<27}║')
    print(f'║   {"Cash After Orders":20} {"✅ PASS" if cash_pass else "❌ FAIL":<10} {risk["cash_after_orders"]["detail"]:<27}║')
    print(f'║   {"Largest Order %":20} {"✅ PASS" if pos_pass else "❌ FAIL":<10} {risk["largest_order_pct"]["detail"]:<27}║')
    print(f'║   {"Exposure After":20} {"✅ PASS" if exp_pass else "❌ FAIL":<10} {risk["exposure_after_orders"]["detail"]:<27}║')
    if summary.get('requested_live') and not summary.get('confirmed_live'):
        print(f'║   {"Confirm Live":20} {"❌ FAIL":<10} {"--confirm-live missing":<27}║')
    elif summary.get('requested_live') and summary.get('confirmed_live'):
        print(f'║   {"Confirm Live":20} {"✅ PASS":<10} {"--confirm-live provided":<27}║')
    print('╚' + '═' * 61 + '╝')


def _save_guardrail_log(summary: dict, log_dir: Path):
    """保存 guardrail log JSON。仅在有订单时写入。"""
    if summary['orders_summary']['total'] == 0:
        return
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    market = summary['market']
    log_path = log_dir / f'guardrails_{market}_{ts}.json'
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def _should_block_live(summary: dict) -> tuple:
    """
    检查是否应拦截 live 执行。
    Returns:
        (should_block, reasons)
    """
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


# === 辅助函数 =====================================================

def _fc_result_to_signal(fc_result: dict, market: str) -> dict:
    """将 FusionController.analyze_ticker() 输出映射为标准信号格式（v3 增强版）。"""
    fc_fusion = fc_result.get('fusion', {})
    fc_dir = fc_result.get('directive', {})
    fc_sources = fc_result.get('sources', {})
    fc_status = fc_result.get('status', {})

    # 提取各因子源详细信息
    xmm_src = fc_sources.get('xmm', {})
    vp_src = fc_sources.get('vp', {})
    llm_src = fc_sources.get('llm', {})

    return {
        'symbol': fc_result.get('ticker', ''),
        'market': market,
        'date': fc_result.get('date', ''),
        'close': fc_result.get('close', 0),
        'data_source': fc_result.get('data_source', 'Fusion'),
        # 融合结果
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
        # 因子分解
        'weights_used': fc_fusion.get('weights_used', {}),
        'raw_scores': fc_fusion.get('raw_scores', {}),
        # 因子详情 (v3 新增)
        'xmm_action': xmm_src.get('action', 'HOLD'),
        'xmm_reason': xmm_src.get('reason', ''),
        'xmm_trend': xmm_src.get('trend', 'UNKNOWN'),
        'xmm_position_size': xmm_src.get('position_size', 0),
        'xmm_td_count': xmm_src.get('td_count', 0),
        'xmm_status': fc_status.get('xmm', 'SKIPPED'),
        'vp_state': vp_src.get('state', 'unknown'),
        'vp_vah': vp_src.get('vah', 0),
        'vp_val': vp_src.get('val', 0),
        'vp_poc': vp_src.get('poc', 0),
        'vp_direction': vp_src.get('direction', 'HOLD'),
        'vp_status': fc_status.get('vp', 'SKIPPED'),
        'llm_sentiment': llm_src.get('sentiment_score', 0),
        'llm_summary': llm_src.get('event_summary', ''),
        'llm_event_type': llm_src.get('event_type', ''),
        'llm_status': fc_status.get('llm', 'SKIPPED'),
        # HardGate
        'gate_approved': fc_result.get('gate', {}).get('approved', True),
        'gate_reasons': fc_result.get('gate', {}).get('reject_reasons', []),
        # 数据新鲜度
        'stale_days': fc_result.get('stale_days', 0),
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
    stale_signals = [s for s in signals if s.get('warnings') and any('滞后' in w for w in s.get('warnings', []))]
    if stale_signals:
        stale_str = ', '.join(s['symbol'] for s in stale_signals[:3])
        print(f'  [STALE] 数据滞后: {stale_str}{"..." if len(stale_signals)>3 else ""}')
    print(f'{"="*65}')

    for sig in signals:
        level = sig['fusion_level']
        stale = ''
        if sig.get('warnings') and any('滞后' in w for w in sig.get('warnings', [])):
            stale = '[STALE]'
        if level in ('STRONG_BUY', 'BUY'):
            ms = sig.get('market_state', '?')
            event = sig.get('event_sentiment_score', 0)
            event_str = f' event={event:+.0f}' if event else ''
            print(f'  {sig["symbol"]:<12} {level:<12} score={sig["fusion_score"]:+.1f} '
                  f'conf={sig["fusion_confidence"]:.0%} '
                  f'rsi_d={sig["rsi_daily"]:.0f} state={ms}{event_str}{stale} [{sig["data_source"]}]')

    def _stale_flag(sig):
        return '[STALE]' if sig.get('warnings') and any('滞后' in w for w in sig.get('warnings', [])) else ''

    print(f'\n--- REDUCED ---')
    for sig in signals:
        level = sig['fusion_level']
        if level == 'REDUCED':
            ms = sig.get('market_state', '?')
            raw = sig.get('raw_scores', {})
            rs = f' xmm={raw.get("xmm",0):+.0f} vp={raw.get("vp",0):+.0f} llm={raw.get("llm",0):+.0f}' if raw else ''
            print(f'  {sig["symbol"]:<12} {level:<12} score={sig["fusion_score"]:+.1f} state={ms} conf={sig["fusion_confidence"]:.0%}{rs}{_stale_flag(sig)} [{sig["data_source"]}]')

    print(f'\n--- HOLD ---')
    for sig in signals:
        level = sig['fusion_level']
        if level == 'HOLD':
            ms = sig.get('market_state', '?')
            print(f'  {sig["symbol"]:<12} {level:<12} score={sig["fusion_score"]:+.1f} state={ms}{_stale_flag(sig)} [{sig["data_source"]}]')

    print(f'\n--- SELL ---')
    for sig in signals:
        level = sig['fusion_level']
        if level in ('STRONG_SELL', 'SELL'):
            ms = sig.get('market_state', '?')
            print(f'  {sig["symbol"]:<12} {level:<12} score={sig["fusion_score"]:+.1f} state={ms}{_stale_flag(sig)} [{sig["data_source"]}]')


# === CLI ===========================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='QuantBot 统一交易运行器')
    parser.add_argument('--market', type=str, default=None, help='HK / US（默认自动判断）')
    parser.add_argument('--both', action='store_true', help='强制港股+美股双线运行')
    parser.add_argument('--live', action='store_true', help='请求实盘交易（默认 DRY-RUN）')
    parser.add_argument('--confirm-live', action='store_true',
                        help='确认实盘交易（必须与 --live 同时使用；或设置环境变量 QUANT_LIVE_CONFIRM=YES）')
    parser.add_argument('--signal-only', action='store_true', help='只生成信号，不交易')
    parser.add_argument('--no-stop', action='store_true', help='不执行止损检查')
    args = parser.parse_args()

    requested_live = bool(args.live)
    live_confirmed = _is_live_confirmed(args)
    dry_run = not requested_live  # default --live 时 dry_run=False
    signal_only = args.signal_only
    no_stop = args.no_stop

    # 警告：--confirm-live 无 --live
    if args.confirm_live and not args.live:
        print('[WARN] --confirm-live 需配合 --live 使用，当前仍为 dry-run')

    # 警告：--live 无确认
    if args.live and not live_confirmed:
        print('[WARN] --live 但未提供 --confirm-live 或 QUANT_LIVE_CONFIRM=YES')
        print('       订单将生成但不执行。使用 --confirm-live 确认执行。')

    if args.both:
        if is_hk_trading_day():
            run('HK', dry_run=dry_run, signal_only=signal_only, no_stop=no_stop,
                requested_live=requested_live, live_confirmed=live_confirmed)
        if is_us_trading_day():
            run('US', dry_run=dry_run, signal_only=signal_only, no_stop=no_stop,
                requested_live=requested_live, live_confirmed=live_confirmed)
    else:
        market = args.market or get_market_to_run()
        if market == 'NONE':
            print('  今天非交易日，无需运行')
        else:
            run(market, dry_run=dry_run, signal_only=signal_only, no_stop=no_stop,
                requested_live=requested_live, live_confirmed=live_confirmed)
