# -*- coding: utf-8 -*-
"""
scripts/db_query.py - QuantDB CLI 查询工具

注意:
  Futu 是 HK/US 日常运行链路的唯一输入源。
  paper_trading/signals/ JSON 是唯一结构化输出。
  reports/ 日报是 JSON 的可读渲染。
  quant.db 是历史归档/研究查询库，不参与当前交易状态判断。

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


CURRENT_STATE_NOTICE = (
    '[INFO] 运行口径: Futu 是唯一输入；'
    'paper_trading/signals/ JSON 是唯一结构化输出；'
    'reports/ 是 JSON 的可读渲染；'
    'quant.db 仅作历史归档/研究查询。'
)


def print_current_state_notice():
    """Print the canonical runtime source-of-truth boundary."""
    print(CURRENT_STATE_NOTICE)
    print()


def _max_table_date(db, table: str) -> str | None:
    """Return max(date) for a QuantDB table, or None if unavailable."""
    try:
        row = db.conn.execute(f'SELECT MAX(date) AS max_date FROM {table}').fetchone()
        return row['max_date'] if row else None
    except Exception:
        return None


def _latest_signal_json_date(signal_dir: Path | None = None) -> str | None:
    """Return latest YYYY-MM-DD date from generated signal JSON files."""
    sig_dir = signal_dir or PROJECT_ROOT / 'paper_trading' / 'signals'
    if not sig_dir.exists():
        return None

    dates = []
    for path in sig_dir.glob('*.json'):
        prefix = path.name.split('_', 1)[0]
        if len(prefix) == 10 and prefix[4] == '-' and prefix[7] == '-':
            dates.append(prefix)
    return max(dates) if dates else None


def print_freshness_warning(db, signal_dir: Path | None = None):
    """Warn when quant.db lags behind generated signal JSON artifacts."""
    db_signal_date = _max_table_date(db, 'signals')
    latest_json_date = _latest_signal_json_date(signal_dir)
    if db_signal_date and latest_json_date and db_signal_date < latest_json_date:
        pos_date = _max_table_date(db, 'positions') or 'N/A'
        trade_date = _max_table_date(db, 'trades') or 'N/A'
        print(
            '[WARN] quant.db 数据落后: '
            f'signals最新={db_signal_date}, positions最新={pos_date}, '
            f'trades最新={trade_date}, 最新signal JSON={latest_json_date}'
        )
        print(
            '[WARN] 当前交易状态以 Futu 查询为准；结构化输出以 '
            'paper_trading/signals/ JSON 为准；reports/ 仅为可读渲染；'
            'quant.db 不参与当前状态判断。'
        )
        print()


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
    parser.add_argument('--db-path', type=str, default=None,
                        help='QuantDB 路径（默认 E:\\quant\\quant.db）')
    parser.add_argument('--signals-dir', type=str, default=None,
                        help='signal JSON 目录（默认 paper_trading/signals）')
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
    
    db = QuantDB(args.db_path)
    signal_dir = Path(args.signals_dir) if args.signals_dir else None
    
    try:
        print_current_state_notice()
        print_freshness_warning(db, signal_dir)

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
