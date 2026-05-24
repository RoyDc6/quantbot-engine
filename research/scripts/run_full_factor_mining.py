#!/usr/bin/env python3
"""
research/scripts/run_full_factor_mining.py — 完整因子挖掘管线

流程:
1. 获取数据（OKX 加密货币真实数据 + 模拟美股/港股数据）
2. 计算全部 20 个因子（14 技术 + 6 LLM）
3. IC 分析（IC 均值、ICIR、IC 标准差）
4. 生成因子挖掘报告

运行: python run_full_factor_mining.py
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
from research.data.symbol_registry import SymbolRegistry
from research.data.okx_provider import OKXProvider
from research.factors.compute import FactorComputeEngine, FactorRegistry
from research.models.evaluation import calc_ic, factor_summary


# ============================================================
# 配置
# ============================================================

CRYPTO_SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT", "ADA-USDT"]
HK_SYMBOLS = ["00700", "09988", "01810", "03690"]
US_SYMBOLS = ["SPY", "QQQ", "NVDA", "AAPL"]

OUTPUT_DIR = Path("E:\\quant\\research\\output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 模拟数据生成（用于无实时数据源的市场）
# ============================================================

def generate_mock_klines(symbol: str, market: str, n_bars: int = 200,
                         base_price: float = 100.0, seed: int = 42) -> KLineData:
    """生成模拟 K 线数据用于测试"""
    rng = np.random.RandomState(seed + hash(symbol) % 10000)

    # 生成随机游走价格
    returns = rng.randn(n_bars) * 0.015  # 1.5% 日波动
    returns[0] = 0
    price = base_price * np.exp(np.cumsum(returns))

    # 生成 OHLCV
    close = price
    daily_range = close * 0.02  # 2% 日内振幅
    high = close + daily_range * rng.rand(n_bars)
    low = close - daily_range * rng.rand(n_bars)
    open_price = close - returns * close * 0.5

    # 成交量（有趋势特征）
    vol_base = base_price * 100000
    volume = vol_base * (1 + 0.5 * rng.randn(n_bars))
    volume = np.maximum(volume, vol_base * 0.1)

    # 日期索引
    dates = pd.date_range(
        end=pd.Timestamp.now(tz="UTC"),
        periods=n_bars,
        freq="D"
    )

    df = pd.DataFrame({
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=dates)

    return KLineData(symbol=symbol, market=market, timeframe="1d", df=df, provider="mock")


# ============================================================
# 因子挖掘管线
# ============================================================

def fetch_data() -> dict:
    """获取所有标的数据"""
    data_map = {}

    # 1. 加密货币 — 真实数据
    print("\n📡 获取加密货币数据 (OKX)...")
    okx = OKXProvider()
    for sym in CRYPTO_SYMBOLS:
        try:
            kdata = okx.fetch_klines(sym, 300, "1d")
            data_map[sym] = kdata
            print(f"  ✅ {sym}: {kdata.n_bars} bars")
        except Exception as e:
            print(f"  ❌ {sym}: {e}")

    # 2. 美股 — 模拟数据
    print("\n📡 生成美股模拟数据...")
    for sym in US_SYMBOLS:
        base = {"SPY": 500, "QQQ": 450, "NVDA": 120, "AAPL": 200}.get(sym, 100)
        kdata = generate_mock_klines(sym, "US", 200, base)
        data_map[sym] = kdata
        print(f"  ✅ {sym}: {kdata.n_bars} bars (mock)")

    # 3. 港股 — 模拟数据
    print("\n📡 生成港股模拟数据...")
    for sym in HK_SYMBOLS:
        base = {"00700": 500, "09988": 100, "01810": 30, "03690": 120}.get(sym, 50)
        kdata = generate_mock_klines(sym, "HK", 200, base)
        data_map[sym] = kdata
        print(f"  ✅ {sym}: {kdata.n_bars} bars (mock)")

    return data_map


def compute_all_factors(data_map: dict) -> dict:
    """计算所有标的的全部因子"""
    engine = FactorComputeEngine()

    # 全部因子
    all_factors = [m.name for m in FactorRegistry.list_factors()]
    tech_factors = [m.name for m in FactorRegistry.list_factors(category="technical")]
    llm_factors = [m.name for m in FactorRegistry.list_factors(category="llm")]

    print(f"\n🧮 因子配置:")
    print(f"  全部: {len(all_factors)} 个")
    print(f"  技术因子: {len(tech_factors)} 个")
    print(f"  LLM 因子: {len(llm_factors)} 个")

    results = {}
    for sym, kdata in data_map.items():
        print(f"\n  计算 {sym}...", end=" ")

        # 技术因子（全量）
        tech_df = engine.compute_single(kdata, factor_names=tech_factors)
        print(f"技术 {tech_df.shape[1]} 个,", end=" ")

        # LLM 因子（规则回退模式，不调用 LLM API）
        # 注：llm_window=0 强制使用规则近似，速度快
        # 如需 LLM 增强，改为 llm_window=10
        llm_df = engine.compute_single(kdata, factor_names=llm_factors, llm_window=0)
        print(f"LLM {llm_df.shape[1]} 个", end=" ")

        # 合并
        combined = pd.concat([tech_df, llm_df], axis=1)
        results[sym] = combined
        print(f"→ 总计 {combined.shape[1]} 个因子")

    return results


def run_ic_analysis(factor_results: dict, data_map: dict) -> dict:
    """运行 IC 分析"""
    print("\n" + "=" * 60)
    print("📊 IC 分析")
    print("=" * 60)

    all_ic_results = {}

    for sym in factor_results:
        factor_df = factor_results[sym]
        kdata = data_map[sym]

        # 计算未来收益（作为 IC 分析的目标变量）
        close = kdata.df["close"]
        forward_ret_1d = close.pct_change().shift(-1)  # 未来 1 日收益
        forward_ret_5d = close.pct_change(5).shift(-5)  # 未来 5 日收益

        for target_name, target in [("ret_1d_fwd", forward_ret_1d),
                                     ("ret_5d_fwd", forward_ret_5d)]:
            if sym not in all_ic_results:
                all_ic_results[sym] = {}

            ic_dict = {}
            for col in factor_df.columns:
                # 使用 factor_summary 做完整评估
                try:
                    summary = factor_summary(factor_df[col], target)
                    if summary["n_samples"] >= 10:
                        # 额外计算 IC 标准差（通过滚动窗口）
                        from research.models.evaluation import calc_ic
                        ic_vals = []
                        for i in range(20, len(factor_df), 5):
                            chunk_f = factor_df[col].iloc[max(0,i-20):i]
                            chunk_r = target.iloc[max(0,i-20):i]
                            if len(chunk_f.dropna()) >= 10:
                                ic_vals.append(calc_ic(chunk_f, chunk_r))

                        ic_dict[col] = {
                            "ic_mean": summary["Rank_IC"],
                            "icir": summary["Rank_IC"] / (np.std(ic_vals) if np.std(ic_vals) > 0 else 1),
                            "ic_std": float(np.std(ic_vals)) if ic_vals else 0,
                            "hit_rate": summary["hit_rate"],
                            "ic_series": ic_vals,
                            "long_short_spread": summary["long_short_spread"],
                            "n_samples": summary["n_samples"],
                        }
                except Exception as e:
                    print(f"    ⚠️  {sym} {col}: {e}")

            all_ic_results[sym][target_name] = ic_dict

            # 打印 LLM 因子的 IC 结果
            llm_cols = [c for c in factor_df.columns if c.startswith("llm_")]
            if llm_cols:
                for col in llm_cols:
                    if col in ic_dict:
                        ic_info = ic_dict[col]
                        print(f"  {sym} | {col} | {target_name}")
                        print(f"    Rank IC: {ic_info['ic_mean']:.4f}")
                        print(f"    ICIR:    {ic_info['icir']:.4f}")
                        print(f"    IC 标准差: {ic_info['ic_std']:.4f}")
                        print(f"    命中率:  {ic_info['hit_rate']:.1%}")

    return all_ic_results


def generate_report(data_map: dict, factor_results: dict,
                    ic_results: dict, execution_time: float):
    """生成因子挖掘报告"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = OUTPUT_DIR / f"factor_mining_report_{timestamp}.md"

    lines = []
    lines.append(f"# QuantBot 因子挖掘报告")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**执行耗时**: {execution_time:.1f}s")
    lines.append(f"**因子总数**: {FactorRegistry.count()} (14 技术 + 6 LLM)")
    lines.append("")

    # 数据概览
    lines.append("## 📡 数据概览")
    lines.append("")
    lines.append("| 标的 | 市场 | K线数 | 数据源 |")
    lines.append("|------|------|-------|--------|")
    for sym, kdata in data_map.items():
        lines.append(f"| {sym} | {kdata.market} | {kdata.n_bars} | {kdata.provider} |")
    lines.append("")

    # 因子概览
    lines.append("## 🧩 因子概览")
    lines.append("")
    lines.append("### 技术因子 (14)")
    lines.append("")
    tech_factors = [m for m in FactorRegistry.list_factors(category="technical")]
    for m in tech_factors:
        lines.append(f"- **{m.name}**: {m.description}")
    lines.append("")

    lines.append("### LLM 因子 (6)")
    lines.append("")
    llm_factors = [m for m in FactorRegistry.list_factors(category="llm")]
    for m in llm_factors:
        lines.append(f"- **{m.name}**: {m.description}")
    lines.append("")

    # IC 分析结果
    lines.append("## 📊 IC 分析结果")
    lines.append("")
    if ic_results:
        lines.append("| 标的 | 因子 | 目标 | IC 均值 | ICIR | IC 标准差 | IC 正比率 |")
        lines.append("|------|------|------|---------|------|----------|----------|")
        for sym, targets in ic_results.items():
            for target_name, ic_dict in targets.items():
                for factor_name, factor_info in ic_dict.items():
                    if factor_name.startswith("llm_"):
                        ic_mean = factor_info.get("ic_mean", "N/A")
                        icir = factor_info.get("icir", "N/A")
                        ic_std = factor_info.get("ic_std", "N/A")
                        ic_series = factor_info.get("ic_series", [])
                        pos_ratio = f"{(np.array(ic_series) > 0).mean():.1%}" if ic_series else "N/A"
                        lines.append(
                            f"| {sym} | {factor_name} | {target_name} "
                            f"| {ic_mean if isinstance(ic_mean, str) else f'{ic_mean:.4f}'} "
                            f"| {icir if isinstance(icir, str) else f'{icir:.4f}'} "
                            f"| {ic_std if isinstance(ic_std, str) else f'{ic_std:.4f}'} "
                            f"| {pos_ratio} |"
                        )

        # 按 IC 均值排序的 Top LLM 因子
        lines.append("### Top LLM 因子 (按 IC 均值)")
        lines.append("")
        top_factors = []
        for sym, targets in ic_results.items():
            for target_name, ic_dict in targets.items():
                for factor_name, factor_info in ic_dict.items():
                    if factor_name.startswith("llm_"):
                        ic_mean = factor_info.get("ic_mean")
                        if ic_mean is not None and not isinstance(ic_mean, str):
                            top_factors.append((abs(ic_mean), sym, factor_name, target_name, ic_mean))
        top_factors.sort(reverse=True)
        for abs_ic, sym, fname, tname, ic_mean in top_factors[:10]:
            lines.append(f"- **{fname}** on {sym} ({tname}): IC = {ic_mean:.4f}")
        lines.append("")
    else:
        lines.append("IC 分析未产生有效结果。")
        lines.append("")

    # 结论
    lines.append("## 📋 结论与下一步")
    lines.append("")
    lines.append("### 当前阶段")
    lines.append("- ✅ 因子注册系统: 20 个因子 (14 技术 + 6 LLM)")
    lines.append("- ✅ 数据层: OKX 加密货币 (真实) + 模拟美股/港股")
    lines.append("- ✅ LLM 因子架构: 规则回退 + LLM 增强 (NVIDIA NIM)")
    lines.append("")

    lines.append("### 下一步计划")
    lines.append("1. **启用 LLM 增强**: 设置 llm_window>0 让 LLM 覆盖最近 K 线")
    lines.append("2. **跨市场 IC 对比**: 对比 LLM 因子在不同市场的 IC")
    lines.append("3. **因子组合**: 基于 IC 筛选最优因子组合")
    lines.append("4. **实盘数据接入**: 连接 Futu OpenD 获取美股/港股真实数据")
    lines.append("")

    report_content = "\n".join(lines)
    report_path.write_text(report_content, encoding="utf-8")
    print(f"\n📄 报告已保存: {report_path}")
    return report_path


# ============================================================
# 主流程
# ============================================================

def main():
    start_time = time.time()

    print("=" * 60)
    print("🔬 QuantBot 因子挖掘管线")
    print(f"   因子总数: {FactorRegistry.count()} (14 技术 + 6 LLM)")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 获取数据
    print("\n" + "-" * 40)
    print("阶段 1/3: 获取数据")
    print("-" * 40)
    data_map = fetch_data()
    print(f"\n  总计 {len(data_map)} 个标的")

    # 2. 计算因子
    print("\n" + "-" * 40)
    print("阶段 2/3: 计算因子")
    print("-" * 40)
    factor_results = compute_all_factors(data_map)

    # 3. IC 分析
    print("\n" + "-" * 40)
    print("阶段 3/3: IC 分析")
    print("-" * 40)
    ic_results = run_ic_analysis(factor_results, data_map)

    # 4. 生成报告
    elapsed = time.time() - start_time
    report_path = generate_report(data_map, factor_results, ic_results, elapsed)

    # 5. 摘要输出
    print("\n" + "=" * 60)
    print("📋 因子挖掘摘要")
    print("=" * 60)
    print(f"  标的数量: {len(data_map)}")
    print(f"  因子数量: {FactorRegistry.count()}")
    print(f"  执行耗时: {elapsed:.1f}s")

    # LLM 因子 IC 摘要
    if ic_results:
        all_llm_ics = []
        for sym, targets in ic_results.items():
            for target_name, ic_dict in targets.items():
                for factor_name, factor_info in ic_dict.items():
                    if factor_name.startswith("llm_"):
                        ic_mean = factor_info.get("ic_mean")
                        if ic_mean is not None and not isinstance(ic_mean, str):
                            all_llm_ics.append({
                                "symbol": sym,
                                "factor": factor_name,
                                "target": target_name,
                                "ic_mean": ic_mean,
                                "icir": factor_info.get("icir", 0),
                            })

        if all_llm_ics:
            ic_df = pd.DataFrame(all_llm_ics)
            print(f"\n  LLM 因子 IC 统计:")
            print(f"    正 IC 比例: {(ic_df['ic_mean'] > 0).mean():.1%}")
            print(f"    平均 |IC|: {ic_df['ic_mean'].abs().mean():.4f}")
            print(f"    最高 |IC|: {ic_df['ic_mean'].abs().max():.4f}")
            print(f"\n  Top 5 LLM 因子 (按 |IC|):")
            top5 = ic_df.reindex(ic_df['ic_mean'].abs().sort_values(ascending=False).index).head(5)
            for _, row in top5.iterrows():
                print(f"    {row['factor']} @ {row['symbol']} ({row['target']}): IC={row['ic_mean']:.4f}")

    print(f"\n  报告: {report_path}")
    print(f"\n  🎉 因子挖掘完成!")
    return 0


if __name__ == "__main__":
    sys.exit(main())