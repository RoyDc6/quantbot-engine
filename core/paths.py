# -*- coding: utf-8 -*-
"""
core/paths.py - QuantBot 统一路径配置

所有路径从 PROJECT_ROOT 动态推导，消除硬编码 E:/quant/...
便于项目迁移、CI 运行和多机器部署。

用法:
    from core.paths import PROJECT_ROOT, CACHE_DIR, OUTPUT_DIR, DB_PATH
    from core.paths import EVENT_CACHE_DIR, ML_ALPHA_DIR
"""

from pathlib import Path

# ── 项目根目录（自动推导，无需硬编码） ──────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 低风险子目录路径（缓存 / 日志 / 输出 / 数据库） ─────────
OUTPUT_DIR      = PROJECT_ROOT / 'output'
CACHE_DIR       = PROJECT_ROOT / 'scanner2' / 'cache'
CACHE_US_DIR    = PROJECT_ROOT / 'scanner2' / 'cache_us'
EVENT_CACHE_DIR = PROJECT_ROOT / 'market_state' / 'event_cache'
DB_PATH         = PROJECT_ROOT / 'quant.db'
ML_ALPHA_DIR    = PROJECT_ROOT / 'ml_alpha'

# ── 扫描器特定 ─────────────────────────────────────────────
SCANNER2_CACHE      = PROJECT_ROOT / 'scanner2' / 'cache'
SCANNER2_CACHE_US   = PROJECT_ROOT / 'scanner2' / 'cache_us'
SCANNER1_CACHE      = PROJECT_ROOT / 'scanner' / 'cache'
