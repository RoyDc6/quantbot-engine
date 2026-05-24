"""
research/analysis/correlation.py — 因子相关性分析
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def factor_correlation(factor_df: pd.DataFrame,
                       method: str = "pearson") -> pd.DataFrame:
    """计算因子间相关系数矩阵"""
    return factor_df.corr(method=method)


def top_correlated_pairs(corr_matrix: pd.DataFrame,
                         threshold: float = 0.7,
                         top_n: int = 10) -> pd.DataFrame:
    """返回高相关性因子对"""
    upper = corr_matrix.where(
        np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))
    pairs = upper.unstack().dropna().sort_values(ascending=False)
    pairs = pairs[abs(pairs) >= threshold]
    return pairs.head(top_n).reset_index()


def cluster_factors(corr_matrix: pd.DataFrame,
                    threshold: float = 0.5) -> dict:
    """基于相关性聚类因子（简单贪婪聚类）"""
    factors = list(corr_matrix.columns)
    assigned = set()
    clusters = {}

    for i, f1 in enumerate(factors):
        if f1 in assigned:
            continue
        cluster = [f1]
        assigned.add(f1)
        for f2 in factors[i + 1:]:
            if f2 in assigned:
                continue
            if abs(corr_matrix.loc[f1, f2]) >= threshold:
                cluster.append(f2)
                assigned.add(f2)
        if len(cluster) > 1:
            clusters[f"cluster_{len(clusters) + 1}"] = cluster

    return clusters