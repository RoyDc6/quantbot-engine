# -*- coding: utf-8 -*-
"""
Futu Trading Pipeline v1.1
Signal Engine -> Sonnet Verification -> Trade Executor -> Summary Report

Author: AiGobot
Date: 2026-04-24
"""
import sys, io, os, json, argparse, warnings
from datetime import datetime, date

_IS_MAIN = __name__ == '__main__'
if _IS_MAIN:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

BASE_DIR = r'E:\quant\futu_trader'
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')

sys.path.insert(0, BASE_DIR)


# HK public holidays 2026 (partial - add more as needed)
HK_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # New Year
    date(2026, 2, 17),  # Lunar New Year
    date(2026, 2, 18),  # Lunar New Year
    date(2026, 2, 19),  # Lunar New Year
    date(2026, 4, 3),   # Good Friday
    date(2026, 4, 4),   # Ching Ming
    date(2026, 4, 6),   # Easter Monday
    date(2026, 5, 1),   # Labour Day
    date(2026, 5, 25),  # Buddha Birthday
    date(2026, 6, 19),  # Tuen Ng
    date(2026, 7, 1),   # HKSAR Day
    date(2026, 10, 1),  # National Day
    date(2026, 10, 22), # Chung Yeung
    date(2026, 12, 25), # Christmas
    date(2026, 12, 26), # Boxing Day
}

def is_hk_trading_day():
    """Check if today is a HK trading day (weekday + not holiday)"""
    today = date.today()
    if today.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    if today in HK_HOLIDAYS_2026:
        return False
    return True

def run_pipeline(dry_run=True, signal_only=False, trade_only=False, signal_file=None, skip_non_trading=True, use_sonnet=True):
    ts_start = datetime.now()
    print('=' * 70)
    print('Futu Trading Pipeline v1.1')
    print('Time: {}'.format(ts_start.strftime('%Y-%m-%d %H:%M:%S')))
    print('Mode: {}'.format('DRY-RUN' if dry_run else 'LIVE'))
    print('Sonnet: {}'.format('ON' if use_sonnet else 'OFF'))
    print('=' * 70)

    # Skip non-trading days
    if skip_non_trading and not is_hk_trading_day():
        print('[SKIP] Today is not a HK trading day (weekend or holiday). Exiting.')
        return None

    signal_json = signal_file

    # === Step 1: Generate Signals ===
    if not trade_only:
        print('\n' + '-' * 70)
        print('STEP 1: Signal Engine')
        print('-' * 70)
        from signal_engine import run_scan
        results = run_scan()

        # Find the latest signal file just written
        signal_files = sorted([f for f in os.listdir(OUTPUT_DIR)
                               if f.startswith('signal_') and f.endswith('.json')])
        if signal_files:
            signal_json = os.path.join(OUTPUT_DIR, signal_files[-1])
            print('\n[PIPELINE] Signal file: {}'.format(signal_json))
        else:
            print('[ERROR] No signal file generated')
            return

        if signal_only:
            print('\n[PIPELINE] Signal-only mode, skipping trade execution')
            return

    # === Step 1.5: Sonnet Verification (NEW) ===
    if use_sonnet and not trade_only:
        print('\n' + '-' * 70)
        print('STEP 1.5: Sonnet Verification')
        print('-' * 70)
        from sonnet_verify import run_sonnet_verification
        # Load signal data
        with open(signal_json, 'r', encoding='utf-8') as f:
            signal_data = json.load(f)
        fused_results = run_sonnet_verification(signal_data.get('results', []))

        # Update signal data with fused signals
        signal_data['fused_results'] = fused_results
        # Override signals with fused signals for trade executor
        for r in fused_results:
            r['signal'] = r.get('fused_signal', r.get('signal', 'HOLD'))
        signal_data['results'] = fused_results

        # Save fused signal file
        fused_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        fused_json = os.path.join(OUTPUT_DIR, 'fused_signal_{}.json'.format(fused_ts))
        with open(fused_json, 'w', encoding='utf-8') as f:
            json.dump(signal_data, f, indent=2, ensure_ascii=False)
        signal_json = fused_json
        print('\n[PIPELINE] Fused signal file: {}'.format(signal_json))

    # === Step 2: Execute Trades ===
    print('\n' + '-' * 70)
    print('STEP 2: Trade Executor')
    print('-' * 70)
    from trade_executor import run_trader
    trades = run_trader(signal_file=signal_json, dry_run=dry_run)

    # === Step 3: Summary Report ===
    print('\n' + '-' * 70)
    print('STEP 3: Summary Report')
    print('-' * 70)

    ts_end = datetime.now()
    duration = (ts_end - ts_start).total_seconds()

    # Load signal data for report
    with open(signal_json, 'r', encoding='utf-8') as f:
        signal_data = json.load(f)

    buys = [t for t in trades if 'BUY' in t.get('side', '')]
    sells = [t for t in trades if 'SELL' in t.get('side', '')]

    report = {
        'timestamp': ts_end.isoformat(),
        'duration_sec': duration,
        'mode': 'DRY-RUN' if dry_run else 'LIVE',
        'signals': {
            'total': len(signal_data.get('results', [])),
            'buy_signals': len(signal_data.get('buy_signals', [])),
            'sell_signals': len(signal_data.get('sell_signals', [])),
        },
        'trades': {
            'total': len(trades),
            'buys': len(buys),
            'sells': len(sells),
        },
        'trade_details': trades,
    }

    # Save report
    report_ts = ts_end.strftime('%Y%m%d_%H%M%S')
    report_path = os.path.join(OUTPUT_DIR, 'pipeline_report_{}.json'.format(report_ts))
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print('\nSignal scan: {} stocks | BUY: {} | SELL: {}'.format(
        report['signals']['total'],
        report['signals']['buy_signals'],
        report['signals']['sell_signals']))
    print('Trade plan: {} trades | BUY: {} | SELL: {}'.format(
        report['trades']['total'], len(buys), len(sells)))
    print('Duration: {:.1f}s'.format(duration))
    print('Report: {}'.format(report_path))

    print('\n' + '=' * 70)
    print('PIPELINE COMPLETE')
    print('=' * 70)

    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Futu Trading Pipeline')
    parser.add_argument('--live', action='store_true', help='Actually place orders (default: dry-run)')
    parser.add_argument('--signal-only', action='store_true', help='Only generate signals, no trading')
    parser.add_argument('--trade-only', action='store_true', help='Only execute trades (use latest signal)')
    parser.add_argument('--signal-file', type=str, default=None, help='Path to signal JSON file')
    parser.add_argument('--no-sonnet', action='store_true', help='Disable Sonnet verification layer')
    args = parser.parse_args()

    run_pipeline(
        dry_run=not args.live,
        signal_only=args.signal_only,
        trade_only=args.trade_only,
        signal_file=args.signal_file,
        use_sonnet=not args.no_sonnet,
    )
