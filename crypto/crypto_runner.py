# -*- coding: utf-8 -*-
"""
crypto_runner.py - OKX 加密市场统一运行器
适配 unified_runner.py 架构风格，复用 FusionEngine 信号体系。

用法:
  python crypto/crypto_runner.py                  # 扫描信号+生成日报
  python crypto/crypto_runner.py --signal-only    # 仅扫描信号
"""
import sys, os, json, argparse
from pathlib import Path
from datetime import datetime

BASE = Path('E:/quant')
sys.path.insert(0, str(BASE))

import warnings
warnings.filterwarnings('ignore')


def run(signal_only=False):
    """主流程：扫描加密市场 → 融合评分 → 生成日报"""
    ts_start = datetime.now()
    today = datetime.now().strftime('%Y-%m-%d')

    print('=' * 65)
    print(f'  OKX Crypto Runner v1.0')
    print(f'  时间: {ts_start.strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 65)

    # Step 1: 扫描 OKX 市场
    print(f'\n{"="*65}')
    print(f'  Step 1: OKX 市场扫描')
    print(f'{"="*65}')

    from crypto.okx_adapter import scan_crypto_market, CRYPTO_TARGETS
    signals = scan_crypto_market()
    targets = CRYPTO_TARGETS

    print(f'  标的: {len(targets)} | 有效信号: {len(signals)}')

    buys = [s for s in signals if s['fusion_level'] in ('STRONG_BUY', 'BUY')]
    sells = [s for s in signals if s['fusion_level'] in ('STRONG_SELL', 'SELL')]
    print(f'  BUY: {len(buys)} | SELL: {len(sells)}')

    if signal_only:
        print(f'\n  [信号模式] 仅生成信号')
        _print_crypto_summary(signals)
        return

    # Step 2: 生成日报
    print(f'\n{"="*65}')
    print(f'  Step 2: 日报生成')
    print(f'{"="*65}')

    try:
        from reports.reporter import generate_daily_report
        generate_daily_report(
            date=today,
            signals=signals,
            orders=[],
            positions=[],
            account={'total_assets': 0, 'cash': 0, 'market_val': 0},
            market_report={'market_state': 'CRYPTO', 'vix_regime': 'N/A'},
        )
    except Exception as e:
        print(f'  [WARN] 日报生成失败: {e}')
        import traceback; traceback.print_exc()

    # Step 3: 输出摘要
    _print_crypto_summary(signals)

    duration = (datetime.now() - ts_start).total_seconds()
    print(f'\n  耗时: {duration:.1f}s')
    print(f'{"="*65}')


def _print_crypto_summary(signals):
    """打印加密信号摘要"""
    print(f'\n{"="*65}')
    print(f'  OKX 信号摘要 ({len(signals)} 标的)')
    print(f'{"="*65}')

    for s in signals:
        level = s['fusion_level']
        score = s['fusion_score']
        rsi = s['rsi_daily']
        reasons = ' | '.join(s['reasons'][:3])
        print(f'  {s["symbol"]:<12} {level:<12} score={score:+.0f}  rsi={rsi:.0f}  {reasons}')

    buys = [s for s in signals if s['fusion_level'] in ('STRONG_BUY', 'BUY')]
    if buys:
        print(f'\n  🟢 TOP BUY:')
        for s in buys[:3]:
            print(f'    {s["symbol"]:<12} score={s["fusion_score"]:+.0f}  {", ".join(s["reasons"][:2])}')

    sells = [s for s in signals if s['fusion_level'] in ('STRONG_SELL', 'SELL')]
    if sells:
        print(f'\n  🔴 TOP SELL:')
        for s in sells[:3]:
            print(f'    {s["symbol"]:<12} score={s["fusion_score"]:+.0f}')

    # 保存 JSON
    output_dir = BASE / 'output'
    output_dir.mkdir(exist_ok=True)
    ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = output_dir / f'crypto_signals_{ts_str}.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'signals': [{k: v for k, v in s.items() if k != 'reasons'} for s in signals],
            'summary': {
                'total': len(signals),
                'strong_buy': sum(1 for s in signals if s['fusion_level'] == 'STRONG_BUY'),
                'buy': sum(1 for s in signals if s['fusion_level'] == 'BUY'),
                'hold': sum(1 for s in signals if s['fusion_level'] == 'HOLD'),
                'sell': sum(1 for s in signals if s['fusion_level'] in ('SELL', 'STRONG_SELL')),
            },
        }, f, ensure_ascii=False, indent=2)
    print(f'\n  💾 已保存: {output_file}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='OKX 加密市场运行器')
    parser.add_argument('--signal-only', action='store_true', help='仅扫描信号')
    args = parser.parse_args()
    run(signal_only=args.signal_only)