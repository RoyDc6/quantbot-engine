"""
research/factors/compute.py — 批量因子计算引擎

支持:
- 单标的全因子计算
- 多标的批量因子计算
- 自动注册所有已知因子
- 因子值 DataFrame 输出
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd

from .registry import FactorRegistry, FactorMeta
from .technical import (
    RSIFactor, MACDFactor, ROCFactor, StochasticFactor,
    EMAFactor, SMAFactor, ADXFactor, TrendStrengthFactor,
    ATRFactor, BollingerBandWidthFactor, HistoricalVolatilityFactor,
    OBVFactor, VolumeRatioFactor, VPTFactor,
    MFIFactor, WilliamsRFactor, CCIFactor, IchimokuBaseFactor,
    VWAPDeviationFactor, AroonOscillatorFactor, KeltnerPositionFactor,
    DonchianPositionFactor,
    VolumeWeightedMomentum, CloseLocationValue, VolatilityRegime,
    VolatilityOfVolatility, EfficiencyRatio, PriceAcceleration,
    GapFactor, VolumePriceCorrelation,
    XMM30mFactor,
)
from .llm import (
    LLMRegimeFactor, LLMRegimeConfidenceFactor,
    LLMSentimentFactor, LLMDivergenceFactor,
    LLMPatternFactor, LLMAnomalyFactor,
)
from .alternative import (
    KronosFactor,
)
from ..data.base import KLineData, UnifiedDataProvider


# ============================================================
# 自动注册所有内置因子
# ============================================================
_BUILTIN_FACTORS = [
    # 技术因子 (14) -- Phase 1
    RSIFactor, MACDFactor, ROCFactor, StochasticFactor,
    EMAFactor, SMAFactor, ADXFactor, TrendStrengthFactor,
    ATRFactor, BollingerBandWidthFactor, HistoricalVolatilityFactor,
    OBVFactor, VolumeRatioFactor, VPTFactor,
    # 技术因子 (8) -- Phase 2: 进阶因子
    MFIFactor, WilliamsRFactor, CCIFactor, IchimokuBaseFactor,
    VWAPDeviationFactor, AroonOscillatorFactor, KeltnerPositionFactor,
    DonchianPositionFactor,
    # 技术因子 (8) -- Phase 3: 微观结构 + 波动率体制
    VolumeWeightedMomentum, CloseLocationValue, VolatilityRegime,
    VolatilityOfVolatility, EfficiencyRatio, PriceAcceleration,
    GapFactor, VolumePriceCorrelation,
    # XMM research factor
    XMM30mFactor,
    # LLM 因子 (6)
    LLMRegimeFactor, LLMRegimeConfidenceFactor,
    LLMSentimentFactor, LLMDivergenceFactor,
    LLMPatternFactor, LLMAnomalyFactor,
    # 另类因子 (1)
    KronosFactor,
]

for _f in _BUILTIN_FACTORS:
    FactorRegistry.register(_f)


class FactorComputeEngine:
    """因子计算引擎 — 批量调度因子计算"""

    def __init__(self, data_provider: Optional[UnifiedDataProvider] = None):
        self.provider = data_provider

    def compute_single(self, data: KLineData,
                       factor_names: Optional[List[str]] = None,
                       market: Optional[str] = None,
                       category: Optional[str] = None,
                       **params) -> pd.DataFrame:
        """对单个标的计算一组因子

        Args:
            data: K 线数据
            factor_names: 指定因子名列表（None 则使用 market/category 筛选）
            market: 市场筛选
            category: 类别筛选
            params: 覆盖因子默认参数

        Returns:
            DataFrame: 列为因子名，行与 data.df 对齐
        """
        if factor_names:
            results = FactorRegistry.compute_batch(factor_names, data, **params)
        else:
            results = FactorRegistry.compute_all(data, market, category, **params)

        # 过滤 None 并合并
        valid = {k: v for k, v in results.items() if v is not None}
        if not valid:
            return pd.DataFrame()

        df = pd.DataFrame(valid)
        return df

    def compute_multi(self, data_map: Dict[str, KLineData],
                      factor_names: Optional[List[str]] = None,
                      **params) -> Dict[str, pd.DataFrame]:
        """对多个标的计算因子

        Args:
            data_map: {symbol: KLineData} 映射
            factor_names: 因子名列表

        Returns:
            {symbol: factor_df} 映射
        """
        results = {}
        for symbol, kdata in data_map.items():
            try:
                df = self.compute_single(kdata, factor_names, **params)
                results[symbol] = df
            except Exception as e:
                print(f"[FactorEngine] {symbol} 计算失败: {e}")
        return results

    def list_registered_factors(self, market: Optional[str] = None,
                                category: Optional[str] = None) -> List[FactorMeta]:
        """列出已注册因子"""
        return FactorRegistry.list_factors(market, category)

    @property
    def factor_count(self) -> int:
        return FactorRegistry.count()
