# -*- coding: utf-8 -*-
"""
config.py - QuantBot 全局配置 (v2.0)
单一真相源：所有管线共享此配置
"""
from datetime import datetime, timedelta

# === Futu OpenD ==================================================
FUTU_HOST = '127.0.0.1'
FUTU_PORT = 11111

# === 股票池 ======================================================
UNIVERSE_HK = {
    '00700.HK': {'name': '腾讯', 'lot_size': 100},
    '00388.HK': {'name': '港交所', 'lot_size': 100},
    '09988.HK': {'name': '阿里巴巴', 'lot_size': 100},
    '03690.HK': {'name': '美团', 'lot_size': 100},
    '09618.HK': {'name': '京东', 'lot_size': 100},
    '01024.HK': {'name': '快手', 'lot_size': 100},
    '06055.HK': {'name': '中烟国际', 'lot_size': 400},
}

UNIVERSE_US = {
    'US.AAPL':  {'name': 'Apple'},
    'US.AMZN':  {'name': 'Amazon'},
    'US.MSFT':  {'name': 'Microsoft'},
    'US.GOOGL': {'name': 'Google'},
    'US.META':  {'name': 'Meta'},
    'US.NVDA':  {'name': 'Nvidia'},
    'US.TSLA':  {'name': 'Tesla'},
    'US.AMD':   {'name': 'AMD'},
    'US.AVGO':  {'name': 'Broadcom'},
    'US.ORCL':  {'name': 'Oracle'},
    'US.NFLX':  {'name': 'Netflix'},
    'US.CRM':   {'name': 'Salesforce'},
    'US.ADBE':  {'name': 'Adobe'},
    'US.INTC':  {'name': 'Intel'},
    'US.QCOM':  {'name': 'Qualcomm'},
}

# === 风控参数（港股+美股统一）======================================
MAX_POSITION_PCT = 0.20       # 单只最大 20%
MAX_TOTAL_PCT    = 0.80       # 总仓位最大 80%
FIXED_STOP_PCT   = -0.12      # 固定止损 -12%
TRAILING_STOP_PCT = -0.12     # 移动止损 -12%
PORTFOLIO_DD_PCT = -0.30      # 组合回撤 -30%
TAKE_PROFIT_PCT_HK = 0.15     # 港股止盈 +15%
TAKE_PROFIT_PCT_US = 0.20     # 美股止盈 +20%
ATR_LOW_VOL_THRESH = 0.025    # 低波动阈值
ATR_LOW_STOP_PCT   = -0.15    # 低波动放宽止损
STOP_COOLDOWN_DAYS = 10       # 止损冷却期

# === 分批建仓 ====================================================
STAGED_ENTRY_CONFIG = {
    'enabled': True,
    'total_tranches': 3,
    'first_pct': 0.34,        # 首批 1/3
    'min_gap_days': 1,
    'confirm_conditions': {
        'pnl_above': -0.02,   # 浮亏 ≤2% 才加仓
        'signal_still_buy': True,
    },
}

# === 信号反转清仓 =================================================
SIGNAL_REVERSAL_CONFIG = {
    'enabled': True,
    'trigger_levels': ['SELL', 'STRONG_SELL'],
    'exclude_if_pnl_above': 0.05,  # 浮盈 >5% 让利润跑
}

# === 市场状态仓位上限 ==============================================
MARKET_STATE_POSITION_LIMIT = {
    'BULL':       0.80,
    'RECOVERY':   0.60,
    'CRAB':       0.40,
    'CORRECTION': 0.20,
    'BEAR':       0.10,
}

# === 信号质量阈值 ==================================================
SIGNAL_QUALITY_THRESHOLD = {
    'STRONG_BUY':  {'min_score': 50, 'min_confidence': 0.75},
    'BUY':         {'min_score': 40, 'min_confidence': 0.65},
    'SELL':        {'min_score': -40, 'min_confidence': 0.65},
    'STRONG_SELL': {'min_score': -50, 'min_confidence': 0.75},
}

# === 交易时间 ======================================================
HK_TRADING_HOURS = {'start': 930, 'end': 1600}  # 9:30-16:00
US_TRADING_HOURS = {'start': 2130, 'end': 400}   # 21:30-次日4:00 (北京时间)

# === VIX / VXX =====================================================
VIX_SYMBOL = 'US.VXX'
VXX_HIGH_THRESH = 35.0   # 高恐惧
VXX_LOW_THRESH  = 22.0   # 低恐惧

# === 成本模型 ======================================================
COMMISSION_RATE = 0.0003
STAMP_TAX_RATE  = 0.001
SLIPPAGE_RATE   = 0.0005

# === 路径 ==========================================================
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SIGNAL_DIR = os.path.join(BASE_DIR, 'paper_trading', 'signals')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')

# === LLM 情绪模型 ==================================================
# ╔══════════════════════════════════════════════════════════════╗
# ║  【锁定标记 L-001】LLM 情绪因子模型名                        ║
# ║  此值不可由 AI 自动修改。修改必须经 Roy 明确授权。              ║
# ║  2026-05-30 经6标的×4模型横评确认:                            ║
# ║    mixtral-8x7b (旧) → 连续下跌场景系统性误判                ║
# ║    llama-4-maverick (新) → 6/6价格理解, 9.9s                 ║
# ╚══════════════════════════════════════════════════════════════╝
LLM_MODEL = 'meta/llama-4-maverick-17b-128e-instruct'
# 所有代码必须通过此配置读取，禁止在代码中直接写模型名字符串。

# === FusionEngine 三因子权重 =========================================
# ╔══════════════════════════════════════════════════════════════╗
# ║  【锁定标记 L-002】FusionEngine 三因子权重                    ║
# ║  此组值不可由 AI 自动修改。修改必须经 Roy 明确授权。            ║
# ║  定义: XMM 60% / VP 25% / LLM 15%                          ║
# ║  使用: fusion_framework/fusion_engine.py → self.BASE_WEIGHTS║
# ╚══════════════════════════════════════════════════════════════╝
XMM_WEIGHT = 0.60
VP_WEIGHT = 0.25
LLM_WEIGHT = 0.15

# === TickFlow API Key 自动注入 ====================================
# 优先从环境变量读取，其次从 Windows 注册表读取
if not os.environ.get('TICKFLOW_API_KEY'):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment')
        val, _ = winreg.QueryValueEx(key, 'TICKFLOW_API_KEY')
        winreg.CloseKey(key)
        if val:
            os.environ['TICKFLOW_API_KEY'] = val
    except Exception:
        pass
TICKFLOW_API_KEY = os.environ.get('TICKFLOW_API_KEY', '')
