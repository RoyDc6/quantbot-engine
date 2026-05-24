"""
research/models/evaluation.py — 模型评估指标

提供因子和模型评估的标准指标集合。
所有函数接受 pandas Series/DataFrame，返回数值或 DataFrame。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np
from scipy import stats


def calc_ic(factor_values: pd.Series, forward_returns: pd.Series,
            method: str = "spearman") -> float:
    """计算 Information Coefficient

    Args:
        factor_values: 因子值序列
        forward_returns: 未来收益率序列
        method: "spearman" (Rank IC) | "pearson" (Normal IC)

    Returns:
        IC 值
    """
    valid = pd.concat([factor_values, forward_returns], axis=1).dropna()
    if len(valid) < 10:
        return 0.0

    if method == "spearman":
        ic, _ = stats.spearmanr(valid.iloc[:, 0], valid.iloc[:, 1])
    else:
        ic, _ = stats.pearsonr(valid.iloc[:, 0], valid.iloc[:, 1])

    return ic if not np.isnan(ic) else 0.0


def calc_ic_series(factor_df: pd.DataFrame,
                   forward_returns: pd.Series,
                   method: str = "spearman") -> pd.Series:
    """计算因子 IC 时间序列（截面 IC）

    Args:
        factor_df: 多标的因子值 DataFrame，列为标的
        forward_returns: 未来收益率 DataFrame，列为标的

    Returns:
        每日 IC 序列
    """
    dates = factor_df.index
    ic_values = []

    for date in dates:
        if date not in forward_returns.index:
            ic_values.append(np.nan)
            continue

        fv = factor_df.loc[date]
        fr = forward_returns.loc[date]
        valid = pd.concat([fv, fr], axis=1).dropna()

        if len(valid) < 10:
            ic_values.append(np.nan)
            continue

        if method == "spearman":
            ic, _ = stats.spearmanr(valid.iloc[:, 0], valid.iloc[:, 1])
        else:
            ic, _ = stats.pearsonr(valid.iloc[:, 0], valid.iloc[:, 1])

        ic_values.append(ic if not np.isnan(ic) else 0.0)

    return pd.Series(ic_values, index=dates, name=f"IC_{method}")


def calc_ic_decay(factor_df: pd.DataFrame,
                  forward_returns_dict: Dict[int, pd.Series],
                  method: str = "spearman",
                  max_lag: int = 10) -> pd.DataFrame:
    """计算 IC 衰减（不同持有期的 IC）

    Args:
        factor_df: 因子值
        forward_returns_dict: {持有期: 收益率} 字典
        method: 相关方法
        max_lag: 最大滞后天数

    Returns:
        DataFrame: index=持有期, columns=[IC, t-stat, p-value]
    """
    results = []
    for horizon, fr in sorted(forward_returns_dict.items()):
        ic = calc_ic(factor_df.iloc[:, 0] if isinstance(factor_df, pd.DataFrame)
                     and factor_df.shape[1] == 1 else factor_df.mean(axis=1),
                     fr, method)
        results.append({"horizon": horizon, "IC": ic})

    return pd.DataFrame(results).set_index("horizon")


def calc_sharpe(returns: pd.Series, annual_factor: int = 252) -> float:
    """计算 Sharpe Ratio

    Args:
        returns: 收益率序列
        annual_factor: 年化因子（日=252, 小时=252*6.5, 分钟=252*6.5*60）

    Returns:
        年化 Sharpe Ratio
    """
    valid = returns.dropna()
    if len(valid) < 10:
        return 0.0

    excess = valid  # 假设无风险利率为 0（研究环境）
    sharpe = excess.mean() / excess.std() * np.sqrt(annual_factor)
    return sharpe if not np.isnan(sharpe) else 0.0


def calc_max_drawdown(equity_curve: pd.Series) -> float:
    """计算最大回撤

    Args:
        equity_curve: 净值曲线

    Returns:
        最大回撤（百分比，正值）
    """
    if len(equity_curve) < 2:
        return 0.0

    rolling_max = equity_curve.expanding().max()
    drawdown = (equity_curve - rolling_max) / rolling_max
    max_dd = abs(drawdown.min())
    return max_dd if not np.isnan(max_dd) else 0.0


def calc_hit_rate(signals: pd.Series, forward_returns: pd.Series,
                  threshold: float = 0.0) -> float:
    """计算命中率（方向准确率）

    Args:
        signals: 信号值（正=看多，负=看空）
        forward_returns: 未来收益率
        threshold: 信号阈值

    Returns:
        命中率 (0~1)
    """
    valid = pd.concat([signals, forward_returns], axis=1).dropna()
    if len(valid) < 10:
        return 0.0

    # 信号方向与收益方向一致的次数
    correct = ((valid.iloc[:, 0] > threshold) & (valid.iloc[:, 1] > 0)) | \
              ((valid.iloc[:, 0] < -threshold) & (valid.iloc[:, 1] < 0))
    return correct.sum() / len(valid)


def calc_turnover(signals: pd.Series) -> float:
    """计算换手率（信号变化频率）

    Args:
        signals: 信号序列

    Returns:
        日均换手率 (0~1)
    """
    changes = (signals.diff() != 0).sum()
    return changes / max(len(signals), 1)


def evaluate_predictions(y_true: pd.Series,
                         y_pred: pd.Series) -> Dict[str, float]:
    """评估预测结果综合指标

    Args:
        y_true: 真实值
        y_pred: 预测值

    Returns:
        指标字典
    """
    valid = pd.concat([y_true, y_pred], axis=1).dropna()
    if len(valid) < 10:
        return {"IC": 0.0, "hit_rate": 0.0, "n_samples": 0}

    ic, _ = stats.spearmanr(valid.iloc[:, 0], valid.iloc[:, 1])

    # 方向准确率
    correct = ((valid.iloc[:, 1] > 0) & (valid.iloc[:, 0] > 0)) | \
              ((valid.iloc[:, 1] < 0) & (valid.iloc[:, 0] < 0))
    hit_rate = correct.sum() / len(valid)

    return {
        "IC": ic if not np.isnan(ic) else 0.0,
        "hit_rate": float(hit_rate),
        "n_samples": len(valid),
    }


def factor_summary(factor_values: pd.Series,
                   forward_returns: pd.Series) -> Dict[str, float]:
    """因子综合评估摘要

    Args:
        factor_values: 因子值
        forward_returns: 未来收益率

    Returns:
        {metric: value} 字典
    """
    ic = calc_ic(factor_values, forward_returns)
    rank_ic = calc_ic(factor_values, forward_returns, method="spearman")
    hit = calc_hit_rate(factor_values, forward_returns)

    # 分组收益（五分位）
    valid = pd.concat([factor_values, forward_returns], axis=1).dropna()
    if len(valid) >= 50:
        valid.columns = ["factor", "return"]
        valid["quantile"] = pd.qcut(valid["factor"], 5, labels=False, duplicates='drop')
        group_returns = valid.groupby("quantile")["return"].mean()
        spread = group_returns.iloc[-1] - group_returns.iloc[0]
    else:
        spread = 0.0

    return {
        "IC": round(ic, 4),
        "Rank_IC": round(rank_ic, 4),
        "hit_rate": round(hit, 4),
        "long_short_spread": round(spread, 6),
        "n_samples": len(valid),
    }