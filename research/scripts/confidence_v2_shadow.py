# -*- coding: utf-8 -*-
r"""
confidence_v2_shadow.py -- Shadow analysis for confidence v2 redesign

Codex Decision Brief (2026-06-03):
  Unified confidence & score evidence source (XMM/VP/LLM).
  Shadow mode: does NOT modify live confidence or Gate.
  v2 inputs align with live FusionController._fuse_signals fields.

Usage:
  python E:\quant\research\scripts\confidence_v2_shadow.py

Outputs:
  - stdout comparison table (no emoji, GBK-safe)
  - research/analysis/confidence_v2_shadow.csv  (machine-readable)
  - research/analysis/confidence_v2_shadow.json  (machine-readable)
"""

import json, sys, os, csv
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime, timedelta

# ── Path: use core.paths.PROJECT_ROOT instead of hardcoded E:/quant ──
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from core.paths import PROJECT_ROOT

_SIGNALS_DIR = PROJECT_ROOT / 'paper_trading' / 'signals'
_OUTPUT_DIR  = PROJECT_ROOT / 'research' / 'analysis'

# ─── 权重常量（与 BASE_WEIGHTS / config.py L-002 一致）───
BASE_WEIGHTS = {'XMM': 0.60, 'VP': 0.25, 'LLM': 0.15}

# ─── HardGate 置信度阈值（仅用于影子判断，不修改 Gate 本身）───
LEVEL_CONF_THRESHOLDS = {
    'STRONG_BUY': 0.75, 'BUY': 0.65,
    'HOLD': 0.00,
    'SELL': 0.65, 'STRONG_SELL': 0.75,
    'REDUCED': 0.60,
}
LEVEL_SCORE_THRESHOLDS = {
    'STRONG_BUY': 60, 'BUY': 40,
    'HOLD': 0,
    'SELL': 40, 'STRONG_SELL': 60,
    'REDUCED': 30,
}


# ═══════════════════════════════════════════════════════════════
#  v2 confidence — 同源于 live FusionController._fuse_signals
#
#  与 live 的对应关系:
#    v2 输入                live 源字段                      signal JSON
#    ─────────────────────────────────────────────────────────────────
#    xmm_factor            _xmm_to_score(xmm)              raw_scores.xmm
#    xmm_action            xmm.action                      xmm_action
#    xmm_position_size     xmm.position_size                xmm_position_size
#    vp_factor             _vp_to_score(vp)                 raw_scores.vp
#    vp_direction          vp.direction                     vp_direction
#    llm_factor            _llm_to_score(llm)               raw_scores.llm
#    llm_sentiment         llm.sentiment_score              llm_sentiment
# ═══════════════════════════════════════════════════════════════
def compute_confidence_v2(
    # ── XMM 同源字段 ──
    xmm_factor: float,
    xmm_action: str,
    xmm_position_size: float,
    # ── VP 同源字段 ──
    vp_factor: float,
    vp_direction: str,
    # ── LLM 同源字段 ──
    llm_factor: float,
    llm_sentiment: float,
    # ── 可选 ──
    weights: dict = None,
) -> float:
    """
    三因子同源置信度 v2（shadow-only，不用于实盘）。

    核心原则:
      1. 各源置信度从 live _fuse_signals 的同源字段推导，与 score 共享证据口径。
      2. 活跃源权重归一化（断流源权重重分配给活跃源）。
      3. XMM 沉默惩罚：raw_scores.xmm ≈ 0 时整体置信度 ≤ 55%。
      4. VP-XMM 方向分歧惩罚：方向相反 ×0.85。
      5. 三因子同向奖励：方向一致 ×1.15（上限 0.95）。

    与 live 置信度的区别:
      live: xmm_conf = position_size(active) | 0.3(HOLD)
            vp_conf  = vp.confidence         (子系统置信度, 0~1)
            llm_conf = llm.confidence        (子系统置信度, 0~1)
      v2:   从 raw_scores.* 反向推导置信度，加上分歧/沉默/同向调整
    """
    w = weights or BASE_WEIGHTS

    # Step 1: 各源活跃性判断
    xmm_active = xmm_action != 'HOLD'
    vp_active  = vp_direction != 'HOLD' and abs(vp_factor) > 1
    llm_active = abs(llm_factor) > 1

    sources_active = {'XMM': xmm_active, 'VP': vp_active, 'LLM': llm_active}

    # Step 2: 活跃权重归一化
    total = sum(w[s] for s in w if sources_active.get(s, False))
    if total <= 0:
        return 0.0
    wn = {s: (w[s] / total if sources_active.get(s, False) else 0.0) for s in w}

    # Step 3: 各源原始置信度（与 live 同源）
    #   live: xmm_conf = position_size (if active) else 0.3
    xmm_conf = min(max(xmm_position_size, 0.1), 0.95) if xmm_active else 0.30
    #   live: vp_conf = vp.confidence  (vp.confidence * 100 = vp_factor → vp.confidence = vp_factor / 100)
    vp_conf  = min(abs(vp_factor) / 100.0, 0.80)
    #   live: llm_conf = llm.confidence (not stored in JSON; proxy from sentiment)
    llm_conf = min(abs(llm_factor) / 100.0 + 0.15, 0.85)

    # Step 4: 加权平均
    confidence = xmm_conf * wn['XMM'] + vp_conf * wn['VP'] + llm_conf * wn['LLM']

    # Step 5: XMM 沉默惩罚
    #   当 xmm_factor ≈ 0（_xmm_to_score 输出为 0），整体置信度上限 55%
    if abs(xmm_factor) < 1:
        confidence = min(confidence, 0.55)

    # Step 6: VP-XMM 方向分歧惩罚
    vp_dir  = 1 if vp_direction == 'BUY' else (-1 if vp_direction == 'SELL' else 0)
    xmm_dir = 1 if xmm_action == 'BUY' else (-1 if xmm_action == 'SELL' else 0)
    llm_dir = 1 if llm_sentiment > 5 else (-1 if llm_sentiment < -5 else 0)

    if vp_dir != 0 and xmm_dir != 0 and vp_dir * xmm_dir < 0:
        confidence *= 0.85

    # Step 7: 三因子同向奖励
    if vp_dir != 0 and llm_dir != 0 and xmm_dir != 0:
        if vp_dir == llm_dir == xmm_dir:
            confidence = min(confidence * 1.15, 0.95)

    return round(min(max(confidence, 0.0), 0.95), 3)


# ═══════════════════════════════════════════════════════════════
#  Shadow Gate — 不修改 live Gate，仅用于对比
# ═══════════════════════════════════════════════════════════════
def shadow_gate(level: str, score: float, confidence_v2: float) -> Tuple[bool, List[str]]:
    reasons = []
    conf_thresh = LEVEL_CONF_THRESHOLDS.get(level, 0.60)
    if confidence_v2 < conf_thresh:
        reasons.append(f"置信度 {confidence_v2:.2f} < {conf_thresh:.2f} (等级: {level})")
    score_thresh = LEVEL_SCORE_THRESHOLDS.get(level, 40)
    if level != 'HOLD' and abs(score) < score_thresh:
        reasons.append(f"融合得分 {score:.1f} < {score_thresh} (等级: {level})")
    return (len(reasons) == 0, reasons)


# ═══════════════════════════════════════════════════════════════
#  数据加载
# ═══════════════════════════════════════════════════════════════
def _find_available_dates(market: str, count: int = 5) -> List[str]:
    """从今天往回找最近 count 个交易日的信号文件。"""
    dates = []
    d = datetime.now()
    for _ in range(30):  # 最多回溯 30 天
        ds = d.strftime('%Y-%m-%d')
        p = _SIGNALS_DIR / f'{ds}_{market}.json'
        if p.exists():
            dates.append(ds)
            if len(dates) >= count:
                break
        d -= timedelta(days=1)
    return dates


def load_signals(date_str: str, market: str) -> List[dict]:
    p = _SIGNALS_DIR / f'{date_str}_{market}.json'
    if not p.exists():
        return []
    with open(p, encoding='utf-8') as f:
        data = json.load(f)
    sigs = data.get('signals', [])
    # 标注 market 来源
    for s in sigs:
        s['_market'] = market
    return sigs


# ═══════════════════════════════════════════════════════════════
#  分析单条信号
# ═══════════════════════════════════════════════════════════════
def analyze_signal(sig: dict) -> dict:
    """对一条信号记录运行 v2 shadow 分析。"""
    # ── 从信号 JSON 提取同源字段（与 live _fuse_signals 一致）──
    raw = sig.get('raw_scores', {})
    xmm_factor = raw.get('xmm', 0.0)
    vp_factor  = raw.get('vp', 0.0)
    llm_factor = raw.get('llm', 0.0)

    xmm_action       = sig.get('xmm_action', 'HOLD')
    xmm_position_size = sig.get('xmm_position_size', 0.0)
    vp_direction     = sig.get('vp_direction', 'HOLD')
    llm_sentiment    = sig.get('llm_sentiment', 0.0)

    # ── 旧置信度（live）──
    old_conf = sig.get('fusion_confidence', 0.0)
    level    = sig.get('fusion_level', 'HOLD')
    score    = sig.get('fusion_score', 0.0)
    # gate_approved 是后来加的字段; 不存在时标记为 None (unknown)
    gate_old = sig.get('gate_approved', None)

    # ── 计算 v2 ──
    conf_v2 = compute_confidence_v2(
        xmm_factor=xmm_factor,
        xmm_action=xmm_action,
        xmm_position_size=xmm_position_size,
        vp_factor=vp_factor,
        vp_direction=vp_direction,
        llm_factor=llm_factor,
        llm_sentiment=llm_sentiment,
    )

    gate_v2_approved, gate_v2_reasons = shadow_gate(level, score, conf_v2)

    # gate_change: 仅当 old_gate 有值时才判断翻转
    gate_change = (gate_old is not None) and (gate_old != gate_v2_approved)

    # ── 方向摘要 ──
    if xmm_action == 'HOLD' and vp_direction != 'HOLD':
        dir_note = f"XMM沉默, VP={'BUY' if vp_factor > 0 else 'SELL'}"
    elif vp_direction != 'HOLD' and xmm_action != 'HOLD':
        xd = 1 if xmm_action == 'BUY' else -1
        vd = 1 if vp_direction == 'BUY' else -1
        dir_note = '同向' if xd * vd > 0 else '分歧'
    else:
        dir_note = '中性'

    return {
        'market':  sig.get('_market', '?'),
        'symbol':  sig.get('symbol', '?'),
        'level':   level,
        'score':   score,
        'old_conf': round(old_conf, 3),
        'conf_v2': conf_v2,
        'delta':   round(conf_v2 - old_conf, 3),
        'gate_old': gate_old,
        'gate_v2': gate_v2_approved,
        'gate_change': gate_change,
        'dir_note': dir_note,
        'xmm_active': xmm_action != 'HOLD',
        'vp_active': vp_direction != 'HOLD',
        'llm_active': abs(llm_factor) > 1,
        'v2_reasons': '; '.join(gate_v2_reasons) if gate_v2_reasons else '',
    }


# ═══════════════════════════════════════════════════════════════
#  输出 — stdout（GBK-safe，无 emoji） + CSV + JSON
# ═══════════════════════════════════════════════════════════════
SEP = "=" * 120
DASH = "-" * 120


def _safe(s: str) -> str:
    """简单 sanitize：移除高码位字符（保留 ASCII + 常见中文）。"""
    return s.replace('[', '').replace(']', '')


def print_header():
    print(SEP)
    print(" 置信度 v2 Shadow Analysis — HK + US")
    print(f" 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(SEP)


def print_table(all_records: List[dict]):
    header = (
        f"{'日期':<12} {'市场':<5} {'标的':<13} {'等级':<10} {'融合分':>6} "
        f"{'旧置信度':>8} {'v2置信度':>8} {'del':>6} "
        f"{'旧Gate':>8} {'v2 Gate':>8} {'方向摘要':<30}"
    )
    print(DASH)
    print(header)
    print(DASH)

    gate_flip = 0
    for r in all_records:
        gate_old_s = 'PASS' if r['gate_old'] else ('BLOCK' if r['gate_old'] is not None else 'N/A')
        gate_v2_s  = 'PASS' if r['gate_v2']  else 'BLOCK'
        delta_s    = f"{r['delta']:+.3f}"

        # 差异解释
        if r['delta'] > 0.015:
            note = f"UP ({r['dir_note']})"
        elif r['delta'] < -0.015:
            note = f"DOWN ({r['dir_note']})"
        else:
            note = "FLAT"

        if r['gate_change']:
            note += " **GATE FLIP**"
            gate_flip += 1

        print(
            f"{r['date']:<12} {r['market']:<5} {r['symbol']:<13} {r['level']:<10} "
            f"{r['score']:>+6.1f} {r['old_conf']:>8.3f} "
            f"{r['conf_v2']:>8.3f} {delta_s:>6} "
            f"{gate_old_s:>8} {gate_v2_s:>8} {note:<30}"
        )

    print(DASH)

    avg_delta = sum(r['delta'] for r in all_records) / len(all_records)
    max_up    = max(r['delta'] for r in all_records)
    max_down  = min(r['delta'] for r in all_records)
    known   = sum(1 for r in all_records if r['gate_old'] is not None)
    unknown = len(all_records) - known
    flip_count = sum(1 for r in all_records if r['gate_change'])

    print()
    print(f"  总样本: {len(all_records)}")
    print(f"  已知旧 Gate 的样本: {known}，Gate 翻转 {flip_count}/{known}")
    print(f"  旧 Gate 未知样本: {unknown}，仅做 v2 推演，不计入翻转率")
    print(f"  平均 Delta: {avg_delta:+.4f}")
    print(f"  最大升幅: {max_up:+.4f}")
    print(f"  最大降幅: {max_down:+.4f}")

    # 方向聚合
    print(f"\n  方向聚合统计:")
    for dt in ['XMM沉默, VP偏多', 'XMM沉默, VP偏空', '分歧', '同向', '中性']:
        matched = [r for r in all_records if dt in r['dir_note']]
        if matched:
            avg_c = sum(r['conf_v2'] for r in matched) / len(matched)
            print(f"    {dt:<25}: {len(matched):>3}条, 平均v2置信度={avg_c:.3f}")


def write_csv(all_records: List[dict], path: Path):
    fieldnames = [
        'date', 'market', 'symbol', 'level', 'score',
        'old_conf', 'conf_v2', 'delta',
        'gate_old', 'gate_v2', 'gate_change',
        'xmm_active', 'vp_active', 'llm_active',
        'dir_note', 'v2_reasons',
    ]
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        for r in all_records:
            row = dict(r)
            # None → 'N/A' for CSV readability
            for k in ('gate_old',):
                if row[k] is None:
                    row[k] = 'N/A'
            w.writerow(row)
    print(f"\n  [CSV] {path}")


def write_json(all_records: List[dict], path: Path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'meta': {
                'total': len(all_records),
                'gate_known': sum(1 for r in all_records if r['gate_old'] is not None),
                'gate_unknown': sum(1 for r in all_records if r['gate_old'] is None),
                'gate_flip': sum(1 for r in all_records if r['gate_change']),
                'markets': sorted(set(r['market'] for r in all_records)),
            },
            'records': all_records,
        }, f, ensure_ascii=False, indent=2)
    print(f"  [JSON] {path}")
    print(SEP)


# ═══════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════
def main():
    # ── Fix #1: UTF-8 stdout/stderr（防 GBK UnicodeEncodeError）──
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

    # ── 确保输出目录存在 ──
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Fix #3: HK + US 最近 5 个交易日 ──
    markets = ['HK', 'US']
    all_records = []
    loaded_dates = {}

    for m in markets:
        dates = _find_available_dates(m, count=5)
        loaded_dates[m] = dates
        for ds in dates:
            signals = load_signals(ds, m)
            for sig in signals:
                rec = analyze_signal(sig)
                rec['date'] = ds
                all_records.append(rec)

    all_records.sort(key=lambda r: (r['date'], r['market'], r['symbol']))

    # ── 输出 ──
    print_header()
    print(f"  覆盖范围:")
    for m in markets:
        dates = loaded_dates.get(m, [])
        cnt = sum(1 for r in all_records if r['market'] == m)
        print(f"    {m}: {len(dates)} 个交易日 ({', '.join(dates)}) — {cnt} 条信号")

    # Crypto 检查
    crypto_dates = _find_available_dates('Crypto', count=1)
    if not crypto_dates:
        crypto_dates2 = _find_available_dates('crypto', count=1)
    if crypto_dates:
        print(f"    Crypto: 有数据可用")
    else:
        print(f"    Crypto: 无同结构信号文件")

    print(SEP)
    print_table(all_records)

    # ── Fix #4: 机器可读输出 ──
    csv_path = _OUTPUT_DIR / 'confidence_v2_shadow.csv'
    json_path = _OUTPUT_DIR / 'confidence_v2_shadow.json'
    write_csv(all_records, csv_path)
    write_json(all_records, json_path)


if __name__ == '__main__':
    main()