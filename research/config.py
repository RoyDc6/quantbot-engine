"""
research/config.py — 因子研究环境配置

包含市场配置、符号列表、数据源连接参数、路径注入。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Any

# ============================================================
# 路径注入 — 复用现有量化基础设施
# ============================================================

QUANT_ROOT = Path(__file__).resolve().parent.parent  # E:\quant

# 确保核心模块可导入
for p in [str(QUANT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)


# ============================================================
# 市场配置
# ============================================================

MARKET_CONFIG: Dict[str, Any] = {
    "US": {
        "name": "美股",
        "timezone": "US/Eastern",
        "open_time": "09:30",
        "close_time": "16:00",
        "default_timeframe": "1d",
    },
    "HK": {
        "name": "港股",
        "timezone": "Asia/Hong_Kong",
        "open_time": "09:30",
        "close_time": "16:00",
        "default_timeframe": "1d",
    },
    "CN": {
        "name": "A股",
        "timezone": "Asia/Shanghai",
        "open_time": "09:30",
        "close_time": "15:00",
        "default_timeframe": "1d",
    },
    "CRYPTO": {
        "name": "加密货币",
        "timezone": "UTC",
        "open_time": "00:00",
        "close_time": "23:59",
        "default_timeframe": "1d",
    },
}


# ============================================================
# 数据源配置
# ============================================================

FUTU_CONFIG: Dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 11111,
}

OKX_CONFIG: Dict[str, Any] = {
    "api_key": "",
    "secret_key": "",
    "passphrase": "",
    # 研究模式不需要 API Key，公共接口可获取 K 线
    "public_only": True,
}


# ============================================================
# 研究默认参数
# ============================================================

RESEARCH_CONFIG: Dict[str, Any] = {
    "default_kline_count": 500,      # 默认 K 线数量
    "max_kline_count": 2000,         # 最大 K 线数量
    "cache_enabled": True,           # 是否启用缓存
    "cache_max_age_days": 1,         # 缓存有效期
    "parallel_fetch": False,         # 是否并行获取数据
    "output_dir": str(QUANT_ROOT / "research" / "output"),
}


# ============================================================
# Kronos 模型配置
# ============================================================

KRONOS_CONFIG: Dict = {
    "default_model": "NeoQuasar/Kronos-small",
    "default_pred_len": 5,
    "default_context_len": 60,
    "default_stride": 5,
    "default_sample_count": 3,
    "default_T": 1.0,
    "default_top_k": 0,
    "default_top_p": 0.9,
    "device": "cpu",
}


# ============================================================
# 便利函数
# ============================================================

def get_market_config(market: str) -> Dict[str, Any]:
    """获取某市场的配置"""
    return MARKET_CONFIG.get(market.upper(), {})

def list_supported_markets() -> list:
    """列出所有支持的市场"""
    return list(MARKET_CONFIG.keys())