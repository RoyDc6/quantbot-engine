# -*- coding: utf-8 -*-
"""
Dual-Strategy Fusion Scanner v2
多策略信号融合与置信度打分系统 v2

修复 v1 问题：
  - v1 用 OR 逻辑，矛盾信号（如TOP+BUY）被错误吞掉
  - v2 改用 AND 逻辑：方向冲突时 → 标记为 CONFLICT，降低置信度
  - 矛盾信号不参与推荐

缠论分型 + 徐小明策略 → 统一置信度 + 分级推荐

Author: AiGobot
Date: 2026-04-17
"""
import os, sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from core.paths import PROJECT_ROOT
from core.paths import OUTPUT_DIR as _OUTPUT_DIR
BASE_DIR   = PROJECT_ROOT
OUTPUT_DIR = _OUTPUT_DIR

SKILL_DIR  = Path('C:/Users/RoyGoode/.workbuddy/skills/xmm-strategy/scripts')

sys.path.insert(0, str(SKILL_DIR))
sys.stdout.reconfigure(encoding='utf-8')


# ═══════════════════════════════════════════════════════════════════════════════
# FUSION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class FusionEngine:
    """
    融合打分规则：

    方向分映射：
      缠论:  BOTTOM → +1,  TOP → -1,  其他 → 0
      XMM:   BUY → +1,   SELL → -1,  HOLD → 0

    方向一致性（最高60分）：
      f_dir == x_dir != 0  → 60分（共振）
      两者同号但有一方=0    → 35分（单方确认）
      f_dir != x_dir != 0   → 0分（矛盾 → CONFLICT）

    XMM强度（最高20分）：
      strength=3 → 20分，strength=2 → 14分，strength=1 → 7分，strength=0 → 0分

    RSI极值区（最高10分）：
      RSI≤30 → +5（超卖），RSI≥70 → +5（超买），RSI≤40/≥60 → +3

    TD9极值（最高10分）：
      TD9≤-7（买入极值）且方向为多 → +8
      TD9≥+7（卖出极值）且方向为空 → +8
      其余极值 → +3

    MACD结构（最高10分）：
      底背离 → +8（多头），顶背离 → +8（空头）
      上MACD（多头结构）→ +4
      下MACD（空头结构）→ +4

    动量一致性（最高10分）：
      5日+20日动量同向且与方向一致 → +6
      有一项同向 → +3

    推荐等级：
      CONFIDENCE ≥ 75 → STRONG_*
      CONFIDENCE ≥ 55 → BUY/SELL
      CONFIDENCE ≥ 35 → WATCH_*
    """

    @staticmethod
    def fractal_dir(frac_type):
        if frac_type == 'BOTTOM': return 1
        if frac_type == 'TOP':    return -1
        return 0

    @staticmethod
    def xmm_dir(signal):
        if signal == 'BUY':  return 1
        if signal == 'SELL': return -1
        return 0

    @staticmethod
    def rsi_score(rsi, direction):
        if rsi is None or pd.isna(rsi): return 0
        if direction > 0:
            if rsi <= 30: return 5
            if rsi <= 40: return 3
        elif direction < 0:
            if rsi >= 70: return 5
            if rsi >= 60: return 3
        return 0

    @staticmethod
    def td9_score(td9, direction):
        if td9 is None or pd.isna(td9): return 0
        if direction > 0:
            if td9 <= -7: return 8
            if td9 <= -4: return 4
        elif direction < 0:
            if td9 >= 7:  return 8
            if td9 >= 4:  return 4
        return 0

    @staticmethod
    def macd_score(macd_desc, direction):
        if not macd_desc or pd.isna(macd_desc): return 0
        desc = str(macd_desc)
        if direction > 0:
            if '底背离' in desc: return 8
            if '上MACD' in desc: return 4
        elif direction < 0:
            if '顶背离' in desc: return 8
            if '下MACD' in desc: return 4
        return 0

    @staticmethod
    def momentum_score(mom5, mom20, direction):
        if any(pd.isna(x) for x in [mom5, mom20]): return 0
        if direction > 0:
            if mom5 > 0 and mom20 > 0: return 6
            if mom5 > 0 or mom20 > 0: return 3
        elif direction < 0:
            if mom5 < 0 and mom20 < 0: return 6
            if mom5 < 0 or mom20 < 0: return 3
        return 0

    @classmethod
    def compute(cls, frac_type, xmm_signal, xmm_strength, td9, macd_desc, rsi, mom5, mom20):
        """
        返回 dict:
          direction, confidence, strength, conflict,
          agree_score, xmm_score, rsi_score, td9_score, macd_score, mom_score
        """
        f_dir = cls.fractal_dir(frac_type)
        x_dir = cls.xmm_dir(xmm_signal)

        # ── 方向一致性 ────────────────────────────────────────────────────
        if f_dir != 0 and x_dir != 0:
            if f_dir == x_dir:
                agree_score = 60
                direction   = f_dir
                conflict    = False
            else:
                agree_score = 0
                direction   = 0   # 矛盾 → 无方向
                conflict    = True
        elif f_dir != 0:
            agree_score = 35; direction = f_dir; conflict = False
        elif x_dir != 0:
            agree_score = 35; direction = x_dir; conflict = False
        else:
            agree_score = 0;  direction = 0;  conflict = False

        if direction == 0:
            return {
                'direction': 0, 'confidence': 0, 'strength': 'NEUTRAL', 'conflict': conflict,
                'agree_score': 0, 'xmm_score': 0, 'rsi_score': 0,
                'td9_score': 0, 'macd_score': 0, 'mom_score': 0,
            }

        # ── 各因子得分 ────────────────────────────────────────────────────
        xmm_score  = min(int(xmm_strength or 0) * 7, 20)
        rsi_score  = cls.rsi_score(rsi, direction)
        td9_score  = cls.td9_score(td9, direction)
        macd_score = cls.macd_score(macd_desc, direction)
        mom_score  = cls.momentum_score(mom5, mom20, direction)

        confidence = min(agree_score + xmm_score + rsi_score + td9_score
                        + macd_score + mom_score, 100)

        # ── 推荐等级 ────────────────────────────────────────────────────
        if direction > 0:
            if confidence >= 75:  strength = 'STRONG_BUY'
            elif confidence >= 55: strength = 'BUY'
            elif confidence >= 35: strength = 'WATCH_BUY'
            else:                  strength = 'SPECULATIVE'
        else:
            if confidence >= 75:  strength = 'STRONG_SELL'
            elif confidence >= 55: strength = 'SELL'
            elif confidence >= 35: strength = 'WATCH_SELL'
            else:                  strength = 'SPECULATIVE'

        return {
            'direction':   int(direction),
            'confidence': int(round(confidence)),
            'strength':    strength,
            'conflict':    conflict,
            'agree_score': int(agree_score),
            'xmm_score':  int(xmm_score),
            'rsi_score':  int(rsi_score),
            'td9_score':  int(td9_score),
            'macd_score': int(macd_score),
            'mom_score':  int(mom_score),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN SCAN
# ═══════════════════════════════════════════════════════════════════════════════

def find_latest_files():
    """找最新的 fracta + xmm CSV"""
    frac_files = sorted(OUTPUT_DIR.glob('frac_all_SPX_*.csv'), key=lambda f: f.stat().st_mtime)
    if not frac_files:
        return None, None
    frac_f = frac_files[-1]
    # 从文件名提取时间戳 e.g. frac_all_SPX_20260417_112527
    ts = frac_f.stem.split('_', 3)[-1]
    xmm_f = OUTPUT_DIR / f'xmm_all_SPX_{ts}.csv'
    if not xmm_f.exists():
        candidates = sorted(OUTPUT_DIR.glob('xmm_all_SPX_*.csv'), key=lambda f: f.stat().st_mtime)
        if candidates: xmm_f = candidates[-1]
    print(f"[INFO] Fractal: {frac_f.name}")
    print(f"[INFO] XMM:     {xmm_f.name}")
    return pd.read_csv(frac_f), pd.read_csv(xmm_f)


def fuse(frac_df, xmm_df):
    """合并两策略结果 + 融合打分"""
    merged = pd.merge(
        frac_df, xmm_df,
        on='code', how='outer',
        suffixes=('_f', '_x')
    )
    records = []
    for _, row in merged.iterrows():
        r = FusionEngine.compute(
            frac_type     = row.get('fractal_type_f', row.get('fractal_type')),
            xmm_signal    = row.get('signal_x',        row.get('signal')),
            xmm_strength  = row.get('strength_x',       row.get('strength')),
            td9           = row.get('td9_count_x',     row.get('td9_count')),
            macd_desc     = row.get('macd_desc_x',     row.get('macd_desc')),
            rsi           = row.get('rsi_x',           row.get('rsi')),
            mom5          = row.get('momentum_5d_x',   row.get('momentum_5d')),
            mom20         = row.get('momentum_20d_x',  row.get('momentum_20d')),
        )
        close = row.get('close_f', row.get('close_x', row.get('close')))
        records.append({
            'code':             row.get('code', ''),
            'close':            close,
            'latest_date':      row.get('latest_date_f', row.get('latest_date_x')),

            # 缠论
            'fractal_type':     row.get('fractal_type_f', row.get('fractal_type')),
            'fractal_price':    row.get('fractal_price_f', row.get('fractal_price')),
            'top_count':        row.get('top_count_f',    row.get('top_count')),
            'bottom_count':     row.get('bottom_count_f', row.get('bottom_count')),

            # XMM
            'xmm_signal':       row.get('signal_x',        row.get('signal')),
            'xmm_strength':     row.get('strength_x',      row.get('strength')),
            'td9_count':        row.get('td9_count_x',     row.get('td9_count')),
            'macd_desc':        row.get('macd_desc_x',     row.get('macd_desc')),
            'resonance':        row.get('resonance_x',     row.get('resonance')),
            'rsi':              row.get('rsi_x',           row.get('rsi')),
            'momentum_5d':     row.get('momentum_5d_x',  row.get('momentum_5d')),
            'momentum_20d':    row.get('momentum_20d_x', row.get('momentum_20d')),

            # 融合结果
            'fusion_direction':  r['direction'],
            'fusion_confidence': r['confidence'],
            'fusion_strength':   r['strength'],
            'conflict':          r['conflict'],
            'agree_score':       r['agree_score'],
            'xmm_score':         r['xmm_score'],
            'rsi_score':         r['rsi_score'],
            'td9_score':         r['td9_score'],
            'macd_score':        r['macd_score'],
            'mom_score':         r['mom_score'],
        })

    return pd.DataFrame(records)


def save_results(df, ts):
    """保存各级别结果"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    df.to_csv(OUTPUT_DIR / f'fusion_all_{ts}.csv', index=False, encoding='utf-8-sig')

    buckets = ['STRONG_BUY', 'BUY', 'WATCH_BUY', 'NEUTRAL',
               'WATCH_SELL', 'SELL', 'STRONG_SELL', 'SPECULATIVE', 'CONFLICT']
    saved = []
    for b in buckets:
        sub = df[df.fusion_strength == b].sort_values('fusion_confidence', ascending=False)
        if len(sub):
            sub.to_csv(OUTPUT_DIR / f'fusion_{b}_{ts}.csv', index=False, encoding='utf-8-sig')
            saved.append((b, len(sub)))
    return saved


def print_report(df, conflict_cnt, ts, saved):
    """打印融合报告"""
    total = len(df)
    neutral_cnt  = len(df[df.fusion_strength == 'NEUTRAL'])

    # 分组
    def g(label): return df[df.fusion_strength == label].sort_values('fusion_confidence', ascending=False)
    sb, b, wb, ns, ws, s, ss, sp = [g(x) for x in
        ['STRONG_BUY','BUY','WATCH_BUY','NEUTRAL','WATCH_SELL','SELL','STRONG_SELL','SPECULATIVE']]

    def pct(n): return f"{n/total*100:.1f}%" if total else "0.0%"

    print()
    print("=" * 82)
    print(f"  📊 双策略融合扫描报告 v2  ·  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  数据范围: S&P 500 成分股 ({total} 只已覆盖)")
    print("=" * 82)

    # ── 总览 ──────────────────────────────────────────────────────────────
    print(f"\n{'─'*82}")
    print(f"  【融合总览 — 共 {total} 只】")
    print(f"{'─'*82}")
    print(f"  {'等级':<18} {'数量':>5} {'占比':>8}  {'说明'}")
    print(f"  {'-'*60}")
    for label, cnt, desc in [
        ('⭐ STRONG_BUY',  len(sb), '缠底+XMM买 + 高置信度 → 重点买入'),
        ('▲ BUY',          len(b),  '缠底+XMM买，置信度中等 → 买入'),
        ('◆ WATCH_BUY',   len(wb), '缠底|XMM买，仅一方确认 → 观望'),
        ('⚪ NEUTRAL',     len(ns), '两策略均无方向 → 保持观望'),
        ('◇ SPECULATIVE',  len(sp), '有方向但置信度低 → 谨慎'),
        ('▼ WATCH_SELL',   len(ws), '缠顶|XMM卖，仅一方确认 → 观望'),
        ('▽ SELL',         len(s),  '缠顶+XMM卖，置信度中等 → 卖出'),
        ('✖ STRONG_SELL', len(ss), '缠顶+XMM卖 + 高置信度 → 重点卖出'),
    ]:
        print(f"  {label:<18} {cnt:>5} ({pct(cnt):>6})  {desc}")

    print(f"\n  ⚠️ 策略冲突（矛盾信号，自动排除）: {conflict_cnt} 只")
    print(f"     冲突原因：缠论分型方向与XMM信号方向相反")

    # ── 策略一致性 ─────────────────────────────────────────────────────────
    print(f"\n{'─'*82}")
    print(f"  【策略一致性统计】")
    print(f"{'─'*82}")

    both_bull = df[(df.fractal_type == 'BOTTOM') & (df.xmm_signal == 'BUY')]
    both_bear = df[(df.fractal_type == 'TOP')    & (df.xmm_signal == 'SELL')]
    frac_only_bull = df[(df.fractal_type == 'BOTTOM') & (df.xmm_signal == 'HOLD')]

    conflict_bull = df[(df.fractal_type == 'TOP')    & (df.xmm_signal == 'BUY')]
    conflict_bear = df[(df.fractal_type == 'BOTTOM') & (df.xmm_signal == 'SELL')]

    print(f"  🟢 缠论底 + XMM BUY  →  {len(both_bull):3d} 只（两策略多头共振）")
    print(f"  🔴 缠论顶 + XMM SELL →  {len(both_bear):3d} 只（两策略空头共振）")
    print(f"  🟡 缠论底 + XMM HOLD →  {len(frac_only_bull):3d} 只（仅缠论信号）")
    print(f"  ⚡ 矛盾: 缠论顶+XMM买  →  {len(conflict_bull):3d} 只 ⚠️")
    print(f"  ⚡ 矛盾: 缠论底+XMM卖  →  {len(conflict_bear):3d} 只 ⚠️")

    # ── ⭐ STRONG_BUY ────────────────────────────────────────────────────
    if len(sb):
        print(f"\n{'─'*82}")
        print(f"  ⭐ STRONG_BUY（{len(sb)} 只）— 两策略多头共振 + 高置信度（≥75分）")
        print(f"{'─'*82}")
        print(f"  {'代码':<10} {'置信':>4} {'XMM强度':>6} {'TD9':>5} {'RSI':>6} {'5日%':>8} {'20日%':>8} {'共振':>4}  备注")
        print(f"  {'-'*80}")
        for _, r in sb.iterrows():
            notes = []
            if r.get('resonance', 0) >= 3: notes.append('共振3')
            if (r.get('td9_count', 0) or 0) <= -7: notes.append('TD9极')
            if r.get('rsi') and r['rsi'] <= 30: notes.append('RSI<30')
            if '底背离' in str(r.get('macd_desc','')): notes.append('底背离')
            print(f"  {r['code']:<10} {r['fusion_confidence']:>4} "
                  f"{int(r['xmm_strength'] or 0):>6} "
                  f"{int(r['td9_count'] or 0):>5} "
                  f"{str(round(r['rsi'],1) if r.get('rsi') else 'N/A'):>6} "
                  f"{float(r['momentum_5d']  or 0):>+8.1f} "
                  f"{float(r['momentum_20d'] or 0):>+8.1f} "
                  f"{int(r['resonance'] or 0):>4}  "
                  f"{','.join(notes)}")

    # ── ✖ STRONG_SELL ───────────────────────────────────────────────────
    if len(ss):
        print(f"\n{'─'*82}")
        print(f"  ✖ STRONG_SELL（{len(ss)} 只）— 两策略空头共振 + 高置信度（≥75分）")
        print(f"{'─'*82}")
        print(f"  {'代码':<10} {'置信':>4} {'XMM强度':>6} {'TD9':>5} {'RSI':>6} {'5日%':>8} {'20日%':>8} {'共振':>4}  备注")
        print(f"  {'-'*80}")
        for _, r in ss.iterrows():
            notes = []
            if r.get('resonance', 0) >= 3: notes.append('共振3')
            if (r.get('td9_count', 0) or 0) >= 7: notes.append('TD9极')
            if r.get('rsi') and r['rsi'] >= 70: notes.append('RSI>70')
            if '顶背离' in str(r.get('macd_desc','')): notes.append('顶背离')
            print(f"  {r['code']:<10} {r['fusion_confidence']:>4} "
                  f"{int(r['xmm_strength'] or 0):>6} "
                  f"{int(r['td9_count'] or 0):>5} "
                  f"{str(round(r['rsi'],1) if r.get('rsi') else 'N/A'):>6} "
                  f"{float(r['momentum_5d']  or 0):>+8.1f} "
                  f"{float(r['momentum_20d'] or 0):>+8.1f} "
                  f"{int(r['resonance'] or 0):>4}  "
                  f"{','.join(notes)}")

    # ── ▲ BUY / ▽ SELL ──────────────────────────────────────────────────
    if len(b):
        print(f"\n{'─'*82}")
        print(f"  ▲ BUY（{len(b)} 只）— 两策略多头共振，置信度中等（55-74分）")
        print(f"{'─'*82}")
        print(f"  {'代码':<10} {'置信':>4} {'XMM强度':>6} {'TD9':>5} {'RSI':>6} {'5日%':>8} {'20日%':>8} {'共振':>4}")
        print(f"  {'-'*75}")
        for _, r in b.iterrows():
            print(f"  {r['code']:<10} {r['fusion_confidence']:>4} "
                  f"{int(r['xmm_strength'] or 0):>6} "
                  f"{int(r['td9_count'] or 0):>5} "
                  f"{str(round(r['rsi'],1) if r.get('rsi') else 'N/A'):>6} "
                  f"{float(r['momentum_5d']  or 0):>+8.1f} "
                  f"{float(r['momentum_20d'] or 0):>+8.1f} "
                  f"{int(r['resonance'] or 0):>4}")

    if len(s):
        print(f"\n{'─'*82}")
        print(f"  ▽ SELL（{len(s)} 只）— 两策略空头共振，置信度中等（55-74分）")
        print(f"{'─'*82}")
        print(f"  {'代码':<10} {'置信':>4} {'XMM强度':>6} {'TD9':>5} {'RSI':>6} {'5日%':>8} {'20日%':>8} {'共振':>4}")
        print(f"  {'-'*75}")
        for _, r in s.iterrows():
            print(f"  {r['code']:<10} {r['fusion_confidence']:>4} "
                  f"{int(r['xmm_strength'] or 0):>6} "
                  f"{int(r['td9_count'] or 0):>5} "
                  f"{str(round(r['rsi'],1) if r.get('rsi') else 'N/A'):>6} "
                  f"{float(r['momentum_5d']  or 0):>+8.1f} "
                  f"{float(r['momentum_20d'] or 0):>+8.1f} "
                  f"{int(r['resonance'] or 0):>4}")

    # ── 因子分解 ─────────────────────────────────────────────────────────
    if len(sb) >= 3:
        print(f"\n{'─'*82}")
        print(f"  【STRONG_BUY 置信度因子分解】")
        print(f"{'─'*82}")
        print(f"  {'代码':<10} {'总分':>4} {'方向一致(60)':>11} {'XMM强度(20)':>10} {'RSI(10)':>7} {'TD9(10)':>7} {'MACD(10)':>8} {'动量(10)':>8}")
        print(f"  {'-'*78}")
        for _, r in sb.head(10).iterrows():
            print(f"  {r['code']:<10} {r['fusion_confidence']:>4} "
                  f"{r['agree_score']:>11} "
                  f"{r['xmm_score']:>10} "
                  f"{r['rsi_score']:>7} "
                  f"{r['td9_score']:>7} "
                  f"{r['macd_score']:>8} "
                  f"{r['mom_score']:>8}")

    # ── 保存信息 ─────────────────────────────────────────────────────────
    print(f"\n{'='*82}")
    print(f"  【已保存文件】")
    print(f"  {'='*82}")
    for label, cnt in saved:
        print(f"    fusion_{label}_{ts}.csv  ({cnt} rows)")
    print(f"\n  融合算法: AND逻辑（两策略方向必须一致才有效）")
    print(f"           方向一致(最高60) + XMM强度(最高20) + RSI/TD9/MACD/动量各(最高10)")
    print(f"  报告时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*82}\n")


def main():
    print("=" * 62)
    print("  多策略信号融合与置信度打分系统 v2")
    print("  缠论分型 + 徐小明策略 → AND逻辑 + 因子加权置信度")
    print("=" * 62)

    frac_df, xmm_df = find_latest_files()
    if frac_df is None:
        print("[ERROR] 未找到扫描结果，请先运行 SPX 扫描")
        return

    print(f"\n[1/3] 融合打分中 ({len(frac_df)} 缠论 + {len(xmm_df)} XMM)...")
    fusion_df = fuse(frac_df, xmm_df)
    print(f"  → {len(fusion_df)} 只股票完成融合打分")

    # 分离冲突信号（注意：在干净数据中筛选）
    clean_df     = fusion_df[fusion_df.conflict == False].copy()
    conflict_df  = fusion_df[fusion_df.conflict == True].copy()
    conflict_cnt = len(conflict_df)

    if conflict_cnt:
        print(f"  → 发现 {conflict_cnt} 只策略冲突信号（两策略方向相反，已排除推荐）")
        ts_conflict = datetime.now().strftime('%Y%m%d_%H%M%S')
        conflict_df.to_csv(OUTPUT_DIR / f'fusion_CONFLICT_{ts_conflict}.csv',
                           index=False, encoding='utf-8-sig')
        print(f"    → fusion_CONFLICT_{ts_conflict}.csv")

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    print(f"\n[2/3] 保存结果...")
    saved = save_results(clean_df, ts)
    for label, cnt in saved:
        print(f"  → fusion_{label}_{ts}.csv  ({cnt} rows)")

    print(f"\n[3/3] 生成报告...")
    print_report(clean_df, conflict_cnt, ts, saved)


if __name__ == '__main__':
    main()
