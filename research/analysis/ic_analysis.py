"""
research/analysis/ic_analysis.py — 因子 IC 分析工具

计算和可视化因子的预测能力：
- 截面 IC / Rank IC
- IC 时间序列
- IC 衰减曲线
- 分组收益（Quintile Spread）
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from ..models.evaluation import calc_ic, calc_ic_series, factor_summary


class ICAnalyzer:
    """因子 IC 分析器

    分析单个因子或多个因子的预测能力。
    """

    def __init__(self, factor_df: pd.DataFrame,
                 forward_returns: pd.Series):
        """
        Args:
            factor_df: 因子值 DataFrame，列为标的
            forward_returns: 未来收益率 Series，index 与 factor_df 对齐
        """
        self.factor_df = factor_df
        self.forward_returns = forward_returns

    def analyze_single(self, factor_name: str) -> Dict:
        """分析单个因子

        Returns:
            包含 IC、RankIC、命中率等指标的字典
        """
        if factor_name not in self.factor_df.columns:
            return {"error": f"因子 {factor_name} 不存在"}

        fv = self.factor_df[factor_name]
        return factor_summary(fv, self.forward_returns)

    def analyze_all(self) -> pd.DataFrame:
        """分析所有因子的 IC

        Returns:
            DataFrame: index=因子名, columns=[IC, Rank_IC, hit_rate, ...]
        """
        results = []
        for col in self.factor_df.columns:
            summary = factor_summary(self.factor_df[col],
                                     self.forward_returns)
            summary["factor"] = col
            results.append(summary)

        df = pd.DataFrame(results)
        if not df.empty:
            df = df.set_index("factor")
        return df

    def ic_time_series(self, method: str = "spearman",
                       rolling_window: int = 20) -> pd.DataFrame:
        """计算 IC 时间序列

        Args:
            method: "spearman" | "pearson"
            rolling_window: 滚动窗口

        Returns:
            DataFrame: 每个因子的 IC 时间序列
        """
        ic_dict = {}
        for col in self.factor_df.columns:
            ic_series = calc_ic_series(
                self.factor_df[[col]],
                self.forward_returns.to_frame("ret"),
                method=method,
            )
            ic_dict[col] = ic_series

        ic_df = pd.DataFrame(ic_dict)
        ic_df["rolling_mean"] = ic_df.mean(axis=1).rolling(
            rolling_window).mean()
        return ic_df

    def best_factors(self, top_n: int = 5,
                     metric: str = "Rank_IC") -> List[Tuple[str, float]]:
        """返回 IC 最高的前 N 个因子

        Args:
            top_n: 返回数量
            metric: 排序指标 ("IC", "Rank_IC", "hit_rate")

        Returns:
            [(factor_name, value), ...]
        """
        results = self.analyze_all()
        if results.empty or metric not in results.columns:
            return []

        sorted_results = results.sort_values(metric, ascending=False)
        return [(idx, row[metric])
                for idx, row in sorted_results.head(top_n).iterrows()]


def compute_forward_returns(close_df: pd.DataFrame,
                            horizon: int = 1) -> pd.Series:
    """计算未来收益率

    Args:
        close_df: 收盘价 DataFrame，列为标的
        horizon: 持有期（天数）

    Returns:
        未来收益率 Series（与 close_df 前 N 行对齐）
    """
    fwd_ret = close_df.pct_change(periods=horizon).shift(-horizon)
    return fwd_ret.stack().droplevel(1).rename(f"fwd_ret_{horizon}")