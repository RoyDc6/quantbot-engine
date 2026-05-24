# -*- coding: utf-8 -*-
"""
Daily Portfolio Report Generator
Generates end-of-day summary: positions, P&L, signals, trades

Author: AiGobot
Date: 2026-04-24
"""
import sys, io, os, json, warnings
from datetime import datetime

_IS_MAIN = __name__ == '__main__'
if _IS_MAIN:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

BASE_DIR = r'E:\quant\futu_trader'
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')


def generate_report():
    from futu import OpenQuoteContext, TrdEnv, TrdMarket, SecurityFirm

    report = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'time': datetime.now().strftime('%H:%M:%S'),
        'positions': [],
        'account': {},
        'signals': [],
        'trades': [],
    }

    # 1. Get account info & positions
    try:
        from futu import OpenSecTradeContext, TrdEnv
        trd_ctx = OpenSecTradeContext(filter_trdmarket=TrdMarket.HK, host='127.0.0.1', port=11111, security_firm=SecurityFirm)
        
        # Account summary
        ret, acc_data = trd_ctx.accinfo_query(trd_env=TrdEnv.SIMULATE)
        if ret == 0:
            report['account'] = {
                'total_assets': float(acc_data.iloc[0]['total_assets']),
                'cash': float(acc_data.iloc[0]['cash']),
                'market_val': float(acc_data.iloc[0]['market_val']),
                'power': float(acc_data.iloc[0]['power']),
            }

        # Positions
        ret, pos_data = trd_ctx.position_list_query(trd_env=TrdEnv.SIMULATE)
        if ret == 0 and len(pos_data) > 0:
            for _, row in pos_data.iterrows():
                report['positions'].append({
                    'code': row['code'],
                    'name': row.get('stock_name', ''),
                    'qty': int(row['qty']),
                    'market_price': float(row['market_price']),
                    'market_val': float(row['market_val']),
                    'cost_price': float(row.get('cost_price', 0)),
                    'pl_ratio': float(row.get('pl_ratio', 0)) * 100,
                    'pl_val': float(row.get('pl_val', 0)),
                })

        # Today's orders
        ret, order_data = trd_ctx.order_list_query(trd_env=TrdEnv.SIMULATE)
        if ret == 0 and len(order_data) > 0:
            today = datetime.now().strftime('%Y-%m-%d')
            for _, row in order_data.iterrows():
                if str(row.get('create_time', '')).startswith(today):
                    report['trades'].append({
                        'order_id': str(row['order_id']),
                        'code': row['code'],
                        'side': row.get('side', ''),
                        'price': float(row.get('price', 0)),
                        'qty': float(row.get('qty', 0)),
                        'status': row.get('order_status', ''),
                    })

        trd_ctx.close()
    except Exception as e:
        report['account_error'] = str(e)

    # 2. Load latest signals
    signal_files = [f for f in os.listdir(OUTPUT_DIR) if f.startswith('signal_') and f.endswith('.json')]
    if signal_files:
        latest_signal = os.path.join(OUTPUT_DIR, sorted(signal_files)[-1])
        with open(latest_signal, 'r', encoding='utf-8') as f:
            sig_data = json.load(f)
            report['signals'] = sig_data.get('results', [])

    # Save report
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = os.path.join(OUTPUT_DIR, 'daily_report_{}.json'.format(ts))
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Print summary
    print('=' * 60)
    print('DAILY PORTFOLIO REPORT - {}'.format(report['date']))
    print('=' * 60)

    acc = report.get('account', {})
    if acc:
        print('\n[Account]')
        print('  Total Assets: {:,.2f} HKD'.format(acc.get('total_assets', 0)))
        print('  Cash:         {:,.2f} HKD'.format(acc.get('cash', 0)))
        print('  Market Value: {:,.2f} HKD'.format(acc.get('market_val', 0)))
        print('  Buying Power: {:,.2f} HKD'.format(acc.get('power', 0)))

    if report['positions']:
        print('\n[Positions]')
        for p in report['positions']:
            pl_str = '{:+.2f}%'.format(p['pl_ratio'])
            print('  {} {:>10s}  {}shares  price={:.2f}  P&L={}'.format(
                p['code'], p.get('name', ''), p['qty'], p['market_price'], pl_str))
    else:
        print('\n[Positions] Empty')

    if report['trades']:
        print('\n[Today Orders]')
        for t in report['trades']:
            print('  {} {} {} @ {:.2f} {}'.format(
                t['order_id'], t['side'], t['code'], t['price'], t['status']))

    buys = [s for s in report['signals'] if 'BUY' in s.get('signal', '')]
    sells = [s for s in report['signals'] if 'SELL' in s.get('signal', '')]
    if buys:
        print('\n[Buy Signals]')
        for s in buys:
            print('  {} {} pred={:+.2%}'.format(s['code'], s['signal'], s.get('pred', 0)))
    if sells:
        print('\n[Sell Signals]')
        for s in sells:
            print('  {} {} pred={:+.2%}'.format(s['code'], s['signal'], s.get('pred', 0)))

    print('\n' + '=' * 60)
    print('Report saved: {}'.format(report_path))
    return report


if __name__ == '__main__':
    generate_report()
