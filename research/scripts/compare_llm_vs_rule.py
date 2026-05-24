#!/usr/bin/env python3
"""
research/scripts/compare_llm_vs_rule.py — LLM vs 规则回退因子对比

在同一标的上分别计算 llm_window=0 (规则回退) 和 llm_window>0 (LLM 增强)
的因子值，对比 IC 表现差异。

运行: python compare_llm_vs_rule.py
"""

import sys
import time
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, "E:\\quant\\research")
sys.path.insert(0, "E:\\quant")

import numpy as np
import pandas as pd

from research.data.base import KLineData
from research.data.okx_provider import OKXProvider
from research.factors.compute import FactorComputeEngine, FactorRegistry
from research.models.evaluation import calc_ic, factor_summary


# ============================================================
# 配置
# ============================================================

# 使用真实加密货币数据（仅 2 个标的，控制 LLM 调用成本）
CRYPTO_SYMBOLS = ["BTC-USDT", "ETH-USDT"]

# LLM window 设置
LLM_WINDOW = 5  # 最近 5 根 K 线调用 LLM

OUTPUT_DIR = Path("E:\\quant\\research\\output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_crypto_data() -> dict:
    """获取加密货币真实数据"""
    data_map = {}
    okx = OKXProvider()
    for sym in CRYPTO_SYMBOLS:
        try:
            kdata = okx.fetch_klines(sym, 300, "1d")
            data_map[sym] = kdata
            print(f"  ✅ {sym}: {kdata.n_bars} bars")
        except Exception as e:
            print(f"  ❌ {sym}: {e}")
    return data_map


def compute_llm_factors(data: KLineData, llm_window: int) -> pd.DataFrame:
    """计算 LLM 因子，指定 llm_window"""
    engine = FactorComputeEngine()
    llm_factors = [m.name for m in FactorRegistry.list_factors(category="llm")]
    df = engine.compute_single(data, factor_names=llm_factors, llm_window=llm_window)
    return df


def compare_ic(data_map: dict) -> dict:
    """对比规则回退 vs LLM 增强的 IC"""
    results = {}

    for sym, kdata in data_map.items():
        print(f"\n{'='*60}")
        print(f"📊 {sym} — LLM vs 规则回退对比")
        print(f"{'='*60}")

        close = kdata.df["close"]

        # 阶段 1: 规则回退 (llm_window=0)
        print(f"\n阶段 1/3: 规则回退 (llm_window=0)")
        t0 = time.time()
        rule_df = compute_llm_factors(kdata, llm_window=0)
        t_rule = time.time() - t0
        print(f"  耗时: {t_rule:.1f}s")

        # 阶段 2: LLM 增强 (llm_window={LLM_WINDOW})
        print(f"\n阶段 2/3: LLM 增强 (llm_window={LLM_WINDOW})")
        print(f"  预计 {len(rule_df.columns) * LLM_WINDOW} 次 LLM 调用 @ ~7s/次 ≈ "
              f"{len(rule_df.columns) * LLM_WINDOW * 7 // 60} 分钟")
        t0 = time.time()
        llm_df = compute_llm_factors(kdata, llm_window=LLM_WINDOW)
        t_llm = time.time() - t0
        print(f"  耗时: {t_llm:.1f}s")

        # 阶段 3: IC 对比
        print(f"\n阶段 3/3: IC 对比")
        results[sym] = {}

        for horizon_name, horizon_bars in [("ret_1d_fwd", 1), ("ret_5d_fwd", 5)]:
            target = close.pct_change(horizon_bars).shift(-horizon_bars)

            ic_comparison = {}
            for col in rule_df.columns:
                if col not in llm_df.columns:
                    continue

                # 规则回退 IC
                try:
                    rule_summary = factor_summary(rule_df[col], target)
                    rule_ic = rule_summary["Rank_IC"]
                except Exception as e:
                    rule_ic = 0.0

                # LLM 增强 IC
                try:
                    llm_summary = factor_summary(llm_df[col], target)
                    llm_ic = llm_summary["Rank_IC"]
                except Exception as e:
                    llm_ic = 0.0

                delta = llm_ic - rule_ic

                ic_comparison[col] = {
                    "rule_ic": rule_ic,
                    "llm_ic": llm_ic,
                    "delta": delta,
                }

                direction = "✅" if abs(llm_ic) > abs(rule_ic) else "❌"
                print(f"  {direction} {col} ({horizon_name}): "
                      f"规则 IC={rule_ic:+.4f} → LLM IC={llm_ic:+.4f} (Δ={delta:+.4f})")

            results[sym][horizon_name] = ic_comparison

    return results


def generate_report(data_map: dict, comparison_results: dict,
                    execution_time: float):
    """生成对比报告"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = OUTPUT_DIR / f"llm_vs_rule_comparison_{timestamp}.md"

    lines = []
    lines.append("# QuantBot LLM vs 规则回退因子对比报告")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**执行耗时**: {execution_time:.1f}s")
    lines.append(f"**LLM Window**: {LLM_WINDOW} bars")
    lines.append(f"**LLM 模型**: meta/llama-4-maverick-17b-128e-instruct")
    lines.append("")

    for sym in comparison_results:
        lines.append(f"## {sym}")
        lines.append("")
        lines.append("| 因子 | 目标 | 规则 IC | LLM IC | ΔIC | 改进? |")
        lines.append("|------|------|---------|--------|------|-------|")
        for horizon_name in comparison_results[sym]:
            for factor_name, ic_info in comparison_results[sym][horizon_name].items():
                improved = "✅" if abs(ic_info["llm_ic"]) > abs(ic_info["rule_ic"]) else "❌"
                lines.append(
                    f"| {factor_name} | {horizon_name} "
                    f"| {ic_info['rule_ic']:+.4f} "
                    f"| {ic_info['llm_ic']:+.4f} "
                    f"| {ic_info['delta']:+.4f} "
                    f"| {improved} |"
                )
        lines.append("")

    # 汇总统计
    lines.append("## 汇总统计")
    lines.append("")
    all_deltas = []
    improved_count = 0
    total_count = 0
    for sym in comparison_results:
        for horizon_name in comparison_results[sym]:
            for factor_name, ic_info in comparison_results[sym][horizon_name].items():
                all_deltas.append(ic_info["delta"])
                total_count += 1
                if abs(ic_info["llm_ic"]) > abs(ic_info["rule_ic"]):
                    improved_count += 1

    if all_deltas:
        lines.append(f"- **改进比例**: {improved_count}/{total_count} ({improved_count/total_count*100:.1f}%)")
        lines.append(f"- **平均 Δ|IC|**: {np.mean(np.abs(all_deltas)):.4f}")
        lines.append(f"- **最大 Δ|IC|**: {np.max(np.abs(all_deltas)):.4f}")

    lines.append("")
    lines.append("### 结论")
    lines.append("")
    lines.append("1. **LLM 增强效果**: 对比规则回退与 LLM 增强的 IC 差异")
    lines.append("2. **因子级分析**: 哪些因子从 LLM 增强中受益最大")
    lines.append("3. **下一步**: 扩展到更多标的和更大 llm_window")

    report_content = "\n".join(lines)
    report_path.write_text(report_content, encoding="utf-8")
    print(f"\n📄 对比报告: {report_path}")
    return report_path


def main():
    start_time = time.time()

    print("=" * 60)
    print("🔬 QuantBot LLM vs 规则回退因子对比")
    print(f"  标的: {CRYPTO_SYMBOLS}")
    print(f"  LLM Window: {LLM_WINDOW}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 获取数据
    print("\n📡 获取数据...")
    data_map = fetch_crypto_data()

    # 2. 对比 IC
    comparison_results = compare_ic(data_map)

    # 3. 生成报告
    elapsed = time.time() - start_time
    report_path = generate_report(data_map, comparison_results, elapsed)

    print(f"\n{'='*60}")
    print("🎉 对比完成!")
    print(f"  报告: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())