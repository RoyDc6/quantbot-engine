# -*- coding: utf-8 -*-
"""
Futu 桥接器 — 打通 daily_runner 信号 → Futu 模拟交易执行
职责:
  1. 读取 daily_runner 信号文件
  2. 转换为 trade_executor 格式
  3. 调用 trade_executor 在 Futu 模拟账户执行
  4. Futu 为唯一持仓真相源，本地不再维护 portfolio.json
"""
import sys, io, os, json, argparse
from pathlib import Path
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings_issued = False

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from core.paths import PROJECT_ROOT
BASE = PROJECT_ROOT
PT = BASE / 'paper_trading'
FW_TRADER = BASE / 'futu_trader'

sys.path.insert(0, str(FW_TRADER))

FUTU_HOST = '127.0.0.1'
FUTU_PORT = 11111

# === 符号转换 ============================================
def to_futu_code(symbol):
    """00700.HK → HK.00700, SPY.US → US.SPY"""
    parts = symbol.split('.')
    if len(parts) == 2:
        return f'{parts[1]}.{parts[0]}'
    return symbol

def to_tickflow_symbol(code):
    """HK.00700 → 00700.HK, US.SPY → SPY.US"""
    parts = code.split('.')
    if len(parts) == 2:
        return f'{parts[1]}.{parts[0]}'
    return code

# === 信号转换 ============================================
def convert_signals(daily_signal_file):
    """将 daily_runner 信号转换为 trade_executor 格式"""
    with open(daily_signal_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    results = []
    for sig in data.get('signals', []):
        futu_code = to_futu_code(sig['symbol'])
        level = sig['fusion_level']
        score = sig.get('fusion_score', 0)

        # 跳过 HOLD / REDUCED
        if level in ('HOLD', 'REDUCED'):
            continue

        results.append({
            'code': futu_code,
            'signal': level,
            'price': sig.get('close', 0),
            'pred': score / 1000,
            'confidence': sig.get('fusion_confidence', 0),
            'original_symbol': sig['symbol'],
            'market': sig.get('market', 'HK'),
            'reasoning': sig.get('reasoning', '')[:80],
        })

    return results

# === Futu 直接执行 =======================================
def execute_on_futu(signals, dry_run=True):
    """在 Futu 模拟账户执行信号 — 支持港股+美股"""
    import futu as ft

    TRD_ENV = ft.TrdEnv.SIMULATE

    print(f'\n{"="*60}')
    print(f'  Futu Bridge 执行器 v2.0 (HK+US)')
    print(f'  模式: {"DRY-RUN" if dry_run else "LIVE"}')
    print(f'  时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'{"="*60}')

    # 按市场分组信号
    hk_signals = [s for s in signals if s.get('market', 'HK') == 'HK']
    us_signals = [s for s in signals if s.get('market') == 'US']

    # 分别执行两个市场
    for market, mkt_signals, mkt_label in [
        ('HK', hk_signals, '港股'),
        ('US', us_signals, '美股'),
    ]:
        if not mkt_signals:
            continue

        mkt_enum = ft.Market.HK if market == 'HK' else ft.Market.US
        print(f'\n--- {mkt_label} ({len(mkt_signals)} signals) ---')

        trd_ctx = ft.OpenSecTradeContext(
            filter_trdmarket=mkt_enum,
            host=FUTU_HOST, port=FUTU_PORT
        )

        try:
            # 查询账户
            ret, acc = trd_ctx.accinfo_query(trd_env=TRD_ENV)
            if ret != ft.RET_OK:
                print(f'  [ERROR] {mkt_label} 账户查询失败: {acc}')
                continue
            total_assets = float(acc.iloc[0].get('total_assets', 0))
            cash = float(acc.iloc[0].get('cash', 0))
            print(f'  账户: 总资产 {total_assets:,.0f} | 现金 {cash:,.0f}')

            # 查询持仓
            ret, pos_data = trd_ctx.position_list_query(trd_env=TRD_ENV)
            held_codes = {}
            if ret == ft.RET_OK and pos_data is not None and len(pos_data) > 0:
                for _, row in pos_data.iterrows():
                    code = row['code']
                    qty = float(row.get('qty', 0))
                    if qty > 0:
                        held_codes[code] = {
                            'qty': qty,
                            'can_sell': float(row.get('can_sell_qty', qty)),
                            'cost': float(row.get('cost_price', 0)),
                            'market_val': float(row.get('market_val', 0)),
                        }
                        pnl = (float(row.get('market_val', 0)) / max(float(row.get('cost_price', 1)) * qty, 1) - 1) * 100 if qty > 0 else 0
                        print(f'  持仓: {code} {qty:.0f}股 成本{row.get("cost_price", 0):.2f} PnL={pnl:+.1f}%')

            # 获取实时报价 + lot_size
            quote_ctx = ft.OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
            all_codes = list(held_codes.keys()) + [s['code'] for s in mkt_signals if s['code'] not in held_codes]
            all_codes = list(set(all_codes))

            price_map = {}
            lot_size_map = {}
            if all_codes:
                ret, snap = quote_ctx.get_market_snapshot(all_codes)
                if ret == ft.RET_OK and snap is not None:
                    for _, row in snap.iterrows():
                        price_map[row['code']] = float(row['last_price'])
                        lot_size_map[row['code']] = int(row.get('lot_size', 1 if market == 'US' else 100))
            quote_ctx.close()

            # 信号反转清仓
            sold_codes = set()
            sell_signals = [s for s in mkt_signals if s['signal'] in ('SELL', 'STRONG_SELL')]

            for sig in sell_signals:
                code = sig['code']
                if code not in held_codes:
                    continue
                if held_codes[code]['can_sell'] <= 0:
                    continue

                price = price_map.get(code, sig['price'])
                lot = lot_size_map.get(code, 1 if market == 'US' else 100)
                qty = int(held_codes[code]['can_sell'])
                qty = (qty // lot) * lot
                if qty <= 0:
                    continue

                pnl = (price / held_codes[code]['cost'] - 1) * 100 if held_codes[code]['cost'] > 0 else 0

                if dry_run:
                    print(f'  [DRY-RUN] SELL {code} {qty}股 @ {price:.2f} (PnL={pnl:+.1f}%)')
                else:
                    ret, data = trd_ctx.place_order(
                        price=price, qty=qty, code=code,
                        trd_side=ft.TrdSide.SELL,
                        order_type=ft.OrderType.NORMAL, trd_env=TRD_ENV,
                    )
                    if ret == ft.RET_OK:
                        order_id = data.iloc[0].get('order_id', 'N/A') if len(data) > 0 else 'N/A'
                        print(f'  [SELL] {code} {qty}股 @ {price:.2f} → {order_id} (PnL={pnl:+.1f}%)')
                    else:
                        print(f'  [ERROR] SELL {code}: {data}')

                sold_codes.add(code)

            # 新建仓
            buy_signals = sorted(
                [s for s in mkt_signals if s['signal'] in ('BUY', 'STRONG_BUY')],
                key=lambda x: x['pred'], reverse=True
            )

            for sig in buy_signals:
                code = sig['code']
                if code in held_codes and code not in sold_codes:
                    continue

                price = price_map.get(code, sig['price'])
                if price <= 0:
                    continue

                if sig['signal'] == 'STRONG_BUY':
                    budget = total_assets * 0.20
                else:
                    budget = total_assets * 0.10

                budget = min(budget, cash * 0.8)
                lot = lot_size_map.get(code, 1 if market == 'US' else 100)
                qty = max(int(budget / price / lot) * lot, lot)
                cost = qty * price

                if cost > cash:
                    qty = max(int(cash * 0.8 / price / lot) * lot, lot)
                    cost = qty * price

                if dry_run:
                    print(f'  [DRY-RUN] BUY {code} {qty}股 @ {price:.2f} = {cost:,.0f} (score={sig["pred"]*1000:+.0f})')
                else:
                    ret, data = trd_ctx.place_order(
                        price=price, qty=qty, code=code,
                        trd_side=ft.TrdSide.BUY,
                        order_type=ft.OrderType.NORMAL, trd_env=TRD_ENV,
                    )
                    if ret == ft.RET_OK:
                        order_id = data.iloc[0].get('order_id', 'N/A') if len(data) > 0 else 'N/A'
                        print(f'  [BUY] {code} {qty}股 @ {price:.2f} = {cost:,.0f} → {order_id}')
                        cash -= cost
                    else:
                        print(f'  [ERROR] BUY {code}: {data}')

        finally:
            trd_ctx.close()

    # 保存执行记录
    log = {
        'timestamp': datetime.now().isoformat(),
        'dry_run': dry_run,
        'signals_received': len(signals),
        'hk_signals': len(hk_signals),
        'us_signals': len(us_signals),
        'signals': signals,
    }
    log_path = PT / 'futu_bridge_log.json'
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(log, f, indent=2, ensure_ascii=False, default=str)
    print(f'\n  日志: {log_path}')

# === 入口 ================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Futu 桥接器 — daily_runner → Futu 执行')
    parser.add_argument('--signal-file', type=str, default=None,
                        help='daily_runner 信号文件路径（默认读取今天的）')
    parser.add_argument('--live', action='store_true',
                        help='实际下单（默认 DRY-RUN）')
    args = parser.parse_args()

    # 确定信号文件
    if args.signal_file:
        signal_file = args.signal_file
    else:
        today = datetime.now().strftime('%Y-%m-%d')
        signal_file = PT / 'signals' / f'{today}.json'

    if not os.path.exists(signal_file):
        print(f'[ERROR] 信号文件不存在: {signal_file}')
        sys.exit(1)

    print(f'信号源: {signal_file}')

    # 转换信号
    converted = convert_signals(signal_file)
    print(f'转换后信号: {len(converted)}条')
    for s in converted:
        print(f'  {s["code"]:12s} {s["signal"]:12s} score={s["pred"]*1000:+.0f} price={s["price"]:.2f}')

    # 执行
    execute_on_futu(converted, dry_run=not args.live)
