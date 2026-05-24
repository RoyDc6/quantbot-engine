"""
复杂度因子计算脚本
计算: Hurst指数, Higuchi分形维数, Approximate Entropy, Sample Entropy, Shannon熵
因子标签: 未来5日收益率
"""

import numpy as np
import pandas as pd
from scipy import signal
import warnings
warnings.filterwarnings('ignore')

# ========================
# 1. Hurst指数 (R/S方法)
# ========================
def hurst_rs(series, min_lag=2, max_lag=None):
    """R/S分析方法计算Hurst指数"""
    if len(series) < 10:
        return np.nan
    if max_lag is None:
        max_lag = len(series) // 2
    max_lag = max(min(max_lag, len(series) // 2), 3)
    
    series = np.array(series, dtype=float)
    n = len(series)
    
    lags = list(range(min_lag, max_lag))
    rs_vals = []
    
    for lag in lags:
        if lag < 2:
            continue
        subseries_list = []
        for start in range(0, n - lag + 1, lag):
            subseries = series[start:start + lag]
            mean_sub = np.mean(subseries)
            cumdev = np.cumsum(subseries - mean_sub)
            R = np.max(cumdev) - np.min(cumdev)
            S = np.std(subseries, ddof=1)
            if S > 1e-10:
                subseries_list.append(R / S)
        
        if len(subseries_list) > 0:
            rs_vals.append(np.mean(subseries_list))
    
    if len(rs_vals) < 3:
        return np.nan
    
    lags = lags[:len(rs_vals)]
    log_n = np.log(lags)
    log_rs = np.log(rs_vals)
    
    # OLS斜率 = Hurst
    x = log_n - np.mean(log_n)
    y = log_rs - np.mean(log_rs)
    denom = np.sum(x**2)
    if denom < 1e-10:
        return np.nan
    slope = np.sum(x * y) / denom
    return np.clip(slope, 0, 2)


# ========================
# 2. Higuchi分形维数
# ========================
def higuchi_fd(series, k_max=None):
    """Higuchi方法计算分形维数"""
    if len(series) < 8:
        return np.nan
    
    series = np.array(series, dtype=float)
    n = len(series)
    
    if k_max is None:
        k_max = max(2, n // 4)
    
    L = []
    k_vals = []
    
    for k in range(2, min(k_max + 1, n)):
        Lk = []
        for m in range(k):
            indices = np.arange(m, n, k)
            if len(indices) < 2:
                continue
            segment = series[indices]
            length = np.sum(np.abs(np.diff(segment))) * (n - 1) / (len(indices) * k)
            Lk.append(length)
        
        if len(Lk) > 0:
            L.append(np.mean(Lk))
            k_vals.append(k)
    
    if len(L) < 3:
        return np.nan
    
    log_k = np.log(k_vals)
    log_L = np.log(L)
    
    # OLS斜率 = -FD  => FD = -slope
    x = log_k - np.mean(log_k)
    y = log_L - np.mean(log_L)
    denom = np.sum(x**2)
    if denom < 1e-10:
        return np.nan
    slope = np.sum(x * y) / denom
    fd = -slope
    return np.clip(fd, 1, 3)


# ========================
# 3. Approximate Entropy (ApEn)
# ========================
def approx_entropy(series, m=2, r_factor=0.2):
    """Approximate Entropy - 序列规律性"""
    if len(series) < 12:
        return np.nan
    
    series = np.array(series, dtype=float)
    n = len(series)
    
    r = r_factor * np.std(series)
    if r < 1e-10:
        return np.nan
    
    def _phi(m_val):
        patterns = np.array([series[i:i+m_val] for i in range(n - m_val + 1)])
        count = np.zeros(len(patterns))
        for i in range(len(patterns)):
            dist = np.max(np.abs(patterns - patterns[i]), axis=1)
            count[i] = np.sum(dist <= r)
        return np.sum(np.log(count + 1e-10)) / (n - m_val + 1)
    
    phi_m = _phi(m)
    phi_m1 = _phi(m + 1)
    
    apen = phi_m - phi_m1
    return apen


# ========================
# 4. Sample Entropy (SampEn)
# ========================
def sample_entropy(series, m=2, r_factor=0.2):
    """Sample Entropy - ApEn的改进版，更稳定"""
    if len(series) < 12:
        return np.nan
    
    series = np.array(series, dtype=float)
    n = len(series)
    
    r = r_factor * np.std(series)
    if r < 1e-10:
        return np.nan
    
    patterns = np.array([series[i:i+m] for i in range(n - m + 1)])
    count_B = np.zeros(len(patterns))
    for i in range(len(patterns)):
        dist = np.max(np.abs(patterns - patterns[i]), axis=1)
        count_B[i] = np.sum(dist <= r) - 1  # 排除自身
    B = np.sum(count_B) / (n - m)
    
    patterns2 = np.array([series[i:i+m+1] for i in range(n - m)])
    count_A = np.zeros(len(patterns2))
    for i in range(len(patterns2)):
        dist = np.max(np.abs(patterns2 - patterns2[i]), axis=1)
        count_A[i] = np.sum(dist <= r) - 1  # 排除自身
    A = np.sum(count_A) / (n - m - 1)
    
    if A < 1e-10 or B < 1e-10:
        return np.nan
    
    sampen = -np.log(A / B)
    return sampen


# ========================
# 5. Shannon熵 (日收益离散化)
# ========================
def shannon_entropy(series, bins=8):
    """Shannon熵 - 收益率分布的信息量"""
    if len(series) < 10:
        return np.nan
    
    series = np.array(series, dtype=float)
    # 归一化收益率
    series_norm = (series - np.mean(series)) / (np.std(series) + 1e-10)
    
    # 离散化
    try:
        hist, _ = np.histogram(series_norm, bins=bins, density=True)
    except:
        return np.nan
    
    hist = hist[hist > 0]  # 去掉零概率
    entropy = -np.sum(hist * np.log(hist + 1e-10))
    return entropy


# ========================
# 主计算函数
# ========================
def compute_complexity_factors(returns_window):
    """
    给定一个窗口的收益率序列，返回所有复杂度因子
    returns_window: array-like, 长度 >= 窗口长度
    """
    r = np.array(returns_window, dtype=float)
    
    if len(r) < 20:
        return {
            'hurst_20': np.nan, 'hurst_60': np.nan,
            'higuchi_fd_20': np.nan, 'higuchi_fd_60': np.nan,
            'apen_20': np.nan, 'apen_60': np.nan,
            'sampen_20': np.nan, 'sampen_60': np.nan,
            'entropy_20': np.nan, 'entropy_60': np.nan,
        }
    
    results = {}
    
    # 20日窗口因子
    if len(r) >= 20:
        r20 = r[-20:]
        results['hurst_20'] = hurst_rs(r20, min_lag=2, max_lag=8)
        results['higuchi_fd_20'] = higuchi_fd(r20, k_max=5)
        results['apen_20'] = approx_entropy(r20, m=2, r_factor=0.2)
        results['sampen_20'] = sample_entropy(r20, m=2, r_factor=0.2)
        results['entropy_20'] = shannon_entropy(r20, bins=8)
    else:
        results['hurst_20'] = np.nan
        results['higuchi_fd_20'] = np.nan
        results['apen_20'] = np.nan
        results['sampen_20'] = np.nan
        results['entropy_20'] = np.nan
    
    # 60日窗口因子
    if len(r) >= 60:
        r60 = r[-60:]
        results['hurst_60'] = hurst_rs(r60, min_lag=2, max_lag=15)
        results['higuchi_fd_60'] = higuchi_fd(r60, k_max=10)
        results['apen_60'] = approx_entropy(r60, m=2, r_factor=0.2)
        results['sampen_60'] = sample_entropy(r60, m=2, r_factor=0.2)
        results['entropy_60'] = shannon_entropy(r60, bins=8)
    else:
        results['hurst_60'] = np.nan
        results['higuchi_fd_60'] = np.nan
        results['apen_60'] = np.nan
        results['sampen_60'] = np.nan
        results['entropy_60'] = np.nan
    
    return results


if __name__ == '__main__':
    # 简单测试
    np.random.seed(42)
    # 趋势序列
    trend = np.cumsum(np.random.randn(100) * 0.01 + 0.001)
    trend_returns = np.diff(trend) / trend[:-1]
    print("趋势序列:", compute_complexity_factors(trend_returns))
    
    # 随机游走
    random_walk = np.cumsum(np.random.randn(100) * 0.01)
    random_returns = np.diff(random_walk) / random_walk[:-1]
    print("随机序列:", compute_complexity_factors(random_returns))
    
    # 震荡序列
    oscillation = np.sin(np.linspace(0, 10*np.pi, 100))
    osc_returns = np.diff(oscillation) / (oscillation[:-1] + 1e-10)
    print("震荡序列:", compute_complexity_factors(osc_returns))
