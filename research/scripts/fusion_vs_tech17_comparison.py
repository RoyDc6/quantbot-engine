"""
scripts/fusion_vs_tech17_comparison.py — FusionEngine vs Tech17 直接对比

同一套数据 (Futu SPY 200 bars)、同一个固定回测引擎（已修复 look-ahead）。
"""

from __future__ import annotations

import sys, time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, "E:\\quant")

import numpy as np
import pandas as pd

# ── Tech17 ──
from research.factors.compute import FactorComputeEngine
from research.backtest.engine import BacktestEngine
from research.backtest.metrics import compute_backtest_metrics

# ── FusionEngine ──
sys.path.insert(0, "E:\\quant\\fusion_framework")
sys.path.insert(0, "E:\\quant")
from fusion_framework.fusion_engine import FusionEngine
from fusion_framework.signal_types import SignalLevel, FusionModelSignal, XMMSignal

# ════════════════════════════════════════════════════════════════
# FusionEngine 内部指标
# ════════════════════════════════════════════════════════════════

def wilder_rsi(close, n=14):
    d = np.diff(close)
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    r = np.full(len(close), 50.0)
    if len(close) <= n:
        return r
    ag, al = np.mean(g[:n]), np.mean(l[:n])
    r[n] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    for i in range(n + 1, len(close)):
        ag = (ag * (n - 1) + g[i - 1]) / n
        al = (al * (n - 1) + l[i - 1]) / n
        r[i] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    return r

def sma(close, n):
    s = np.full(len(close), np.nan)
    for i in range(n - 1, len(close)):
        s[i] = np.mean(close[i - n + 1 : i + 1])
    return s

def macd_hist(close):
    s = pd.Series(close)
    ef = s.ewm(span=12, adjust=False).mean().values
    es = s.ewm(span=26, adjust=False).mean().values
    macd = ef - es
    sig = pd.Series(macd).ewm(span=9, adjust=False).mean().values
    return macd - sig


# ════════════════════════════════════════════════════════════════
# FusionEngine 信号生成（生产级）
# ════════════════════════════════════════════════════════════════

def gen_fusion_signals(close: np.ndarray, dates: list[str], symbol: str = "SPY"):
    """
    从价格序列生成 FM + TA 信号。

    FM 信号始终 HOLD（匹配生产环境缠论分形逻辑）。详见 full_backtest_v2.py 说明。
    TA 信号为均线多头 + MACD + RSI 组合。
    """
    n = len(close)

    # 计算指标（仅 TA 需要）
    rsi_d = wilder_rsi(close, 14)
    sma50 = sma(close, 50)
    sma20 = sma(close, 20)
    macd_h = macd_hist(close)

    fm_signals: list[FusionModelSignal] = []
    xmm_signals: list[XMMSignal] = []

    for i in range(60, n):
        date = dates[i]
        c = close[i]
        d = rsi_d[i]
        mh = macd_h[i]

        # ── FM 信号（始终 HOLD，仅保留 trend_up 供融合矩阵使用） ──
        trend_up = not np.isnan(sma50[i]) and c > sma50[i]
        fm_signals.append(
            FusionModelSignal(
                symbol, date, 0.0, 50.0, 50.0, 0.0, "HOLD", 0.5,
                {"trend_up": trend_up, "sma50_above": trend_up},
            )
        )

        # ── XMM 信号（均线多头 + MACD + RSI） ──
        above20 = not np.isnan(sma20[i]) and c > sma20[i]
        above50 = not np.isnan(sma50[i]) and c > sma50[i]
        macd_pos = mh > 0

        if above20 and above50 and macd_pos and d < 70:
            action, xmm_conf = "BUY", 0.75
        elif d >= 78 or (not above20 and d > 65):
            action, xmm_conf = "SELL", 0.72
        else:
            action, xmm_conf = "HOLD", 0.5

        xmm_signals.append(
            XMMSignal(
                symbol, date, action, float(xmm_conf),
                float(d),
                float(c / sma20[i] if not np.isnan(sma20[i]) else 1.0),
                float(mh),
            )
        )

    return fm_signals, xmm_signals


def fusion_signal_to_continuous(fm_signals, xmm_signals) -> pd.Series:
    """
    将 FusionEngine 的离散信号转为连续 [-1, +1] 信号
    映射规则:
      STRONG_BUY  → +1.0
      BUY         → +0.5
      HOLD        →  0.0
      REDUCED     → +0.2
      SELL        → -0.5.0
      STRONG_SELL → -1.0
    """
    engine = FusionEngine()
    signal_map = {
        SignalLevel.STRONG_BUY: 1.0,
        SignalLevel.BUY: 0.5,
        SignalLevel.HOLD: 0.0,
        SignalLevel.REDUCED: 0.2,
        SignalLevel.SELL: -0.5,
        SignalLevel.STRONG_SELL: -1.0,
    }

    fm_dict = {s.date: s for s in fm_signals}
    xmm_dict = {s.date: s for s in xmm_signals}

    all_dates = sorted(set(fm_dict.keys()) | set(xmm_dict.keys()))
    signals = []
    for date in all_dates:
        fusion = engine.fuse(fm_dict.get(date), xmm_dict.get(date), "SPX")
        signals.append(signal_map.get(fusion.level, 0.0))

    return pd.Series(signals, index=pd.DatetimeIndex(all_dates), name="fusion_signal")


# ════════════════════════════════════════════════════════════════
# Tech17 信号
# ════════════════════════════════════════════════════════════════

TECH17 = [
    "rsi_14",
    "macd",
    "roc_10",
    "stoch_k_14",
    "ema_deviation",
    "sma_deviation",
    "adx_14",
    "trend_strength",
    "atr_14",
    "bb_width",
    "hist_vol_20",
    "obv_zscore",
    "volume_ratio",
    "vpt",
    "williams_r_14",
    "keltner_position",
    "donchian_position",
]


def compute_tech17_signal(kdata) -> pd.Series:
    """计算 Tech17 等权信号，使用 kdata 的日期作为索引"""
    engine = FactorComputeEngine()
    factor_df = engine.compute_single(kdata, factor_names=TECH17)

    # 全序列 z-score（与 validation 脚本一致的生产做法）
    normalized = pd.DataFrame(index=factor_df.index)
    for f in factor_df.columns:
        series = factor_df[f]
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        series = series.ffill().bfill().fillna(0)
        mu = series.mean()
        sd = series.std()
        if sd > 1e-10:
            normalized[f] = (series - mu) / sd
        else:
            normalized[f] = 0.0

    signal = normalized.mean(axis=1).clip(-1, 1)
    # 关键修复：factor_df 索引为整数，需映射回日期
    signal.index = pd.DatetimeIndex(kdata.df["date"].iloc[:len(signal)].values)
    return signal


# ════════════════════════════════════════════════════════════════
# 用同一个固定 BacktestEngine 回测
# ════════════════════════════════════════════════════════════════

def run_uniform_backtest(signal: pd.Series, prices: pd.Series, name: str) -> dict:
    """用统一的 BacktestEngine（已修复 look-ahead）回测"""
    sig_df = signal.to_frame("signal")
    pri_df = prices.to_frame("close")
    align_idx = sig_df.index.intersection(pri_df.index)
    sig_df = sig_df.loc[align_idx]
    pri_df = pri_df.loc[align_idx]

    if len(sig_df) < 20:
        return {"name": name, "sharpe": 0, "ret": 0, "dd": 0, "trades": 0, "status": "SKIP"}

    engine = BacktestEngine(initial_capital=1_000_000, commission_pct=0.0003, slippage_pct=0.0001)
    result = engine.run(sig_df, pri_df, max_position=0.2, max_total=0.8)
    metrics = compute_backtest_metrics(result)

    return {
        "name": name,
        "sharpe": metrics["sharpe_ratio"],
        "ret": metrics["total_return_pct"],
        "dd": metrics["max_drawdown_pct"],
        "trades": metrics["n_trades"],
        "hit_rate": metrics["hit_rate"],
        "status": "OK",
    }


# ════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════

def main():
    print("=" * 72)
    print("FusionEngine vs Tech17 — SPY 直接对比")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)

    # ── 1. 获取数据 ──
    from research.data.futu_provider import FutuProvider

    futu = FutuProvider()
    kdata = futu.fetch_klines("SPY", 200, "1d")
    print(f"\n数据: {kdata.n_bars} bars")

    kdata.df["date"] = pd.to_datetime(kdata.df["date"])
    close = pd.Series(
        kdata.df["close"].values,
        index=pd.DatetimeIndex(kdata.df["date"]),
        name="close",
    )

    # ── 2. Tech17 ──
    print("\n--- Tech17 ---")
    t0 = time.time()
    tech17_signal = compute_tech17_signal(kdata)
    elapsed = time.time() - t0
    print(f"  计算: {len(tech17_signal)} 天, {elapsed:.1f}s")
    tech17_res = run_uniform_backtest(tech17_signal, close, "Tech17 EW")
    print(f"  Sharpe={tech17_res['sharpe']:.3f}  Ret={tech17_res['ret']:+.2f}%  DD={tech17_res['dd']:.2f}%  "
          f"Trades={tech17_res['trades']}  HitRate={tech17_res['hit_rate']:.1%}")

    # ── 3. FusionEngine ──
    print("\n--- FusionEngine ---")
    t0 = time.time()
    dates = list(kdata.df["date"].astype(str))
    close_arr = close.values
    fm_sigs, xmm_sigs = gen_fusion_signals(close_arr, dates, "SPY")
    fusion_continuous = fusion_signal_to_continuous(fm_sigs, xmm_sigs)
    elapsed = time.time() - t0
    print(f"  计算: {len(fusion_continuous)} 天, {elapsed:.1f}s")
    fusion_res = run_uniform_backtest(fusion_continuous, close, "FusionEngine")
    print(f"  Sharpe={fusion_res['sharpe']:.3f}  Ret={fusion_res['ret']:+.2f}%  DD={fusion_res['dd']:.2f}%  "
          f"Trades={fusion_res['trades']}  HitRate={fusion_res['hit_rate']:.1%}")

    # ── 兼容性检查：signal 长度匹配 ──
    print(f"\n  FusionEngine signature range: {fusion_continuous.index[0].date()} ~ {fusion_continuous.index[-1].date()}")
    print(f"  Tech17 signal range:          {tech17_signal.index[0].date()} ~ {tech17_signal.index[-1].date()}")

    # ── 4. Report ──
    print("\n" + "=" * 72)
    print("对比结果")
    print("=" * 72)
    header = f"{'策略':<20} {'Sharpe':>8} {'Return%':>8} {'MaxDD%':>8} {'Trades':>8} {'HitRate':>8}"
    print(header)
    print("-" * 72)

    for r in [tech17_res, fusion_res]:
        print(
            f"{r['name']:<20} {r['sharpe']:>8.3f} {r['ret']:>+8.2f}% "
            f"{r['dd']:>8.2f}% {r['trades']:>8} {r.get('hit_rate', 0):>8.1%}"
        )

    # ── 信号分布对比 ──
    print("\n" + "-" * 72)
    print("信号分布对比")
    print("-" * 72)

    for name, sig in [("Tech17", tech17_signal), ("FusionEngine", fusion_continuous)]:
        print(f"\n{name}:")
        print(f"  Mean={sig.mean():+.4f}  Std={sig.std():.4f}  Min={sig.min():+.4f}  Max={sig.max():+.4f}")
        bullish = (sig >= 0.3).sum()
        bearish = (sig <= -0.3).sum()
        neutral = len(sig) - bullish - bearish
        print(f"  Buy={bullish}  Hold={neutral}  Sell={bearish}")

    # ── 信号相关性 ──
    common_idx = tech17_signal.index.intersection(fusion_continuous.index)
    if len(common_idx) > 10:
        corr = tech17_signal.loc[common_idx].corr(fusion_continuous.loc[common_idx])
        print(f"\n信号相关性 (Pearson): {corr:.3f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())