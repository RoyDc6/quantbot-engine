"""
research/analysis/visualization.py — 研究可视化工具

使用 matplotlib 绘制因子分析图表，不依赖 mplfinance。
"""

from __future__ import annotations

from typing import List, Optional, Dict

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker


def plot_ic_timeseries(ic_df: pd.DataFrame,
                       title: str = "IC Time Series",
                       figsize=(12, 5),
                       save_path: Optional[str] = None):
    """绘制 IC 时间序列"""
    fig, ax = plt.subplots(figsize=figsize)
    for col in ic_df.columns:
        if col != "rolling_mean":
            ax.plot(ic_df.index, ic_df[col], alpha=0.3, linewidth=0.5)
    if "rolling_mean" in ic_df.columns:
        ax.plot(ic_df.index, ic_df["rolling_mean"],
                color="red", linewidth=2, label="Rolling Mean")
    ax.axhline(0, color="gray", linestyle="--", alpha=0.5)
    ax.set_title(title)
    ax.legend()
    ax.grid(alpha=0.3)
    if save_path:
        fig.savefig(save_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def plot_factor_heatmap(corr_matrix: pd.DataFrame,
                        title: str = "Factor Correlation",
                        figsize=(10, 8),
                        save_path: Optional[str] = None):
    """绘制因子相关性热力图"""
    fig, ax = plt.subplots(figsize=figsize)
    im = ax.imshow(corr_matrix.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(corr_matrix.columns)))
    ax.set_yticks(range(len(corr_matrix.columns)))
    ax.set_xticklabels(corr_matrix.columns, rotation=45, ha="right", fontsize=8)
    ax.set_yticklabels(corr_matrix.columns, fontsize=8)
    plt.colorbar(im, ax=ax, shrink=0.8)
    ax.set_title(title)
    if save_path:
        fig.savefig(save_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def plot_ic_decay(decay_df: pd.DataFrame,
                  title: str = "IC Decay",
                  figsize=(8, 4),
                  save_path: Optional[str] = None):
    """绘制 IC 衰减曲线"""
    fig, ax = plt.subplots(figsize=figsize)
    ax.plot(decay_df.index, decay_df["IC"],
            marker="o", linewidth=2, color="blue")
    ax.axhline(0, color="gray", linestyle="--", alpha=0.5)
    ax.set_title(title)
    ax.set_xlabel("Holding Period")
    ax.set_ylabel("IC")
    ax.grid(alpha=0.3)
    if save_path:
        fig.savefig(save_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def plot_factor_distribution(factor_values: pd.Series,
                             title: str = "Factor Distribution",
                             bins: int = 50,
                             figsize=(10, 4),
                             save_path: Optional[str] = None):
    """绘制因子值分布"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=figsize)
    valid = factor_values.dropna()
    ax1.hist(valid, bins=bins, alpha=0.7, color="steelblue", edgecolor="white")
    ax1.set_title(f"{title} (Histogram)")
    ax1.set_xlabel("Factor Value")
    ax1.set_ylabel("Frequency")
    ax2.boxplot(valid, vert=False)
    ax2.set_title(f"{title} (Box Plot)")
    if save_path:
        fig.savefig(save_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def plot_quantile_returns(returns: pd.Series,
                          quantiles: int = 5,
                          title: str = "Quantile Returns",
                          figsize=(8, 4),
                          save_path: Optional[str] = None):
    """绘制分组收益（五分位）"""
    valid = returns.dropna()
    labels = [f"Q{i+1}" for i in range(quantiles)]
    valid_q = pd.qcut(valid, quantiles, labels=labels)
    group_means = valid.groupby(valid_q).mean()

    fig, ax = plt.subplots(figsize=figsize)
    colors = ["red" if v < 0 else "green" for v in group_means]
    ax.bar(range(len(group_means)), group_means.values, color=colors, alpha=0.7)
    ax.set_xticks(range(len(group_means)))
    ax.set_xticklabels(labels)
    ax.axhline(0, color="gray", linestyle="--", alpha=0.5)
    ax.set_title(title)
    ax.set_ylabel("Mean Return")
    ax.grid(alpha=0.3, axis="y")
    if save_path:
        fig.savefig(save_path, dpi=100, bbox_inches="tight")
    plt.close(fig)