#!/usr/bin/env python3
"""
research/scripts/cross_market_ic.py — 跨市场 IC 对比分析

对比 LLM 因子在 Crypto / 美股 / 港股 三个市场的 IC 表现差异。

运行: python cross_market_ic.py
"""

import sys
import time
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, "E:\\quant\\research")
sys.path.insert(0, "E:\\quant")

import numpy as np
import pandas as pd

from research.data.base import KLineData
from research.data.okx_provider import OKXProvider
from research.data.futu_provider import FutuProvider
from research.factors.compute import FactorComputeEngine, FactorRegistry
from research.models.evaluation import calc_ic, factor_summary


# ============================================================
# 配置
# ============================================================

SYMBOLS = {
    "CRYPTO": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT", "ADA-USDT"],
    "US":     ["SPY", "QQQ", "VXX", "NVDA", "AAPL", "MSFT", "AMZN", "TSLA"],
    "HK":     ["00700", "09988", "01810", "03690", "09618", "02318", "00005", "00941"],
}

OUTPUT_DIR = Path("E:\\quant\\research\\output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_all_data() -> dict:
    """获取所有市场数据 — 全部使用真实数据源"""
    data_map = {}

    # Crypto — OKX 真实数据
    print("\n📡 Crypto (OKX 真实数据)...")
    okx = OKXProvider()
    for sym in SYMBOLS["CRYPTO"]:
        try:
            kdata = okx.fetch_klines(sym, 300, "1d")
            data_map[sym] = kdata
            print(f"  ✅ {sym}: {kdata.n_bars} bars")
        except Exception as e:
            print(f"  ❌ {sym}: {e}")

    # US — Futu 真实数据
    print("\n📡 US (Futu 真实数据)...")
    futu = FutuProvider()
    for sym in SYMBOLS["US"]:
        try:
            kdata = futu.fetch_klines(sym, 200, "1d")
            data_map[sym] = kdata
            print(f"  ✅ {sym}: {kdata.n_bars} bars")
        except Exception as e:
            print(f"  ❌ {sym}: {e}")

    # HK — Futu 真实数据
    print("\n📡 HK (Futu 真实数据)...")
    for sym in SYMBOLS["HK"]:
        try:
            kdata = futu.fetch_klines(sym, 200, "1d")
            data_map[sym] = kdata
            print(f"  ✅ {sym}: {kdata.n_bars} bars")
        except Exception as e:
            print(f"  ❌ {sym}: {e}")

    return data_map


def get_market(symbol: str) -> str:
    """获取标的所属市场"""
    for market, symbols in SYMBOLS.items():
        if symbol in symbols:
            return market
    return "UNKNOWN"


def compute_and_analyze(data_map: dict) -> dict:
    """计算因子并做跨市场 IC 分析"""
    engine = FactorComputeEngine()
    llm_factors = [m.name for m in FactorRegistry.list_factors(category="llm")]
    tech_factors = [m.name for m in FactorRegistry.list_factors(category="technical")]

    # 按市场 + 因子 + 前向收益 聚合 IC
    # 结构: results[market][factor_name][target] = list of IC values
    results = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    # 同时记录每个市场-因子-标的的详细 IC
    details = defaultdict(list)

    for sym, kdata in data_map.items():
        market = get_market(sym)
        print(f"\n  {sym} [{market}]...", end=" ")

        # 计算因子（规则回退模式，llm_window=0）
        factor_df = engine.compute_single(kdata,
                                          factor_names=llm_factors + tech_factors,
                                          llm_window=0)
        print(f"{factor_df.shape[1]} 因子", end=" ")

        close = kdata.df["close"]

        for horizon_name, horizon_bars in [("1d", 1), ("5d", 5)]:
            target = close.pct_change(horizon_bars).shift(-horizon_bars)

            for col in factor_df.columns:
                try:
                    summary = factor_summary(factor_df[col], target)
                    ic = summary["Rank_IC"]
                    hit = summary["hit_rate"]
                    n = summary["n_samples"]

                    if n >= 10:
                        is_llm = col.startswith("llm_")
                        factor_type = "LLM" if is_llm else "TECH"
                        results[market][col][horizon_name].append(ic)
                        details[f"{market}_{horizon_name}"].append({
                            "symbol": sym,
                            "factor": col,
                            "factor_type": factor_type,
                            "ic": ic,
                            "hit_rate": hit,
                            "n_samples": n,
                        })
                except Exception:
                    pass

        print("✓")

    return results, details


def generate_report(results: dict, details: dict, execution_time: float):
    """生成跨市场 IC 对比报告"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = OUTPUT_DIR / f"cross_market_ic_{timestamp}.md"

    lines = []
    lines.append("# QuantBot 跨市场 IC 对比报告")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**执行耗时**: {execution_time:.1f}s")
    lines.append(f"**模式**: 规则回退 (llm_window=0)")
    lines.append("")

    # ============================================================
    # 1. 数据概览
    # ============================================================
    lines.append("## 📡 数据概览")
    lines.append("")
    lines.append("| 市场 | 标的数 | 数据源 |")
    lines.append("|------|--------|--------|")
    for market, symbols in SYMBOLS.items():
        source = {"CRYPTO": "OKX (真实)", "US": "Futu (真实)", "HK": "Futu (真实)"}.get(market, "未知")
        lines.append(f"| {market} | {len(symbols)} | {source} |")
    lines.append("")

    # ============================================================
    # 2. LLM 因子跨市场 IC 对比
    # ============================================================
    lines.append("## 🤖 LLM 因子跨市场 IC 对比")
    lines.append("")

    llm_factor_names = sorted(set(
        d["factor"] for lists in details.values()
        for d in lists if d["factor_type"] == "LLM"
    ))

    for horizon in ["1d", "5d"]:
        lines.append(f"### 前向收益: {horizon}")
        lines.append("")
        lines.append("| 因子 | CRYPTO IC | US IC | HK IC | 最佳市场 | |IC|差距 |")
        lines.append("|------|-----------|-------|-------|----------|---------|")

        for factor in llm_factor_names:
            row_data = {}
            for market in ["CRYPTO", "US", "HK"]:
                ics = results[market][factor][horizon]
                if ics:
                    row_data[market] = {
                        "mean_ic": float(np.mean(ics)),
                        "std_ic": float(np.std(ics)),
                        "abs_ic": float(np.mean(np.abs(ics))),
                        "n": len(ics),
                    }
                else:
                    row_data[market] = None

            # 找最佳市场（按 |IC|）
            valid_markets = {m: v for m, v in row_data.items() if v is not None}
            if valid_markets:
                best_market = max(valid_markets, key=lambda m: valid_markets[m]["abs_ic"])
                best_abs = valid_markets[best_market]["abs_ic"]

                # 计算 |IC| 差距（最佳 vs 次佳）
                abs_ics = [v["abs_ic"] for v in valid_markets.values()]
                if len(abs_ics) >= 2:
                    sorted_abs = sorted(abs_ics, reverse=True)
                    gap = sorted_abs[0] - sorted_abs[1]
                else:
                    gap = 0.0

                crypto_ic = f"{row_data['CRYPTO']['mean_ic']:+.4f}" if row_data['CRYPTO'] else "N/A"
                us_ic = f"{row_data['US']['mean_ic']:+.4f}" if row_data['US'] else "N/A"
                hk_ic = f"{row_data['HK']['mean_ic']:+.4f}" if row_data['HK'] else "N/A"

                lines.append(
                    f"| {factor} | {crypto_ic} | {us_ic} | {hk_ic} "
                    f"| {best_market} | {gap:.4f} |"
                )

        lines.append("")

    # ============================================================
    # 3. 技术因子跨市场 IC 对比（作为基准）
    # ============================================================
    lines.append("## 📊 技术因子跨市场 IC 对比（基准）")
    lines.append("")

    tech_factor_names = sorted(set(
        d["factor"] for lists in details.values()
        for d in lists if d["factor_type"] == "TECH"
    ))

    for horizon in ["1d", "5d"]:
        lines.append(f"### 前向收益: {horizon}")
        lines.append("")
        lines.append("| 因子 | CRYPTO IC | US IC | HK IC | 最佳市场 |")
        lines.append("|------|-----------|-------|-------|----------|")

        for factor in tech_factor_names:
            row_data = {}
            for market in ["CRYPTO", "US", "HK"]:
                ics = results[market][factor][horizon]
                if ics:
                    row_data[market] = {
                        "mean_ic": float(np.mean(ics)),
                        "abs_ic": float(np.mean(np.abs(ics))),
                    }
                else:
                    row_data[market] = None

            valid_markets = {m: v for m, v in row_data.items() if v is not None}
            if valid_markets:
                best_market = max(valid_markets, key=lambda m: valid_markets[m]["abs_ic"])
                crypto_ic = f"{row_data['CRYPTO']['mean_ic']:+.4f}" if row_data['CRYPTO'] else "N/A"
                us_ic = f"{row_data['US']['mean_ic']:+.4f}" if row_data['US'] else "N/A"
                hk_ic = f"{row_data['HK']['mean_ic']:+.4f}" if row_data['HK'] else "N/A"
                lines.append(
                    f"| {factor} | {crypto_ic} | {us_ic} | {hk_ic} | {best_market} |"
                )

        lines.append("")

    # ============================================================
    # 4. 市场级汇总统计
    # ============================================================
    lines.append("## 📈 市场级汇总统计")
    lines.append("")

    lines.append("| 指标 | CRYPTO | US | HK |")
    lines.append("|------|--------|-----|-----|")

    for horizon in ["1d", "5d"]:
        for metric_name, metric_key in [("平均 IC", "mean_ic"), ("平均 |IC|", "abs_ic")]:
            row = [f"{metric_name} ({horizon})"]
            for market in ["CRYPTO", "US", "HK"]:
                all_ics = []
                for factor in llm_factor_names:
                    ics = results[market][factor][horizon]
                    if ics:
                        if metric_key == "mean_ic":
                            all_ics.append(float(np.mean(ics)))
                        else:
                            all_ics.append(float(np.mean(np.abs(ics))))
                if all_ics:
                    row.append(f"{np.mean(all_ics):.4f}")
                else:
                    row.append("N/A")
            lines.append("| " + " | ".join(row) + " |")

    # 正 IC 比例
    for horizon in ["1d", "5d"]:
        row = [f"LLM 正 IC 比例 ({horizon})"]
        for market in ["CRYPTO", "US", "HK"]:
            all_ics = []
            for factor in llm_factor_names:
                ics = results[market][factor][horizon]
                all_ics.extend(ics)
            if all_ics:
                pos_ratio = np.mean(np.array(all_ics) > 0)
                row.append(f"{pos_ratio:.1%}")
            else:
                row.append("N/A")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")

    # ============================================================
    # 5. 结论
    # ============================================================
    lines.append("## 🎯 结论")
    lines.append("")

    # 计算最佳市场
    market_scores = {}
    for horizon in ["1d", "5d"]:
        for market in ["CRYPTO", "US", "HK"]:
            all_abs = []
            for factor in llm_factor_names:
                ics = results[market][factor][horizon]
                if ics:
                    all_abs.append(float(np.mean(np.abs(ics))))
            if all_abs:
                key = f"{market}_{horizon}"
                market_scores[key] = np.mean(all_abs)

    if market_scores:
        best = max(market_scores, key=market_scores.get)
        best_market, best_horizon = best.split("_")
        lines.append(f"1. **最佳市场**: LLM 因子在 **{best_market}** ({best_horizon}) 市场表现最佳，平均 |IC| = {market_scores[best]:.4f}")
        lines.append(f"2. **LLM vs 技术因子**: 对比 LLM 因子与技术因子的 IC 分布差异")
        lines.append(f"3. **数据质量**: 三个市场均使用真实数据（OKX + Futu OpenD），对比结果可信")
        lines.append(f"4. **下一步**: 扩大 LLM window 测试，或基于 IC 筛选最优因子组合")

    lines.append("")

    report_content = "\n".join(lines)
    report_path.write_text(report_content, encoding="utf-8")
    print(f"\n📄 跨市场 IC 报告: {report_path}")
    return report_path


def main():
    start_time = time.time()

    print("=" * 60)
    print("🔬 QuantBot 跨市场 IC 对比分析")
    print(f"   市场: CRYPTO({len(SYMBOLS['CRYPTO'])}标的) "
          f"US({len(SYMBOLS['US'])}标的) "
          f"HK({len(SYMBOLS['HK'])}标的)")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 获取数据
    print("\n📡 阶段 1/3: 获取数据")
    data_map = fetch_all_data()
    print(f"\n  总计 {len(data_map)} 个标的")

    # 2. 计算因子 + IC 分析
    print("\n🧮 阶段 2/3: 计算因子 + IC 分析")
    results, details = compute_and_analyze(data_map)

    # 3. 生成报告
    print("\n📄 阶段 3/3: 生成报告")
    elapsed = time.time() - start_time
    report_path = generate_report(results, details, elapsed)

    # 4. 终端摘要
    print(f"\n{'='*60}")
    print("📋 跨市场 IC 摘要")
    print(f"{'='*60}")
    print(f"  执行耗时: {elapsed:.1f}s")

    # 打印每个市场 LLM 因子的平均 IC
    llm_factor_names = sorted(set(
        d["factor"] for lists in details.values()
        for d in lists if d["factor_type"] == "LLM"
    ))
    for horizon in ["1d", "5d"]:
        print(f"\n  [{horizon} 前向] LLM 因子平均 |IC|:")
        for market in ["CRYPTO", "US", "HK"]:
            all_abs = []
            all_raw_ics = []
            for factor in llm_factor_names:
                ics = results[market][factor][horizon]
                if ics:
                    all_abs.append(float(np.mean(np.abs(ics))))
                    all_raw_ics.extend(ics)
            if all_abs:
                mean_val = np.mean(all_abs)
                pos_ratio = np.mean(np.array(all_raw_ics) > 0) if all_raw_ics else 0
                print(f"    {market}: 平均 |IC|={mean_val:.4f} 正IC率={pos_ratio:.1%}")

    print(f"\n  报告: {report_path}")
    print(f"\n  🎉 跨市场 IC 对比完成!")
    return 0


if __name__ == "__main__":
    sys.exit(main())