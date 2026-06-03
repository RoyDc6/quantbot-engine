# -*- coding: utf-8 -*-
"""
scripts/db_query.py - QuantDB CLI 查询工具
用法:
  python scripts/db_query.py stats                  # 数据库统计
  python scripts/db_query.py signals [--days 30]    # 最近信号
  python scripts/db_query.py trades [--symbol 00700.HK] [--days 90]
  python scripts/db_query.py portfolio              # 当前持仓
  python scripts/db_query.py market [--days 30]     # 市场状态
  python scripts/db_query.py pnl [--days 90]        # PnL 汇总
  python scripts/db_query.py ic [--factor xxx]      # 因子IC
  python scripts/db_query.py sql "SELECT ..."       # 自定义SQL
"""

import sys, io, argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from core.paths import PROJECT_ROOT

from core.quant_db import QuantDB


def cmd_stats(db):
    """数据库统计"""
    s = db.stats()
    total = sum(s.values())
    print(f'═══ QuantDB 统计 ═══')
    for table, cnt in s.items():
        print(f'  {table:20s}: {cnt:>6d} 行')
    print(f'  {"合计":20s}: {total:>6d} 行')
    print(f'\n数据库: {db.db_path} ({db.db_path.stat().st_size / 1024:.0f} KB)')


def cmd_signals(db, days, symbol=None, market=None):
    """信号查询"""
    df = db.get_signals(symbol=symbol, days=days, market=market)
    if df.empty:
        print('无信号数据')
        return
    # 只显示关键列
    cols = ['date', 'symbol', 'fusion_level', 'fusion_score', 'fusion_confidence',
            'rsi_daily', 'regime']
    display_cols = [c for c in cols if c in df.columns]
    print(df[display_cols].to_string(index=False))
    print(f'\n共 {len(df)} 条信号')


def cmd_trades(db, days, symbol=None):
    """交易记录"""
    df = db.get_trades(symbol=symbol, days=days)
    if df.empty:
        print('无交易记录')
        return
    print(df.to_string(index=False))
    total_pnl = df['pnl'].sum()
    print(f'\n共 {len(df)} 笔交易, 累计PnL: {total_pnl:+.2f}%')


def cmd_portfolio(db, date=None):
    """持仓快照"""
    df = db.get_positions(date=date)
    if df.empty:
        print('无持仓数据')
        return
    cols = ['date', 'symbol', 'shares', 'entry_price', 'current_price', 'pnl']
    display_cols = [c for c in cols if c in df.columns]
    print(df[display_cols].to_string(index=False))


def cmd_market(db, days):
    """市场状态"""
    df = db.get_market_states(days=days)
    if df.empty:
        print('无市场状态数据')
        return
    cols = ['date', 'market_state', 'vix_regime', 'trend', 'momentum', 'vix_price', 'spy_price']
    display_cols = [c for c in cols if c in df.columns]
    print(df[display_cols].to_string(index=False))


def cmd_pnl(db, days):
    """PnL 汇总"""
    trades_df = db.get_trades(days=days)
    if trades_df.empty:
        print('无交易数据')
        return
    
    # 按标的汇总
    pnl_by_symbol = trades_df.groupby('symbol').agg(
        trades=('action', 'count'),
        total_pnl=('pnl', 'sum'),
        avg_pnl=('pnl', 'mean')
    ).round(2)
    
    print('═══ PnL 汇总（按标的）═══')
    print(pnl_by_symbol.to_string())
    
    # 按日期汇总
    pnl_by_date = trades_df.groupby('date').agg(
        trades=('action', 'count'),
        total_pnl=('pnl', 'sum')
    ).round(2)
    
    print('\n═══ PnL 汇总（按日期）═══')
    print(pnl_by_date.to_string())
    print(f'\n总PnL: {trades_df["pnl"].sum():+.2f}%')


def cmd_ic(db, days, factor=None):
    """因子IC"""
    df = db.get_factor_ic(factor_name=factor, days=days)
    if df.empty:
        print('无因子IC数据')
        return
    print(df.to_string(index=False))
    print(f'\n共 {len(df)} 条IC记录')


def cmd_sql(db, sql):
    """自定义SQL"""
    df = db.query(sql)
    if df.empty:
        print('查询结果为空')
        return
    print(df.to_string(index=False))
    print(f'\n共 {len(df)} 行')


def main():
    parser = argparse.ArgumentParser(description='QuantDB 查询工具')
    sub = parser.add_subparsers(dest='command')
    
    # stats
    sub.add_parser('stats', help='数据库统计')
    
    # signals
    p_sig = sub.add_parser('signals', help='信号查询')
    p_sig.add_argument('--days', type=int, default=30)
    p_sig.add_argument('--symbol', type=str, default=None)
    p_sig.add_argument('--market', type=str, default=None)
    
    # trades
    p_trd = sub.add_parser('trades', help='交易记录')
    p_trd.add_argument('--days', type=int, default=90)
    p_trd.add_argument('--symbol', type=str, default=None)
    
    # portfolio
    p_port = sub.add_parser('portfolio', help='持仓快照')
    p_port.add_argument('--date', type=str, default=None)
    
    # market
    p_mkt = sub.add_parser('market', help='市场状态')
    p_mkt.add_argument('--days', type=int, default=30)
    
    # pnl
    p_pnl = sub.add_parser('pnl', help='PnL汇总')
    p_pnl.add_argument('--days', type=int, default=90)
    
    # ic
    p_ic = sub.add_parser('ic', help='因子IC')
    p_ic.add_argument('--factor', type=str, default=None)
    p_ic.add_argument('--days', type=int, default=60)
    
    # sql
    p_sql = sub.add_parser('sql', help='自定义SQL')
    p_sql.add_argument('query', type=str, help='SQL查询语句')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    db = QuantDB()
    
    try:
        if args.command == 'stats':
            cmd_stats(db)
        elif args.command == 'signals':
            cmd_signals(db, args.days, args.symbol, args.market)
        elif args.command == 'trades':
            cmd_trades(db, args.days, args.symbol)
        elif args.command == 'portfolio':
            cmd_portfolio(db, args.date)
        elif args.command == 'market':
            cmd_market(db, args.days)
        elif args.command == 'pnl':
            cmd_pnl(db, args.days)
        elif args.command == 'ic':
            cmd_ic(db, args.days, args.factor)
        elif args.command == 'sql':
            cmd_sql(db, args.query)
    finally:
        db.close()


if __name__ == '__main__':
    main()
