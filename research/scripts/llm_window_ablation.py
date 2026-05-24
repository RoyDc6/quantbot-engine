#!/usr/bin/env python3
"""
research/scripts/llm_window_ablation.py — LLM Window 消融实验

在 HK/US 最优标的上，测试 llm_window=0 vs 5 vs 20 的 IC 差异，
验证 LLM 实时推理能否提升因子预测力。

运行: python -m scripts.llm_window_ablation
"""

import sys
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, "E:\\quant\\research")
sys.path.insert(0, "E:\\quant")

import numpy as np
import pandas as pd

from research.data.futu_provider import FutuProvider
from research.factors.compute import FactorComputeEngine, FactorRegistry
from research.models.evaluation import factor_summary


# ============================================================
# 配置
# ============================================================

# 从跨市场 IC 报告选出的最优标的
SYMBOLS = {
    "HK": ["00700"],   # HK 1d |IC| 最强
    "US": ["SPY"],     # US 5d |IC| 最强
}

# LLM window 消融值
LLM_WINDOWS = [0, 5, 20]

# 仅测试 LLM 因子（技术因子作为基准不变）
LLM_FACTOR_NAMES = [
    "llm_regime", "llm_regime_confidence",
    "llm_sentiment", "llm_divergence",
    "llm_pattern", "llm_anomaly",
]

OUTPUT_DIR = Path("E:\\quant\\research\\output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_data() -> dict:
    """获取 HK + US 真实数据"""
    data_map = {}
    futu = FutuProvider()

    for market, symbols in SYMBOLS.items():
        print(f"\n📡 {market} (Futu 真实数据)...")
        for sym in symbols:
            try:
                kdata = futu.fetch_klines(sym, 300, "1d")
                data_map[sym] = kdata
                date_col = kdata.df["date"]
                print(f"  ✅ {sym}: {kdata.n_bars} bars ({date_col.iloc[0].date()} ~ {date_col.iloc[-1].date()})")
            except Exception as e:
                print(f"  ❌ {sym}: {e}")

    return data_map


def compute_llm_factors(data, factor_names, llm_window):
    """计算 LLM 因子，指定 llm_window"""
    engine = FactorComputeEngine()
    df = engine.compute_single(data, factor_names=factor_names, llm_window=llm_window)
    return df


def run_ablation(data_map: dict) -> dict:
    """运行消融实验"""
    # results[symbol][window][factor][horizon] = ic_value
    results = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    # timing[symbol][window] = elapsed_seconds
    timing = defaultdict(dict)

    for sym, kdata in data_map.items():
        market = "HK" if sym in SYMBOLS["HK"] else "US"
        close = kdata.df["close"]

        print(f"\n{'='*60}")
        print(f"📊 {sym} [{market}] — LLM Window 消融实验")
        print(f"{'='*60}")

        for window in LLM_WINDOWS:
            print(f"\n  🔄 llm_window={window}...", end=" ", flush=True)

            t0 = time.time()
            factor_df = compute_llm_factors(kdata, LLM_FACTOR_NAMES, llm_window=window)
            elapsed = time.time() - t0
            timing[sym][window] = elapsed

            print(f"({elapsed:.1f}s, {factor_df.shape[1]} factors)", end=" ", flush=True)

            for horizon_name, horizon_bars in [("1d", 1), ("5d", 5)]:
                target = close.pct_change(horizon_bars).shift(-horizon_bars)

                for col in factor_df.columns:
                    try:
                        summary = factor_summary(factor_df[col], target)
                        ic = summary["Rank_IC"]
                        results[sym][window][col][horizon_name] = ic
                    except Exception:
                        results[sym][window][col][horizon_name] = 0.0

            print("✓")

    return results, timing


def generate_report(data_map, results, timing, execution_time):
    """生成消融实验报告"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = OUTPUT_DIR / f"llm_window_ablation_{timestamp}.md"

    lines = []
    lines.append("# QuantBot LLM Window 消融实验报告")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**执行耗时**: {execution_time:.1f}s")
    lines.append(f"**LLM 模型**: meta/llama-4-maverick-17b-128e-instruct")
    lines.append(f"**数据源**: Futu OpenD (真实)")
    lines.append("")

    # ============================================================
    # 1. 实验设计
    # ============================================================
    lines.append("## 🧪 实验设计")
    lines.append("")
    lines.append("| 参数 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 标的 | {', '.join(data_map.keys())} |")
    lines.append(f"| LLM Windows | {', '.join(map(str, LLM_WINDOWS))} |")
    lines.append(f"| LLM 因子数 | {len(LLM_FACTOR_NAMES)} |")
    lines.append(f"| 前向收益 | 1d, 5d |")
    lines.append("")

    # ============================================================
    # 2. 执行耗时
    # ============================================================
    lines.append("## ⏱ 执行耗时")
    lines.append("")
    lines.append("| 标的 | llm_window=0 | llm_window=5 | llm_window=20 |")
    lines.append("|------|-------------|-------------|--------------|")
    for sym in data_map:
        row = [sym]
        for w in LLM_WINDOWS:
            t = timing.get(sym, {}).get(w, 0)
            row.append(f"{t:.1f}s")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # ============================================================
    # 3. 逐标的 IC 对比
    # ============================================================
    lines.append("## 📊 逐标的 IC 对比")
    lines.append("")

    for sym in data_map:
        market = "HK" if sym in SYMBOLS["HK"] else "US"
        lines.append(f"### {sym} [{market}]")
        lines.append("")

        for horizon in ["1d", "5d"]:
            lines.append(f"#### 前向收益: {horizon}")
            lines.append("")
            lines.append("| 因子 | IC(w=0) | IC(w=5) | IC(w=20) | Δ(w=5) | Δ(w=20) | 最佳窗口 |")
            lines.append("|------|---------|---------|----------|--------|---------|----------|")

            for factor in LLM_FACTOR_NAMES:
                ic_w0 = results[sym][0].get(factor, {}).get(horizon, 0.0)
                ic_w5 = results[sym][5].get(factor, {}).get(horizon, 0.0)
                ic_w20 = results[sym][20].get(factor, {}).get(horizon, 0.0)

                delta_5 = ic_w5 - ic_w0
                delta_20 = ic_w20 - ic_w0

                # 找最佳窗口（按 |IC|）
                abs_ics = {0: abs(ic_w0), 5: abs(ic_w5), 20: abs(ic_w20)}
                best_w = max(abs_ics, key=abs_ics.get)

                lines.append(
                    f"| {factor} | {ic_w0:+.4f} | {ic_w5:+.4f} | {ic_w20:+.4f} "
                    f"| {delta_5:+.4f} | {delta_20:+.4f} | w={best_w} |"
                )

            lines.append("")

    # ============================================================
    # 4. 汇总统计
    # ============================================================
    lines.append("## 📈 汇总统计")
    lines.append("")

    # 按窗口聚合所有 IC
    window_ics = defaultdict(list)
    window_abs_ics = defaultdict(list)
    for sym in data_map:
        for w in LLM_WINDOWS:
            for factor in LLM_FACTOR_NAMES:
                for horizon in ["1d", "5d"]:
                    ic = results[sym][w].get(factor, {}).get(horizon, 0.0)
                    window_ics[w].append(ic)
                    window_abs_ics[w].append(abs(ic))

    lines.append("| 指标 | w=0 | w=5 | w=20 |")
    lines.append("|------|-----|-----|------|")
    for metric_name, data_dict in [("平均 IC", window_ics), ("平均 |IC|", window_abs_ics)]:
        row = [metric_name]
        for w in LLM_WINDOWS:
            vals = data_dict[w]
            row.append(f"{np.mean(vals):.4f}")
        lines.append("| " + " | ".join(row) + " |")

    # 正 IC 比例
    lines.append("| 正 IC 比例 | " + " | ".join(
        f"{np.mean(np.array(window_ics[w]) > 0):.1%}" for w in LLM_WINDOWS
    ) + " |")

    # 改进统计
    lines.append("")
    lines.append("### LLM 增强改进统计")
    lines.append("")
    lines.append("| 对比 | 改进次数 | 总次数 | 改进比例 | 平均 Δ|IC| |")
    lines.append("|------|---------|-------|---------|-----------|")

    for baseline_w, target_w in [(0, 5), (0, 20)]:
        improved = 0
        total = 0
        deltas = []
        for sym in data_map:
            for factor in LLM_FACTOR_NAMES:
                for horizon in ["1d", "5d"]:
                    ic_base = abs(results[sym][baseline_w].get(factor, {}).get(horizon, 0.0))
                    ic_target = abs(results[sym][target_w].get(factor, {}).get(horizon, 0.0))
                    total += 1
                    delta = ic_target - ic_base
                    deltas.append(delta)
                    if delta > 0:
                        improved += 1

        lines.append(
            f"| w={baseline_w} → w={target_w} | {improved} | {total} "
            f"| {improved/total*100:.1f}% | {np.mean(deltas):+.4f} |"
        )

    lines.append("")

    # ============================================================
    # 5. 结论
    # ============================================================
    lines.append("## 🎯 结论")
    lines.append("")

    # 找最佳窗口
    best_window = max(LLM_WINDOWS, key=lambda w: np.mean(window_abs_ics[w]))
    best_mean_abs = np.mean(window_abs_ics[best_window])

    lines.append(f"1. **最佳 LLM Window**: w={best_window}，平均 |IC| = {best_mean_abs:.4f}")
    lines.append(f"2. **改进效果**: w=0 → w=5 和 w=0 → w=20 的 IC 变化方向")
    lines.append(f"3. **因子级差异**: 不同因子对 LLM window 的敏感度不同")
    lines.append(f"4. **成本考量**: w=20 的 LLM 调用成本是 w=5 的 4 倍，需评估性价比")
    lines.append(f"5. **下一步**: 若 w=5 已足够，可扩展到更多标的验证稳定性")

    lines.append("")

    report_content = "\n".join(lines)
    report_path.write_text(report_content, encoding="utf-8")
    print(f"\n📄 消融实验报告: {report_path}")
    return report_path


def main():
    start_time = time.time()

    print("=" * 60)
    print("🔬 QuantBot LLM Window 消融实验")
    print(f"   标的: {', '.join(SYMBOLS['HK'] + SYMBOLS['US'])}")
    print(f"   LLM Windows: {LLM_WINDOWS}")
    print(f"   LLM 因子: {len(LLM_FACTOR_NAMES)}")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 获取数据
    print("\n📡 阶段 1/3: 获取数据")
    data_map = fetch_data()
    if not data_map:
        print("❌ 无有效数据，退出")
        return 1

    # 2. 运行消融实验
    print("\n🧮 阶段 2/3: 运行消融实验")
    print(f"\n  预计 LLM 调用: {len(data_map) * len(LLM_FACTOR_NAMES) * (LLM_WINDOWS[-1] + LLM_WINDOWS[1])} 次")
    print(f"  (w=5: {len(data_map) * len(LLM_FACTOR_NAMES) * 5} 次, "
          f"w=20: {len(data_map) * len(LLM_FACTOR_NAMES) * 20} 次)")
    results, timing = run_ablation(data_map)

    # 3. 生成报告
    print("\n📄 阶段 3/3: 生成报告")
    elapsed = time.time() - start_time
    report_path = generate_report(data_map, results, timing, elapsed)

    # 4. 终端摘要
    print(f"\n{'='*60}")
    print("📋 消融实验摘要")
    print(f"{'='*60}")
    print(f"  执行耗时: {elapsed:.1f}s")

    for sym in data_map:
        print(f"\n  {sym}:")
        for w in LLM_WINDOWS:
            ics = [results[sym][w].get(f, {}).get("1d", 0) for f in LLM_FACTOR_NAMES]
            abs_ics = [abs(v) for v in ics]
            print(f"    w={w}: 平均 IC={np.mean(ics):+.4f} 平均 |IC|={np.mean(abs_ics):.4f}")

    print(f"\n  报告: {report_path}")
    print(f"\n  🎉 消融实验完成!")
    return 0


if __name__ == "__main__":
    sys.exit(main())