# -*- coding: utf-8 -*-
"""
core/quant_db.py - QuantBot 统一 SQLite 数据库模块
替代散落的 CSV/JSON 文件，提供结构化存储和高效查询。

用法:
    from core.quant_db import QuantDB
    db = QuantDB()  # 自动初始化表结构
    db.insert_signals(date, signals_list)
    db.insert_trades(date, trades_list)
"""

import json
import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

import pandas as pd

from core.paths import DB_PATH as _DB_PATH
DB_PATH = _DB_PATH


class QuantDB:
    """统一 SQLite 数据库封装。"""

    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=NORMAL')
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def close(self):
        if self.conn:
            self.conn.close()

    # ═══════════════════════════════════════════════════════════
    # 表初始化
    # ═══════════════════════════════════════════════════════════

    def _init_tables(self):
        """创建所有表和索引（IF NOT EXISTS）。"""
        c = self.conn.cursor()

        # 1. 每日信号
        c.execute('''CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            close REAL,
            fusion_level TEXT,
            fusion_score REAL,
            fusion_confidence REAL,
            target_position REAL,
            risk TEXT,
            rsi_daily REAL,
            rsi_weekly REAL,
            rsi_monthly REAL,
            td9_count INTEGER,
            macd_desc TEXT,
            fractal_type TEXT,
            trend_up INTEGER,
            fm_signal TEXT,
            fm_score REAL,
            xmm_signal TEXT,
            xmm_confidence REAL,
            llm_factor_score REAL,
            llm_factor_summary TEXT,
            event_sentiment_score REAL,
            event_type TEXT,
            event_summary TEXT,
            event_confidence REAL,
            regime TEXT,
            vix_regime TEXT,
            llm_weight_used REAL,
            stop_fixed REAL,
            stop_trailing_pct REAL,
            stop_dd_limit REAL,
            warnings TEXT,
            reasoning TEXT,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(date, symbol)
        )''')

        # ── Schema Migration: 动态补齐已有表可能缺失的字段 ──
        try:
            c.execute("PRAGMA table_info(signals);")
            existing = {col[1] for col in c.fetchall()}

            # 当前规范 schema（对应上方 CREATE TABLE 的全部非 id 字段）
            required_columns = {
                'date': 'TEXT', 'market': 'TEXT', 'symbol': 'TEXT',
                'close': 'REAL',
                'fusion_level': 'TEXT', 'fusion_score': 'REAL',
                'fusion_confidence': 'REAL', 'target_position': 'REAL',
                'risk': 'TEXT',
                'rsi_daily': 'REAL', 'rsi_weekly': 'REAL', 'rsi_monthly': 'REAL',
                'td9_count': 'INTEGER', 'macd_desc': 'TEXT',
                'fractal_type': 'TEXT', 'trend_up': 'INTEGER',
                'fm_signal': 'TEXT', 'fm_score': 'REAL',
                'xmm_signal': 'TEXT', 'xmm_confidence': 'REAL',
                'llm_factor_score': 'REAL', 'llm_factor_summary': 'TEXT',
                'event_sentiment_score': 'REAL', 'event_type': 'TEXT',
                'event_summary': 'TEXT', 'event_confidence': 'REAL',
                'regime': 'TEXT', 'vix_regime': 'TEXT', 'llm_weight_used': 'REAL',
                'stop_fixed': 'REAL', 'stop_trailing_pct': 'REAL',
                'stop_dd_limit': 'REAL',
                'warnings': 'TEXT', 'reasoning': 'TEXT', 'raw_json': 'TEXT',
                'created_at': 'TEXT',
            }
            # 扩展字段（非原始必需，但备查）
            optional_columns = {
                'chanlun_structure': 'TEXT',
                'vp_signal': 'TEXT',
            }

            all_expected = {**required_columns, **optional_columns}

            missing = []
            for col_name, col_type in all_expected.items():
                if col_name not in existing:
                    missing.append((col_name, col_type))

            if missing:
                for col_name, col_type in missing:
                    alter_sql = f"ALTER TABLE signals ADD COLUMN {col_name} {col_type}"
                    c.execute(alter_sql)
                    logger.info("DB migration: added column %s (%s) to signals table", col_name, col_type)
                self.conn.commit()
                logger.info("DB migration: completed %d column additions to signals table", len(missing))
        except Exception as e:
            logger.warning("DB migration check failed for signals table: %s", e)
        # ── 结束 Schema Migration ──

        # 2. 交易记录
        c.execute('''CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            price REAL,
            shares INTEGER,
            cost REAL,
            pnl REAL,
            reason TEXT,
            fusion_level TEXT,
            fusion_score REAL,
            source TEXT DEFAULT 'paper',
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )''')

        # 3. 持仓快照
        c.execute('''CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market TEXT,
            entry_price REAL,
            shares INTEGER,
            entry_date TEXT,
            highest REAL,
            stop_fixed REAL,
            stop_trail REAL,
            stop_dd REAL,
            fusion_level TEXT,
            fusion_score REAL,
            current_price REAL,
            pnl REAL,
            atr_pct REAL,
            UNIQUE(date, symbol)
        )''')

        # 4. 市场状态
        c.execute('''CREATE TABLE IF NOT EXISTS market_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            timestamp TEXT,
            symbol TEXT,
            market_state TEXT,
            vix_regime TEXT,
            trend TEXT,
            momentum TEXT,
            vol_level TEXT,
            vix_price REAL,
            vix_rsi_day REAL,
            vix_rsi_week REAL,
            vix_trend TEXT,
            vix_pct_rank REAL,
            spy_price REAL,
            spy_sma20 REAL,
            spy_sma50 REAL,
            spy_rsi_day REAL,
            spy_rsi_week REAL,
            spy_atr_pct REAL,
            raw_json TEXT
        )''')

        # 5. 交易执行日志
        c.execute('''CREATE TABLE IF NOT EXISTS trade_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            mode TEXT,
            symbol TEXT,
            futu_code TEXT,
            action TEXT,
            qty INTEGER,
            price REAL,
            cost REAL,
            reason TEXT,
            status TEXT,
            message TEXT,
            raw_json TEXT
        )''')

        # 6. K线缓存
        c.execute('''CREATE TABLE IF NOT EXISTS klines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            source TEXT,
            UNIQUE(symbol, date)
        )''')

        # 7. 因子IC历史
        c.execute('''CREATE TABLE IF NOT EXISTS factor_ic_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            factor_name TEXT NOT NULL,
            ic_value REAL,
            UNIQUE(date, factor_name)
        )''')

        # 索引
        c.execute('CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_signals_date_symbol ON signals(date, symbol)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_positions_date ON positions(date)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_klines_symbol_date ON klines(symbol, date)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_market_states_date ON market_states(date)')

    # ═══════════════════════════════════════════════════════════
    # 写入接口
    # ═══════════════════════════════════════════════════════════

    def insert_signals(self, date: str, signals: List[Dict], market: str = 'HK'):
        """批量写入每日信号。信号来自 daily_runner.py 的 JSON 输出。"""
        c = self.conn.cursor()
        for s in signals:
            # 从 _decision_signal 提取 regime 信息
            ds = s.get('_decision_signal', {})
            # 从嵌套数据提取止损
            stops = s.get('stop_levels', {})
            c.execute('''INSERT OR REPLACE INTO signals (
                date, market, symbol, close,
                fusion_level, fusion_score, fusion_confidence, target_position, risk,
                rsi_daily, rsi_weekly, rsi_monthly, td9_count, macd_desc, fractal_type, trend_up,
                fm_signal, fm_score, xmm_signal, xmm_confidence,
                llm_factor_score, llm_factor_summary,
                event_sentiment_score, event_type, event_summary, event_confidence,
                regime, vix_regime, llm_weight_used,
                stop_fixed, stop_trailing_pct, stop_dd_limit,
                warnings, reasoning, raw_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                date, market, s.get('symbol', ''),
                s.get('close'),
                s.get('fusion_level'),
                s.get('fusion_score'),
                s.get('fusion_confidence'),
                s.get('target_position'),
                s.get('risk'),
                s.get('rsi_daily'),
                s.get('rsi_weekly'),
                s.get('rsi_monthly'),
                s.get('td9_count'),
                s.get('macd_desc'),
                s.get('fractal_type'),
                1 if s.get('trend_up') else 0,
                s.get('fm_signal'),
                s.get('fm_score'),
                s.get('xmm_signal'),
                s.get('xmm_confidence'),
                s.get('llm_factor_score'),
                s.get('llm_factor_summary'),
                s.get('event_sentiment_score'),
                s.get('event_type'),
                s.get('event_summary'),
                s.get('event_confidence'),
                ds.get('regime'),
                ds.get('vix_regime'),
                ds.get('llm_weight_used'),
                stops.get('fixed'),
                stops.get('trailing_pct'),
                stops.get('dd_limit'),
                json.dumps(s.get('warnings', []), ensure_ascii=False),
                s.get('reasoning'),
                json.dumps(s, ensure_ascii=False),
            ))

    def insert_market_state(self, date: str, ms: Dict):
        """写入市场状态（每日一条）。"""
        vd = ms.get('vix_detail', {})
        td = ms.get('trend_detail', {})
        c = self.conn.cursor()
        c.execute('''INSERT OR REPLACE INTO market_states (
            date, timestamp, symbol, market_state, vix_regime, trend, momentum, vol_level,
            vix_price, vix_rsi_day, vix_rsi_week, vix_trend, vix_pct_rank,
            spy_price, spy_sma20, spy_sma50, spy_rsi_day, spy_rsi_week, spy_atr_pct,
            raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
            date,
            ms.get('timestamp'),
            ms.get('symbol'),
            ms.get('market_state'),
            ms.get('vix_regime'),
            ms.get('trend'),
            ms.get('momentum'),
            ms.get('vol_level'),
            vd.get('vxx_price'),
            vd.get('rsi_day'),
            vd.get('rsi_week'),
            vd.get('trend'),
            vd.get('pct_rank'),
            td.get('price'),
            td.get('sma20'),
            td.get('sma50'),
            td.get('rsi_day'),
            td.get('rsi_week'),
            td.get('atr_pct'),
            json.dumps(ms, ensure_ascii=False),
        ))

    def insert_trades(self, date: str, trades: List[Dict]):
        """批量写入交易记录（来自 portfolio.trades）。"""
        c = self.conn.cursor()
        for t in trades:
            # 只写入当天的交易（避免重复写入历史）
            if t.get('date') != date:
                continue
            c.execute('''INSERT INTO trades (
                date, symbol, action, price, shares, cost, pnl, reason,
                fusion_level, fusion_score, source, raw_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (
                t.get('date'),
                t.get('symbol'),
                t.get('action'),
                t.get('price'),
                t.get('shares'),
                t.get('cost'),
                t.get('pnl'),
                t.get('reason'),
                t.get('fusion_level'),
                t.get('fusion_score'),
                'paper',
                json.dumps(t, ensure_ascii=False),
            ))

    def insert_positions(self, date: str, positions: List[Dict]):
        """写入持仓快照。"""
        c = self.conn.cursor()
        for p in positions:
            c.execute('''INSERT OR REPLACE INTO positions (
                date, symbol, market, entry_price, shares, entry_date, highest,
                stop_fixed, stop_trail, stop_dd, fusion_level, fusion_score,
                current_price, pnl, atr_pct
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                date,
                p.get('symbol'),
                p.get('market'),
                p.get('entry_price'),
                p.get('shares'),
                p.get('entry_date'),
                p.get('highest'),
                p.get('stop_fixed'),
                p.get('stop_trail'),
                p.get('stop_dd'),
                p.get('fusion_level'),
                p.get('fusion_score'),
                p.get('current_price'),
                p.get('pnl'),
                p.get('atr_pct'),
            ))

    def insert_trade_executions(self, timestamp: str, mode: str, orders: List[Dict]):
        """写入交易执行日志（来自 output/trades_*.json）。"""
        c = self.conn.cursor()
        for o in orders:
            c.execute('''INSERT INTO trade_executions (
                timestamp, mode, symbol, futu_code, action, qty, price, cost,
                reason, status, message, raw_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (
                o.get('time', timestamp),
                mode,
                o.get('symbol'),
                o.get('futu_code'),
                o.get('action'),
                o.get('qty'),
                o.get('price'),
                o.get('cost'),
                o.get('reason'),
                o.get('status'),
                o.get('message'),
                json.dumps(o, ensure_ascii=False),
            ))

    def insert_klines(self, symbol: str, df: pd.DataFrame, source: str = ''):
        """批量写入K线数据。df 需有 date, open, high, low, close, volume 列。"""
        c = self.conn.cursor()
        rows = []
        for _, row in df.iterrows():
            rows.append((
                symbol,
                str(row.get('date', ''))[:10],
                float(row.get('open', 0)),
                float(row.get('high', 0)),
                float(row.get('low', 0)),
                float(row.get('close', 0)),
                float(row.get('volume', 0)),
                source,
            ))
        c.executemany('''INSERT OR REPLACE INTO klines
            (symbol, date, open, high, low, close, volume, source)
            VALUES (?,?,?,?,?,?,?,?)''', rows)

    def insert_factor_ic(self, date: str, factor_name: str, ic_value: float):
        """写入因子IC记录。"""
        c = self.conn.cursor()
        c.execute('''INSERT OR REPLACE INTO factor_ic_history
            (date, factor_name, ic_value) VALUES (?,?,?)''', (date, factor_name, ic_value))

    # ═══════════════════════════════════════════════════════════
    # 查询接口
    # ═══════════════════════════════════════════════════════════

    def query(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        """执行任意 SQL 查询，返回 DataFrame。"""
        return pd.read_sql_query(sql, self.conn, params=params)

    def get_signals(self, symbol: str = None, days: int = 30, market: str = None) -> pd.DataFrame:
        """查询信号历史。"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        conditions = ['date >= ?']
        params: list = [cutoff]
        if symbol:
            conditions.append('symbol = ?')
            params.append(symbol)
        if market:
            conditions.append('market = ?')
            params.append(market)
        where = ' AND '.join(conditions)
        return self.query(
            f'SELECT date, market, symbol, close, fusion_level, fusion_score, '
            f'fusion_confidence, rsi_daily, rsi_weekly, regime, vix_regime, risk '
            f'FROM signals WHERE {where} ORDER BY date DESC, symbol',
            tuple(params)
        )

    def get_trades(self, symbol: str = None, days: int = 90) -> pd.DataFrame:
        """查询交易记录。"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        conditions = ['date >= ?']
        params: list = [cutoff]
        if symbol:
            conditions.append('symbol = ?')
            params.append(symbol)
        where = ' AND '.join(conditions)
        return self.query(
            f'SELECT date, symbol, action, price, shares, cost, pnl, reason '
            f'FROM trades WHERE {where} ORDER BY date DESC',
            tuple(params)
        )

    def get_positions(self, date: str = None) -> pd.DataFrame:
        """查询持仓快照（指定日期或最新）。"""
        if date:
            return self.query(
                'SELECT * FROM positions WHERE date = ?', (date,))
        return self.query(
            'SELECT * FROM positions WHERE date = (SELECT MAX(date) FROM positions)')

    def get_portfolio_history(self, days: int = 90) -> pd.DataFrame:
        """查询持仓历史（按日期聚合总资产）。"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        return self.query('''
            SELECT date,
                   SUM(current_price * shares) AS position_value,
                   COUNT(*) AS position_count
            FROM positions
            WHERE date >= ?
            GROUP BY date ORDER BY date
        ''', (cutoff,))

    def get_market_states(self, days: int = 30) -> pd.DataFrame:
        """查询市场状态历史。"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        return self.query(
            'SELECT * FROM market_states WHERE date >= ? ORDER BY date DESC',
            (cutoff,)
        )

    def get_klines(self, symbol: str, days: int = 252) -> pd.DataFrame:
        """查询K线数据。"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        return self.query(
            'SELECT date, open, high, low, close, volume FROM klines '
            'WHERE symbol = ? AND date >= ? ORDER BY date',
            (symbol, cutoff)
        )

    def get_factor_ic(self, factor_name: str = None, days: int = 60) -> pd.DataFrame:
        """查询因子IC历史。"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        if factor_name:
            return self.query(
                'SELECT * FROM factor_ic_history WHERE factor_name = ? AND date >= ? ORDER BY date',
                (factor_name, cutoff)
            )
        return self.query(
            'SELECT * FROM factor_ic_history WHERE date >= ? ORDER BY date, factor_name',
            (cutoff,)
        )

    def get_signal_accuracy(self, days: int = 30) -> pd.DataFrame:
        """信号准确率分析：信号方向 vs 实际价格变化。"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        return self.query('''
            SELECT s.date, s.symbol, s.fusion_level, s.fusion_score, s.close,
                   k.close AS next_close,
                   ROUND((k.close - s.close) / s.close * 100, 2) AS actual_return
            FROM signals s
            LEFT JOIN klines k ON s.symbol = k.symbol
                AND k.date = (
                    SELECT MIN(date) FROM klines
                    WHERE symbol = s.symbol AND date > s.date
                )
            WHERE s.date >= ? AND k.close IS NOT NULL
            ORDER BY s.date DESC
        ''', (cutoff,))

    def get_all_trades_history(self) -> pd.DataFrame:
        """查询全部交易记录（不限时间）。"""
        return self.query(
            'SELECT date, symbol, action, price, shares, cost, pnl, reason, fusion_level '
            'FROM trades ORDER BY date'
        )

    # ═══════════════════════════════════════════════════════════
    # 统计
    # ═══════════════════════════════════════════════════════════

    def stats(self) -> Dict[str, int]:
        """返回各表行数统计。"""
        tables = ['signals', 'trades', 'positions', 'market_states',
                   'trade_executions', 'klines', 'factor_ic_history']
        result = {}
        for t in tables:
            row = self.query(f'SELECT COUNT(*) AS cnt FROM {t}')
            result[t] = int(row.iloc[0]['cnt']) if len(row) > 0 else 0
        return result

    def __repr__(self):
        s = self.stats()
        total = sum(s.values())
        return (f'<QuantDB path={self.db_path} rows={total} '
                f'(signals={s["signals"]}, trades={s["trades"]}, '
                f'klines={s["klines"]})>')


def get_db(db_path=None) -> QuantDB:
    """获取 QuantDB 实例（单例风格）。"""
    return QuantDB(db_path)
