# -*- coding: utf-8 -*-
"""
okx_adapter.py - OKX 加密市场适配器
将 OKX 市场扫描器接入 FusionEngine 信号体系。

用法:
    from crypto.okx_adapter import scan_crypto_market
    signals = scan_crypto_market()

输出格式与 unified_runner.py 的 signal_engine 兼容：
    {
        'symbol': 'BTC-USDT',
        'close': 50000.0,
        'fusion_level': 'BUY',
        'fusion_score': 35.0,
        'fusion_confidence': 0.65,
        'rsi_daily': 45.0,
        'target_position': 0.20,
        'market': 'CRYPTO',
        'data_source': 'okx_scanner',
        'risk': 'LOW',
        ...
    }
"""
import sys, os, json
from pathlib import Path
from datetime import datetime

# ═══ 路径注入：OKX 引擎路径 ═══════════════════════════════
_OKX_ENGINE = r'D:\new_quant\2026-05-11-task-51\okx_engine'
if _OKX_ENGINE not in sys.path:
    sys.path.insert(0, _OKX_ENGINE)

# ═══ 信号级别映射 ════════════════════════════════════════
OKX_TO_FUSION_LEVEL = {
    'STRONG_BUY': 'STRONG_BUY',
    'BUY':        'BUY',
    'NEUTRAL':    'HOLD',
    'SELL':       'SELL',
    'STRONG_SELL': 'STRONG_SELL',
}

# ═══ 目标标的（与 OKX 引擎一致）════════════════════════════
CRYPTO_TARGETS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT",
    "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "LINK-USDT",
    "DOT-USDT", "NEAR-USDT"
]


def scan_crypto_market() -> list:
    """
    扫描 OKX 加密市场，返回 FusionEngine 兼容的信号列表。

    Returns:
        list of dicts，每个 dict 是标准信号格式
    """
    from market_scanner import generate_signal

    results = []
    for inst_id in CRYPTO_TARGETS:
        try:
            raw = generate_signal(inst_id)
            if raw is None:
                continue
            results.append(_convert_signal(raw))
        except Exception as e:
            print(f'  [CRYPTO] {inst_id} 扫描失败: {e}')
            continue

    # 按 fusion_score 排序
    results.sort(key=lambda s: s.get('fusion_score', 0), reverse=True)
    return results


def _convert_signal(raw: dict) -> dict:
    """将 OKX 信号格式转换为 FusionEngine 标准格式"""
    level = OKX_TO_FUSION_LEVEL.get(raw.get('signal', 'NEUTRAL'), 'HOLD')
    score = float(raw.get('score', 0))

    # 置信度：基于 score 的绝对值映射到 [0.5, 0.95]
    abs_score = abs(score)
    if abs_score >= 40:
        confidence = 0.85
    elif abs_score >= 30:
        confidence = 0.75
    elif abs_score >= 20:
        confidence = 0.65
    elif abs_score >= 10:
        confidence = 0.55
    else:
        confidence = 0.50

    # 风险等级
    if level in ('STRONG_BUY', 'STRONG_SELL'):
        risk = 'LOW' if level.startswith('STRONG_BUY') else 'HIGH'
    elif level in ('BUY', 'SELL'):
        risk = 'MEDIUM'
    else:
        risk = 'LOW'

    # 建议仓位
    if level == 'STRONG_BUY':
        target_pos = 0.20
    elif level == 'BUY':
        target_pos = 0.10
    else:
        target_pos = 0.0

    atr_pct = raw.get('atr_pct', 0) or 0

    return {
        'symbol':            raw.get('inst_id', '?'),
        'close':             float(raw.get('last_price', 0)),
        'fusion_level':      level,
        'fusion_score':      score,
        'fusion_confidence': confidence,
        'rsi_daily':         float(raw.get('rsi', 50) or 50),
        'target_position':   target_pos,
        'market':            'CRYPTO',
        'data_source':       'okx_scanner',
        'risk':              risk,
        'stop_levels': {
            'fixed':        float(raw.get('last_price', 0)) * 0.985,  # -1.5%
            'trailing_pct': -0.015,
            'dd_limit':     -0.10,
        },
        'stop_loss':        float(raw.get('last_price', 0)) * 0.985,
        'take_profit':      float(raw.get('last_price', 0)) * 1.03,
        'change_24h':       raw.get('change_24h', 0),
        'change_7d':        raw.get('change_7d', 0),
        'atr_pct':          atr_pct,
        'vol_ratio':        raw.get('vol_ratio', 1.0),
        'funding_rate':     raw.get('funding_rate', None),
        'reasons':          raw.get('reasons', []),
        'macd_hist':        raw.get('macd_hist', None),
        'ema_7':            raw.get('ema_7', None),
        'ema_25':           raw.get('ema_25', None),
        'ema_99':           raw.get('ema_99', None),
        'warnings':         [],
        'layer':            'research → decision',
    }


def scan_and_print():
    """扫描并打印结果（CLI 入口）"""
    signals = scan_crypto_market()

    print('=' * 65)
    print(f'  OKX 加密市场信号 | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 65)
    print(f'  标的: {len(signals)}')

    buys = [s for s in signals if s['fusion_level'] in ('STRONG_BUY', 'BUY')]
    sells = [s for s in signals if s['fusion_level'] in ('STRONG_SELL', 'SELL')]
    print(f'  BUY: {len(buys)} | SELL: {len(sells)}')

    if buys:
        print(f'\n  🟢 BUY 信号:')
        for s in buys:
            score = s['fusion_score']
            print(f'    {s["symbol"]:<12} {s["fusion_level"]:<12} score={score:+.0f}  rsi={s["rsi_daily"]:.0f}  {", ".join(s["reasons"][:2])}')

    if sells:
        print(f'\n  🔴 SELL 信号:')
        for s in sells:
            print(f'    {s["symbol"]:<12} {s["fusion_level"]:<12} score={s["fusion_score"]:+.0f}')

    return signals


if __name__ == '__main__':
    scan_and_print()