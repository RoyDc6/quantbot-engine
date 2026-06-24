# -*- coding: utf-8 -*-
"""
scripts/migrate_to_sqlite.py - 从 JSON/CSV 文件迁移历史数据到 quant.db

注意:
  这是历史归档/研究迁移工具。
  当前 HK/US 日常运行口径为 Futu 输入 -> signal JSON 结构化输出 -> reports 日报渲染。
  quant.db 不参与当前交易状态判断。

用法:
  python scripts/migrate_to_sqlite.py          # 执行迁移
  python scripts/migrate_to_sqlite.py --dry-run # 预览模式（不写入）
"""

import sys, io, os, json, argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from core.paths import PROJECT_ROOT
BASE = PROJECT_ROOT

from core.quant_db import QuantDB


def migrate_signals(db: QuantDB, dry_run=False):
    """归档 paper_trading/signals/*.json → signals + market_states + trades + positions."""
    signals_dir = BASE / 'paper_trading' / 'signals'
    files = sorted(signals_dir.glob('*.json'))
    
    signal_count = 0
    ms_count = 0
    trade_count = 0
    pos_count = 0
    
    for f in files:
        try:
            with open(f, encoding='utf-8') as fh:
                data = json.load(fh)
        except Exception as e:
            print(f'  [SKIP] {f.name}: {e}')
            continue
        
        date = data.get('date', f.stem.split('_')[0])
        market = 'HK' if '_HK' in f.name.upper() else ('US' if '_US' in f.name.upper() else 'HK')
        
        # 市场状态
        ms = data.get('market_state', {})
        if ms:
            if dry_run:
                print(f'  [DRY] market_state: {date} → {ms.get("market_state")}')
            else:
                db.insert_market_state(date, ms)
            ms_count += 1
        
        # 信号
        signals = data.get('signals', [])
        if signals:
            # 区分 HK 和 US 信号
            hk_signals = [s for s in signals if s.get('market') == 'HK']
            us_signals = [s for s in signals if s.get('market') in ('US', 'SPX')]
            
            if not dry_run:
                if hk_signals:
                    db.insert_signals(date, hk_signals, market='HK')
                if us_signals:
                    db.insert_signals(date, us_signals, market='US')
            signal_count += len(signals)
        
        # 交易记录
        portfolio = data.get('portfolio', {})
        trades = portfolio.get('trades', [])
        if trades:
            if not dry_run:
                db.insert_trades(date, trades)
            trade_count += len([t for t in trades if t.get('date') == date])
        
        # 持仓快照
        positions = portfolio.get('positions', [])
        if positions:
            if not dry_run:
                db.insert_positions(date, positions)
            pos_count += len(positions)
        
        print(f'  ✅ {f.name}: {len(signals)} signals, {trade_count} trades, {len(positions)} positions')
    
    return signal_count, ms_count, trade_count, pos_count


def migrate_trade_executions(db: QuantDB, dry_run=False):
    """迁移 output/trades_*.json → trade_executions"""
    output_dir = BASE / 'output'
    files = sorted(output_dir.glob('trades_*.json'))
    
    count = 0
    for f in files:
        try:
            with open(f, encoding='utf-8') as fh:
                data = json.load(fh)
        except Exception as e:
            print(f'  [SKIP] {f.name}: {e}')
            continue
        
        timestamp = data.get('timestamp', '')
        mode = data.get('mode', 'DRY-RUN')
        orders = data.get('orders', [])
        
        if orders:
            if not dry_run:
                db.insert_trade_executions(timestamp, mode, orders)
            count += len(orders)
            print(f'  ✅ {f.name}: {len(orders)} orders ({mode})')
    
    return count


def migrate_factor_ic(db: QuantDB, dry_run=False):
    """迁移 backtest/factor_ic_history.json → factor_ic_history"""
    ic_file = BASE / 'backtest' / 'factor_ic_history.json'
    if not ic_file.exists():
        print('  [SKIP] factor_ic_history.json 不存在')
        return 0
    
    try:
        with open(ic_file, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f'  [SKIP] factor_ic_history.json: {e}')
        return 0
    
    count = 0
    if isinstance(data, list):
        for record in data:
            date = record.get('date', '')
            factors = record.get('factors', {})
            for fname, ic_val in factors.items():
                if not dry_run:
                    db.insert_factor_ic(date, fname, float(ic_val))
                count += 1
    elif isinstance(data, dict):
        for date, factors in data.items():
            if isinstance(factors, dict):
                for fname, ic_val in factors.items():
                    if not dry_run:
                        db.insert_factor_ic(date, fname, float(ic_val))
                    count += 1
    
    if count:
        print(f'  ✅ factor_ic_history.json: {count} IC records')
    return count


def main():
    parser = argparse.ArgumentParser(description='迁移历史量化数据到 SQLite 归档库')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不实际写入')
    args = parser.parse_args()
    
    dry_run = args.dry_run
    mode = 'DRY-RUN' if dry_run else 'LIVE'
    
    print(f'═══ QuantDB 数据迁移 ({mode}) ═══')
    print('口径: Futu 输入 -> signal JSON 结构化输出 -> reports 日报渲染；quant.db 仅作历史归档/研究查询。')
    print(f'数据库: {BASE / "quant.db"}')
    print()
    
    if dry_run:
        db = QuantDB(':memory:')
    else:
        db = QuantDB()
    
    # 1. 信号 + 市场状态 + 交易 + 持仓
    print('📊 迁移信号数据 (paper_trading/signals/*.json)...')
    sc, msc, tc, pc = migrate_signals(db, dry_run)
    
    # 2. 交易执行日志
    print('\n📈 迁移交易执行日志 (output/trades_*.json)...')
    ec = migrate_trade_executions(db, dry_run)
    
    # 3. 因子IC历史
    print('\n🔬 迁移因子IC历史 (backtest/factor_ic_history.json)...')
    fc = migrate_factor_ic(db, dry_run)
    
    # 统计
    print('\n═══ 迁移完成 ═══')
    print(f'  信号: {sc} 条')
    print(f'  市场状态: {msc} 条')
    print(f'  交易记录: {tc} 条')
    print(f'  持仓快照: {pc} 条')
    print(f'  执行日志: {ec} 条')
    print(f'  因子IC: {fc} 条')
    
    if not dry_run:
        stats = db.stats()
        print(f'\n📊 数据库统计:')
        for table, cnt in stats.items():
            print(f'  {table}: {cnt} 行')
    
    db.close()
    print('\n✅ 完成')


if __name__ == '__main__':
    main()
