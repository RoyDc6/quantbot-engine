#!/usr/bin/env python3
"""
research/scripts/run_cross_market.py — 跨市场因子研究脚本

对 US / HK / CN / CRYPTO 四个市场分别运行完整的因子研究流水线：
  数据获取 → 因子计算 → IC 分析 → 回测验证 → 跨市场对比

两种模式:
  --mode simulate   (默认) 使用模拟 K 线数据进行因子验证
  --mode live        (需要 Futu OpenD / OKX API) 使用真实数据

用法:
  python -m research.scripts.run_cross_market
  python -m research.scripts.run_cross_market --mode live --markets US,HK
  python -m research.scripts.run_cross_market --mode simulate --output ./output
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Windows GBK 编码兼容 ─────────────────────────────
if sys.stdout.encoding and sys.stdout.encoding.upper() in ("GBK", "CP936"):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np

# ── 路径注入 ──────────────────────────────────────────────
RESEARCH_ROOT = Path(__file__).resolve().parent.parent
QUANT_ROOT = RESEARCH_ROOT.parent
for p in [str(RESEARCH_ROOT), str(QUANT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ── 导入研究模块 ──────────────────────────────────────────
from research.data.base import KLineData
from research.data.symbol_registry import SymbolRegistry
from research.factors.compute import FactorComputeEngine, FactorRegistry
from research.factors.registry import FactorMeta
from research.models.evaluation import calc_ic, factor_summary
from research.analysis.ic_analysis import ICAnalyzer
from research.backtest.engine import BacktestEngine
from research.backtest.metrics import compute_backtest_metrics


# ═══════════════════════════════════════════════════════════
# 模拟数据生成
# ═══════════════════════════════════════════════════════════

def _generate_simulated_klines(n_bars: int = 500,
                               base_price: float = 100.0,
                               drift: float = 0.0002,
                               volatility: float = 0.015,
                               seed: int = 42) -> pd.DataFrame:
    """生成模拟 K 线数据用于因子研究"""
    rng = np.random.RandomState(seed)
    dates = pd.date_range(end=pd.Timestamp.today(),
                          periods=n_bars, freq="D")

    # 几何布朗运动模拟价格
    log_ret = rng.normal(drift - volatility**2/2, volatility, n_bars)
    price = base_price * np.exp(np.cumsum(log_ret))

    # 生成 OHLCV
    daily_vol = price * volatility
    opens = price * (1 + rng.uniform(-0.005, 0.005, n_bars))
    highs = np.maximum(opens, price) + abs(rng.normal(0, daily_vol * 0.5))
    lows = np.minimum(opens, price) - abs(rng.normal(0, daily_vol * 0.5))
    closes = price
    volumes = rng.randint(500_000, 5_000_000, n_bars)

    return pd.DataFrame({
        "date": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


def _build_simulated_data(registry: SymbolRegistry) -> Dict[str, KLineData]:
    """为注册表中所有标的生成模拟 K 线"""
    market_configs = {
        "US": {"base": 450, "vol": 0.012, "drift": 0.0003},
        "HK": {"base": 300, "vol": 0.015, "drift": 0.0002},
        "CN": {"base": 50,  "vol": 0.018, "drift": 0.0001},
        "CRYPTO": {"base": 50000, "vol": 0.035, "drift": 0.0005},
    }

    data_map = {}
    for market in ["US", "HK", "CN", "CRYPTO"]:
        cfg = market_configs.get(market, {"base": 100, "vol": 0.02, "drift": 0.0002})
        symbols = registry.get_symbols(market)
        for i, sym in enumerate(symbols):
            df = _generate_simulated_klines(
                base_price=cfg["base"] * (1 + i * 0.1),
                volatility=cfg["vol"],
                drift=cfg["drift"],
                seed=42 + i,
            )
            data_map[sym.symbol] = KLineData(
                symbol=sym.symbol,
                market=market,
                timeframe="1d",
                df=df,
                provider="simulate",
            )
    return data_map


# ═══════════════════════════════════════════════════════════
# 市场因子研究
# ═══════════════════════════════════════════════════════════

def _compute_forward_returns(kdata: KLineData,
                             horizons: List[int] = [1, 5, 10, 20]) -> Dict[int, pd.Series]:
    """计算多持有期未来收益率"""
    close = kdata.df["close"]
    results = {}
    for h in horizons:
        fwd = close.pct_change(h).shift(-h)
        results[h] = fwd.rename(f"fwd_{h}d")
    return results


class MarketStudyResult:
    """单个市场的研究结果容器"""
    def __init__(self, market: str, n_symbols: int):
        self.market = market
        self.n_symbols = n_symbols
        self.factor_values: Dict[str, pd.DataFrame] = {}      # {symbol: factor_df}
        self.factor_ic: Dict[str, Dict] = {}                   # {factor_name: ic_metrics}
        self.ic_ranking: List[Tuple[str, float]] = []          # [(factor, rank_ic), ...]
        self.backtest_result = None
        self.top_factors: List[str] = []
        self.compute_time: float = 0.0

    def summary_dict(self) -> Dict:
        return {
            "market": self.market,
            "n_symbols": self.n_symbols,
            "n_factors_computed": len(self.factor_values.get(list(self.factor_values.keys())[0], pd.DataFrame()).columns)
                if self.factor_values else 0,
            "top_factor": self.ic_ranking[0] if self.ic_ranking else ("N/A", 0.0),
            "compute_time_s": round(self.compute_time, 2),
        }


def run_market_study(market: str,
                     data_map: Dict[str, KLineData],
                     registry: SymbolRegistry,
                     engine: FactorComputeEngine,
                     top_n: int = 5) -> MarketStudyResult:
    """对单个市场运行完整因子研究

    步骤:
      1. 获取该市场所有标的的 K 线
      2. 批量计算因子
      3. 计算未来收益率
      4. 逐因子 IC 分析
      5. 回测验证
    """
    t0 = time.time()
    symbols = registry.get_symbols(market)
    result = MarketStudyResult(market, len(symbols))

    if not symbols:
        print(f"  ⚠️  市场 {market} 无标的，跳过")
        return result

    print(f"\n{'='*60}")
    print(f"📊 市场: {market} ({len(symbols)} 个标的)")
    print(f"{'='*60}")

    # ── 1. 收集该市场所有标的的因子值 ──
    all_factors: Dict[str, pd.DataFrame] = {}
    factor_returns_map: Dict[str, pd.Series] = {}

    for sym in symbols:
        kdata = data_map.get(sym.symbol)
        if kdata is None:
            continue

        # 计算因子
        factor_df = engine.compute_single(kdata, market=market)
        if factor_df.empty:
            continue
        all_factors[sym.symbol] = factor_df

        # 计算未来 5 日收益率（用于 IC 分析）
        fwd_rets = _compute_forward_returns(kdata, horizons=[5])
        fwd_5d = fwd_rets[5]
        # 与因子 DataFrame 对齐
        aligned = factor_df.join(fwd_5d, how="inner")
        factor_returns_map[sym.symbol] = aligned["fwd_5d"]

    result.factor_values = all_factors
    n_computed = len(all_factors)
    print(f"  成功计算因子: {n_computed}/{len(symbols)} 个标的")

    if not all_factors:
        return result

    # ── 2. 逐因子 IC 分析（截面 IC） ──
    # 将所有标的的因子值堆叠成一个长表
    factor_names = FactorRegistry.list_factors(market=market)
    ic_results = {}

    for fmeta in factor_names:
        fname = fmeta.name
        f_vals = []
        f_rets = []

        for sym in all_factors:
            if fname not in all_factors[sym].columns:
                continue
            # 取最后一个同时有因子值和未来收益率的对齐行
            aligned = all_factors[sym][[fname]].join(
                factor_returns_map[sym], how="inner"
            ).dropna()
            if len(aligned) < 1:
                continue
            fv = aligned[fname].iloc[-1]
            fr = aligned["fwd_5d"].iloc[-1]
            f_vals.append(fv)
            f_rets.append(fr)

        # 自适应最低样本数：至少需要 min(10, n_symbols)
        min_samples = min(10, max(5, len(all_factors)))
        if len(f_vals) >= min_samples:
            ic = calc_ic(
                pd.Series(f_vals, name=fname),
                pd.Series(f_rets, name="ret"),
                method="spearman",
            )
            ic_results[fname] = round(ic, 4)

    # 排序
    sorted_ic = sorted(ic_results.items(), key=lambda x: abs(x[1]), reverse=True)
    result.factor_ic = ic_results
    result.ic_ranking = sorted_ic

    print(f"  IC 分析完成，{len(ic_results)} 个因子有有效 IC")
    for name, ic_val in sorted_ic[:top_n]:
        marker = "✅" if abs(ic_val) > 0.05 else "➖"
        print(f"    {marker} {name}: IC={ic_val:+.4f}")

    # ── 3. 回测验证（使用综合信号） ──
    print(f"  运行回测验证...")

    # 构建信号: 使用 IC 最高的前 N 个因子的等权组合
    top_factor_names = [name for name, _ in sorted_ic[:min(top_n, len(sorted_ic))]]
    result.top_factors = top_factor_names

    if top_factor_names and n_computed >= 2 and len(sorted_ic) >= 1:
        # 构建信号 DataFrame (daily × symbols)
        signal_dfs = {}
        for sym in all_factors:
            df = all_factors[sym]
            # 等权组合信号
            available = [c for c in top_factor_names if c in df.columns]
            if available:
                # 标准化后等权
                z = (df[available] - df[available].mean()) / df[available].std().replace(0, np.nan)
                signal_dfs[sym] = z.mean(axis=1).clip(-1, 1)

        if signal_dfs:
            # 用 date 列作为索引，确保与价格数据对齐
            def _attach_date_index(df_orig, sym):
                """将 DataFrame 的 RangeIndex 替换为 date 列"""
                dates = data_map[sym].df["date"]
                df_out = df_orig.copy()
                df_out.index = pd.DatetimeIndex(dates.iloc[:len(df_out)])
                return df_out

            signal_dfs_dated = {
                sym: _attach_date_index(df, sym)
                for sym, df in signal_dfs.items()
            }
            signal_df = pd.DataFrame(signal_dfs_dated).dropna(how="all")

            # 价格 DataFrame（按日期对齐）
            price_df = pd.DataFrame({
                sym: data_map[sym].df.set_index("date")["close"]
                for sym in signal_df.columns if sym in data_map
            })
            # 对齐到信号日期
            common_dates = signal_df.index.intersection(price_df.index)
            signal_df = signal_df.loc[common_dates]
            price_df = price_df.loc[common_dates]

            # 最少需要 10 个交易日
            if len(signal_df) < 10 or len(price_df) < 10:
                print(f"    数据不足: signal={len(signal_df)}, price={len(price_df)}, 跳过回测")
            else:
                # 运行回测
                bt = BacktestEngine(
                    initial_capital=1_000_000,
                    commission_pct=0.0003,
                    slippage_pct=0.0001,
                )
                bt_result = bt.run(signal_df, price_df)
                metrics = compute_backtest_metrics(bt_result)
                result.backtest_result = metrics

                print(f"    回测结果:")
                print(f"      总收益: {metrics['total_return_pct']:+.2f}%")
                print(f"      Sharpe: {metrics['sharpe_ratio']:.3f}")
                print(f"      最大回撤: {metrics['max_drawdown_pct']:.2f}%")
                print(f"      命中率: {metrics['hit_rate']:.2%}")

    result.compute_time = time.time() - t0
    return result


# ═══════════════════════════════════════════════════════════
# 跨市场对比报告
# ═══════════════════════════════════════════════════════════

def generate_comparison_report(results: List[MarketStudyResult]) -> str:
    """生成跨市场对比报告 Markdown"""

    lines = []
    lines.append("# 🔬 跨市场因子研究对比报告")
    lines.append(f"\n> 生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("> 模式: 模拟数据（simulated）")
    lines.append("")

    # ── 1. 汇总表 ──
    lines.append("## 📋 市场汇总")
    lines.append("")
    lines.append("| 市场 | 标的数 | 有效因子 | Top因子 | IC | Sharpe | 总收益 |")
    lines.append("|------|--------|----------|---------|----|--------|--------|")

    for r in results:
        top_name, top_ic = r.ic_ranking[0] if r.ic_ranking else ("-", 0.0)
        sharpe = r.backtest_result["sharpe_ratio"] if r.backtest_result else 0.0
        ret = r.backtest_result["total_return_pct"] if r.backtest_result else 0.0
        n_factors = r.factor_ic.get("n_factors_computed", 0) if hasattr(r, 'factor_ic') else len(r.factor_ic)
        lines.append(
            f"| {r.market} | {r.n_symbols} | {len(r.factor_ic)} | "
            f"{top_name} | {top_ic:+.4f} | {sharpe:.3f} | {ret:+.2f}% |"
        )

    lines.append("")

    # ── 2. 各市场 Top 因子 ──
    lines.append("## 🏆 各市场最优因子")
    lines.append("")
    for r in results:
        lines.append(f"### {r.market} (Top 5)")
        lines.append("")
        lines.append("| 排名 | 因子名 | IC |")
        lines.append("|------|--------|-----|")
        for rank, (name, ic_val) in enumerate(r.ic_ranking[:5], 1):
            lines.append(f"| {rank} | {name} | {ic_val:+.4f} |")
        lines.append("")

    # ── 3. 因子跨市场一致性 ──
    lines.append("## 🔄 因子跨市场一致性")
    lines.append("")
    lines.append("统计每个因子在各市场的 IC 符号一致性。")
    lines.append("")

    # 收集所有因子名
    all_factor_names = set()
    for r in results:
        all_factor_names.update(r.factor_ic.keys())

    lines.append("| 因子 | " + " | ".join(r.market for r in results) + " | 一致数 |")
    lines.append("|------|" + "|".join("---" for _ in results) + "|--------|")

    for fname in sorted(all_factor_names):
        ics = []
        consistent_count = 0
        signs = []
        for r in results:
            ic_val = r.factor_ic.get(fname, 0.0)
            ics.append(f"{ic_val:+.4f}")
            if ic_val != 0.0:
                signs.append(np.sign(ic_val))
        if signs:
            consistent_count = max(signs.count(1), signs.count(-1))
        lines.append(f"| {fname} | " + " | ".join(ics) + f" | {consistent_count}/{len(results)} |")

    lines.append("")
    lines.append("---")
    lines.append(f"\n_报告由 QuantBot Research Framework 自动生成_")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def run_study(markets: List[str],
              mode: str = "simulate",
              top_n: int = 5,
              output_dir: Optional[str] = None) -> List[MarketStudyResult]:
    """主研究流程"""
    registry = SymbolRegistry()
    engine = FactorComputeEngine()

    print(f"🔬 QuantBot 跨市场因子研究")
    print(f"   模式: {mode}")
    print(f"   市场: {', '.join(markets)}")
    print(f"   已注册因子: {FactorRegistry.count()}")

    # ── 获取数据 ──
    if mode == "simulate":
        print("\n📥 生成模拟数据...")
        data_map = _build_simulated_data(registry)
    else:
        print("\n📥 从 API 获取真实数据...")
        data_map = {}
        from research.data.futu_provider import FutuProvider
        from research.data.okx_provider import OKXProvider

        futu = FutuProvider()
        okx = OKXProvider()

        for market in markets:
            symbols = registry.get_symbols(market)
            for sym in symbols:
                try:
                    if market == "CRYPTO":
                        kdata = okx.fetch_klines(sym.symbol, count=500)
                    else:
                        kdata = futu.fetch_klines(sym.symbol, count=500)
                    data_map[sym.symbol] = kdata
                    print(f"  ✅ {sym.symbol}")
                except Exception as e:
                    print(f"  ❌ {sym.symbol}: {e}")

    print(f"\n   共获取 {len(data_map)} 个标的 K 线")

    # ── 逐市场研究 ──
    results: List[MarketStudyResult] = []
    for market in markets:
        r = run_market_study(market, data_map, registry, engine, top_n)
        results.append(r)

    # ── 生成对比报告 ──
    report = generate_comparison_report(results)

    # 保存报告
    out_dir = Path(output_dir) if output_dir else (RESEARCH_ROOT / "output")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"cross_market_report_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n📄 对比报告已保存: {report_path}")

    # 打印摘要
    print(f"\n{'='*60}")
    print("📋 研究完成摘要")
    print(f"{'='*60}")
    for r in results:
        top = r.ic_ranking[0] if r.ic_ranking else ("-", 0.0)
        sharpe = r.backtest_result["sharpe_ratio"] if r.backtest_result else 0.0
        print(f"  {r.market}: Top因子={top[0]} (IC={top[1]:+.4f}), Sharpe={sharpe:.3f}, {r.compute_time:.1f}s")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="QuantBot 跨市场因子研究"
    )
    parser.add_argument("--mode", choices=["simulate", "live"],
                        default="simulate",
                        help="研究模式: simulate(模拟) / live(实时)")
    parser.add_argument("--markets", type=str, default="US,HK,CN,CRYPTO",
                        help="市场列表，逗号分隔")
    parser.add_argument("--top-n", type=int, default=5,
                        help="每个市场 Top N 因子")
    parser.add_argument("--output", type=str, default=None,
                        help="输出目录")
    args = parser.parse_args()

    markets = [m.strip().upper() for m in args.markets.split(",")]
    run_study(markets, args.mode, args.top_n, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())