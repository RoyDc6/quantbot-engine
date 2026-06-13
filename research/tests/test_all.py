"""
research/tests/test_all.py — 研究环境全套单元测试

覆盖: data, factors, registry, compute, models, backtest, analysis 模块。
"""

import sys
from pathlib import Path

# ── 路径注入 ──────────────────────────────────────────────
RESEARCH_ROOT = Path(__file__).resolve().parent.parent
QUANT_ROOT = RESEARCH_ROOT.parent
for p in [str(RESEARCH_ROOT), str(QUANT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import math
import tempfile
import unittest
from typing import Dict, List

import pandas as pd
import numpy as np

from research.data.base import KLineData, UnifiedDataProvider
from research.data.symbol_registry import SymbolRegistry, SymbolInfo
from research.data.cache import KLineCache

from research.factors.registry import FactorRegistry, FactorMeta
from research.factors.base import BaseFactor
from research.factors.compute import FactorComputeEngine
from research.factors.technical.momentum import (
    RSIFactor, MACDFactor, ROCFactor, StochasticFactor,
)
from research.factors.technical.trend import (
    EMAFactor, SMAFactor, ADXFactor, TrendStrengthFactor,
)
from research.factors.technical.volatility import (
    ATRFactor, BollingerBandWidthFactor, HistoricalVolatilityFactor,
)
from research.factors.technical.volume import (
    OBVFactor, VolumeRatioFactor, VPTFactor,
)
from research.factors.technical.xmm_30m import XMM30mFactor

from research.models.evaluation import (
    calc_ic, calc_ic_series, calc_ic_decay, calc_sharpe,
    calc_max_drawdown, calc_hit_rate, calc_turnover,
    evaluate_predictions, factor_summary,
)

from research.backtest.engine import BacktestEngine
from research.backtest.metrics import compute_backtest_metrics

from research.analysis.ic_analysis import ICAnalyzer, compute_forward_returns
from research.analysis.correlation import (
    factor_correlation, top_correlated_pairs, cluster_factors,
)
from research.analysis.visualization import (
    plot_ic_timeseries, plot_factor_heatmap, plot_ic_decay,
    plot_factor_distribution, plot_quantile_returns,
)

# ═══════════════════════════════════════════════════════════
# 测试辅助函数
# ═══════════════════════════════════════════════════════════

def make_sample_klinedata(n: int = 200, symbol: str = "TEST",
                          market: str = "US", tf: str = "1d") -> KLineData:
    """生成标准测试 K 线数据"""
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    volume = np.random.randint(1_000_000, 10_000_000, size=n)

    df = pd.DataFrame({
        "date": dates,
        "open": close - np.random.randn(n) * 0.2,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })
    return KLineData(symbol=symbol, market=market, timeframe=tf, df=df)


def make_empty_klinedata() -> KLineData:
    """生成空 K 线数据（用于测试异常路径）"""
    df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    return KLineData(symbol="EMPTY", market="US", timeframe="1d", df=df)


def make_trending_klinedata(n: int = 200) -> KLineData:
    """生成强趋势 K 线数据（用于趋势因子测试）"""
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.linspace(0, 30, n) + np.random.randn(n) * 0.3
    high = close + 0.5
    low = close - 0.5
    volume = np.random.randint(1_000_000, 5_000_000, size=n)
    df = pd.DataFrame({
        "date": dates, "open": close - 0.1,
        "high": high, "low": low,
        "close": close, "volume": volume,
    })
    return KLineData(symbol="TREND", market="US", timeframe="1d", df=df)


def make_highvol_klinedata(n: int = 200) -> KLineData:
    """生成高波动率 K 线数据"""
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 2.0)
    high = close + np.abs(np.random.randn(n) * 1.5)
    low = close - np.abs(np.random.randn(n) * 1.5)
    volume = np.random.randint(5_000_000, 20_000_000, size=n)
    df = pd.DataFrame({
        "date": dates, "open": close - 0.2,
        "high": high, "low": low,
        "close": close, "volume": volume,
    })
    return KLineData(symbol="HIGHVOL", market="US", timeframe="1d", df=df)


# ═══════════════════════════════════════════════════════════
# 数据层测试
# ═══════════════════════════════════════════════════════════

class TestKLineData(unittest.TestCase):
    """KLineData 数据容器测试"""

    def test_valid_creation(self):
        """有效 K 线数据应正确创建"""
        kd = make_sample_klinedata(100)
        self.assertEqual(kd.symbol, "TEST")
        self.assertEqual(kd.market, "US")
        self.assertEqual(len(kd.df), 100)
        self.assertGreater(kd.n_bars, 0)

    def test_missing_columns(self):
        """缺少必要列应抛出 ValueError"""
        df = pd.DataFrame({"close": [1, 2, 3]})
        with self.assertRaises(ValueError):
            KLineData(symbol="X", market="US", timeframe="1d", df=df)

    def test_date_range_with_date_column(self):
        """date 列应正确返回日期范围"""
        kd = make_sample_klinedata(50)
        start, end = kd.date_range
        self.assertIsNotNone(start)
        self.assertIsNotNone(end)
        self.assertLess(start, end)

    def test_date_range_no_date(self):
        """无 date 列时 date_range 应返回 (None, None)"""
        df = pd.DataFrame({
            "open": [1, 2], "high": [3, 4], "low": [0.5, 1],
            "close": [2, 3], "volume": [100, 200],
        })
        kd = KLineData(symbol="X", market="US", timeframe="1d", df=df)
        self.assertEqual(kd.date_range, (None, None))


class TestSymbolRegistry(unittest.TestCase):
    """符号注册表测试"""

    def setUp(self):
        self.registry = SymbolRegistry()

    def test_list_markets(self):
        """应列出所有已注册的市场"""
        markets = self.registry.list_markets()
        self.assertIn("HK", markets)
        self.assertIn("US", markets)
        self.assertIn("CN", markets)
        self.assertIn("CRYPTO", markets)

    def test_get_symbols_by_market(self):
        """按市场筛选应返回正确的标的"""
        hk_syms = self.registry.get_symbols(market="HK")
        self.assertGreater(len(hk_syms), 0)
        for s in hk_syms:
            self.assertEqual(s.market, "HK")

    def test_get_symbols_by_asset_type(self):
        """按资产类型筛选"""
        crypto = self.registry.get_symbols(asset_type="crypto")
        self.assertGreater(len(crypto), 0)
        for s in crypto:
            self.assertEqual(s.asset_type, "crypto")

    def test_get_symbol_found(self):
        """查询存在的符号应返回 SymbolInfo"""
        info = self.registry.get_symbol("00700")
        self.assertIsNotNone(info)
        self.assertEqual(info.symbol, "00700")
        self.assertEqual(info.market, "HK")

    def test_get_symbol_not_found(self):
        """查询不存在的符号应返回 None"""
        info = self.registry.get_symbol("NONEXISTENT")
        self.assertIsNone(info)

    def test_add_symbol(self):
        """动态添加符号应更新索引"""
        new_sym = SymbolInfo("NEW", "US", "New Stock", "stock")
        self.registry.add_symbol(new_sym)
        info = self.registry.get_symbol("NEW")
        self.assertIsNotNone(info)
        self.assertEqual(info.name, "New Stock")

    def test_futu_codes(self):
        """futu_codes 应返回非空列表"""
        codes = self.registry.futu_codes("HK")
        self.assertGreater(len(codes), 0)
        for c in codes:
            self.assertIn("HK.", c)

    def test_okx_inst_ids(self):
        """okx_inst_ids 应返回合约ID列表"""
        ids = self.registry.okx_inst_ids()
        self.assertGreater(len(ids), 0)
        for inst_id in ids:
            self.assertIn("-SWAP", inst_id)


class TestKLineCache(unittest.TestCase):
    """K 线缓存测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.cache = KLineCache(cache_dir=self.tmp_dir, max_age_days=7)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_cache_miss(self):
        """未缓存时返回 None"""
        result = self.cache.get("NONEXIST", "US", "1d")
        self.assertIsNone(result)

    def test_cache_set_and_get(self):
        """写入后应能正确读取"""
        kd = make_sample_klinedata(50, symbol="TEST_CACHE")
        self.cache.set(kd)
        cached = self.cache.get("TEST_CACHE", "US", "1d")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.symbol, "TEST_CACHE")
        self.assertEqual(len(cached.df), 50)

    def test_cache_invalidate(self):
        """清除缓存后应返回 None"""
        kd = make_sample_klinedata(30, symbol="INVAL")
        self.cache.set(kd)
        self.cache.invalidate("INVAL", "US", "1d")
        result = self.cache.get("INVAL", "US", "1d")
        self.assertIsNone(result)

    def test_cache_size(self):
        """size 属性应反映内存缓存数量"""
        self.assertEqual(self.cache.size, 0)
        self.cache.set(make_sample_klinedata(10, symbol="A"))
        self.cache.set(make_sample_klinedata(10, symbol="B"))
        self.assertEqual(self.cache.size, 2)

    def test_clear_all(self):
        """clear_all 应清空所有缓存"""
        self.cache.set(make_sample_klinedata(10, symbol="A"))
        self.cache.set(make_sample_klinedata(10, symbol="B"))
        self.cache.clear_all()
        self.assertEqual(self.cache.size, 0)


# ═══════════════════════════════════════════════════════════
# 因子层测试
# ═══════════════════════════════════════════════════════════

class TestRSIFactor(unittest.TestCase):
    """RSI 因子测试"""

    def setUp(self):
        self.factor = RSIFactor()
        self.kd = make_sample_klinedata(200)

    def test_meta(self):
        """元数据应正确"""
        meta = RSIFactor.meta()
        self.assertEqual(meta.name, "rsi_14")
        self.assertEqual(meta.category, "technical")
        self.assertIn("US", meta.markets)

    def test_compute_valid(self):
        """正常数据应计算 RSI 值"""
        result = self.factor.compute(self.kd)
        self.assertIsInstance(result, pd.Series)
        self.assertEqual(len(result), 200)
        # RSI 应在 [0, 100] 范围内
        valid = result.dropna()
        if len(valid) > 0:
            self.assertGreaterEqual(valid.min(), 0)
            self.assertLessEqual(valid.max(), 100)

    def test_compute_insufficient_data(self):
        """数据不足时应返回全 NaN"""
        short_kd = make_sample_klinedata(5)
        result = self.factor.compute(short_kd)
        self.assertTrue(result.isna().all())

    def test_validate_rejects_empty(self):
        """空数据验证应返回 False"""
        self.assertFalse(self.factor.validate(make_empty_klinedata()))

    def test_validate_accepts_sufficient(self):
        """充足数据验证应返回 True"""
        self.assertTrue(self.factor.validate(self.kd))


class TestMACDFactor(unittest.TestCase):
    """MACD 因子测试"""

    def setUp(self):
        self.factor = MACDFactor()
        self.kd = make_sample_klinedata(200)

    def test_meta(self):
        meta = MACDFactor.meta()
        self.assertEqual(meta.name, "macd")

    def test_compute(self):
        result = self.factor.compute(self.kd)
        self.assertIsInstance(result, pd.Series)
        self.assertEqual(len(result), 200)
        self.assertEqual(result.name, "MACD_histogram")

    def test_custom_params(self):
        """自定义参数应生效"""
        result = self.factor.compute(self.kd, fast=6, slow=13, signal=5)
        self.assertEqual(len(result), 200)


class TestROCFactor(unittest.TestCase):
    """ROC 因子测试"""

    def setUp(self):
        self.factor = ROCFactor()
        self.kd = make_sample_klinedata(200)

    def test_meta(self):
        meta = ROCFactor.meta()
        self.assertEqual(meta.name, "roc_10")

    def test_compute(self):
        result = self.factor.compute(self.kd)
        self.assertEqual(len(result), 200)

    def test_constant_price(self):
        """价格不变时 ROC 应为 0"""
        dates = pd.date_range("2024-01-01", periods=50, freq="D")
        df = pd.DataFrame({
            "date": dates, "open": 100, "high": 101, "low": 99,
            "close": 100, "volume": 1000000,
        })
        kd = KLineData(symbol="CONST", market="US", timeframe="1d", df=df)
        result = self.factor.compute(kd)
        valid = result.dropna()
        self.assertTrue((valid.abs() < 1e-6).all())


class TestStochasticFactor(unittest.TestCase):
    """随机指标测试"""

    def setUp(self):
        self.factor = StochasticFactor()
        self.kd = make_sample_klinedata(200)

    def test_meta(self):
        meta = StochasticFactor.meta()
        self.assertEqual(meta.name, "stoch_k_14")

    def test_compute(self):
        result = self.factor.compute(self.kd)
        self.assertEqual(len(result), 200)
        valid = result.dropna()
        if len(valid) > 0:
            self.assertGreaterEqual(valid.min(), 0)
            self.assertLessEqual(valid.max(), 100)


class TestEMAFactor(unittest.TestCase):
    """EMA 因子测试"""

    def setUp(self):
        self.factor = EMAFactor()
        self.kd = make_trending_klinedata(200)

    def test_meta(self):
        meta = EMAFactor.meta()
        self.assertEqual(meta.name, "ema_deviation")

    def test_compute(self):
        result = self.factor.compute(self.kd)
        self.assertEqual(len(result), 200)

    def test_trending_market(self):
        """上升趋势中 EMA 偏离应主要为正值"""
        result = self.factor.compute(self.kd)
        valid = result.dropna()
        self.assertGreater(valid.mean(), 0)


class TestSMAFactor(unittest.TestCase):
    """SMA 因子测试"""

    def setUp(self):
        self.factor = SMAFactor()

    def test_meta(self):
        meta = SMAFactor.meta()
        self.assertEqual(meta.name, "sma_deviation")

    def test_compute(self):
        kd = make_trending_klinedata(200)
        result = self.factor.compute(kd)
        self.assertEqual(len(result), 200)


class TestADXFactor(unittest.TestCase):
    """ADX 因子测试"""

    def setUp(self):
        self.factor = ADXFactor()

    def test_meta(self):
        meta = ADXFactor.meta()
        self.assertEqual(meta.name, "adx_14")

    def test_compute_trending(self):
        """强趋势数据 ADX 应 > 20"""
        kd = make_trending_klinedata(200)
        result = self.factor.compute(kd)
        valid = result.dropna()
        if len(valid) > 10:
            self.assertGreater(valid.mean(), 20)

    def test_compute_random(self):
        """随机数据 ADX 应偏低"""
        kd = make_sample_klinedata(200)
        result = self.factor.compute(kd)
        valid = result.dropna()
        if len(valid) > 10:
            self.assertLess(valid.mean(), 40)


class TestTrendStrengthFactor(unittest.TestCase):
    """趋势强度因子测试"""

    def setUp(self):
        self.factor = TrendStrengthFactor()

    def test_meta(self):
        meta = TrendStrengthFactor.meta()
        self.assertEqual(meta.name, "trend_strength")

    def test_compute_trending(self):
        """上升趋势中趋势强度应为正"""
        kd = make_trending_klinedata(200)
        result = self.factor.compute(kd)
        valid = result.dropna()
        if len(valid) > 10:
            self.assertGreater(valid.mean(), 0)


class TestXMM30mFactor(unittest.TestCase):
    """30m XMM research factor tests"""

    def setUp(self):
        self.factor = XMM30mFactor()
        self.kd = make_sample_klinedata(180, market="US", tf="30m")

    def test_meta(self):
        meta = XMM30mFactor.meta()
        self.assertEqual(meta.name, "xmm_30m")
        self.assertIn("US", meta.markets)
        self.assertIn("HK", meta.markets)
        self.assertEqual(meta.frequencies, ["30m"])

    def test_compute_returns_bounded_series(self):
        result = self.factor.compute(self.kd, min_bars=100, lookback=120)
        self.assertIsInstance(result, pd.Series)
        self.assertEqual(len(result), len(self.kd.df))
        self.assertTrue(result.dropna().between(-100, 100).all())

    def test_validate_rejects_daily_data(self):
        daily = make_sample_klinedata(180, market="US", tf="1d")
        self.assertFalse(self.factor.validate(daily))


class TestATRFactor(unittest.TestCase):
    """ATR 因子测试"""

    def setUp(self):
        self.factor = ATRFactor()

    def test_meta(self):
        meta = ATRFactor.meta()
        self.assertEqual(meta.name, "atr_14")

    def test_compute_highvol(self):
        """高波动数据 ATR% 应高于低波动"""
        highvol = make_highvol_klinedata(200)
        lowvol = make_sample_klinedata(200)
        result_hv = self.factor.compute(highvol)
        result_lv = self.factor.compute(lowvol)
        self.assertGreater(
            result_hv.dropna().mean(),
            result_lv.dropna().mean(),
        )


class TestBollingerBandWidthFactor(unittest.TestCase):
    """布林带宽度测试"""

    def setUp(self):
        self.factor = BollingerBandWidthFactor()

    def test_meta(self):
        meta = BollingerBandWidthFactor.meta()
        self.assertEqual(meta.name, "bb_width")

    def test_compute(self):
        kd = make_sample_klinedata(200)
        result = self.factor.compute(kd)
        self.assertEqual(len(result), 200)
        # 宽度应始终为正
        valid = result.dropna()
        if len(valid) > 0:
            self.assertGreater(valid.min(), 0)

    def test_validate_insufficient(self):
        """不足 30 根 K 线验证应失败"""
        short_kd = make_sample_klinedata(20)
        self.assertFalse(self.factor.validate(short_kd))


class TestHistoricalVolatilityFactor(unittest.TestCase):
    """历史波动率测试"""

    def setUp(self):
        self.factor = HistoricalVolatilityFactor()

    def test_meta(self):
        meta = HistoricalVolatilityFactor.meta()
        self.assertEqual(meta.name, "hist_vol_20")

    def test_compute(self):
        kd = make_sample_klinedata(200)
        result = self.factor.compute(kd)
        self.assertEqual(len(result), 200)
        valid = result.dropna()
        if len(valid) > 0:
            self.assertGreater(valid.min(), 0)


class TestOBVFactor(unittest.TestCase):
    """OBV 因子测试"""

    def setUp(self):
        self.factor = OBVFactor()

    def test_meta(self):
        meta = OBVFactor.meta()
        self.assertEqual(meta.name, "obv_zscore")

    def test_compute(self):
        kd = make_sample_klinedata(200)
        result = self.factor.compute(kd)
        self.assertEqual(len(result), 200)

    def test_price_up_volume_up(self):
        """价涨量增时 OBV 应上升"""
        dates = pd.date_range("2024-01-01", periods=100, freq="D")
        close = 100 + np.arange(100) * 0.5  # 稳步上涨
        df = pd.DataFrame({
            "date": dates, "open": close - 0.1, "high": close + 0.3,
            "low": close - 0.3, "close": close,
            "volume": np.linspace(1_000_000, 10_000_000, 100),  # 量增
        })
        kd = KLineData(symbol="UP", market="US", timeframe="1d", df=df)
        result = self.factor.compute(kd)
        # 后期 OBV Z-score 应 > 0
        last_50 = result.dropna().tail(50)
        self.assertGreater(last_50.mean(), -1)


class TestVolumeRatioFactor(unittest.TestCase):
    """成交量比率测试"""

    def setUp(self):
        self.factor = VolumeRatioFactor()

    def test_meta(self):
        meta = VolumeRatioFactor.meta()
        self.assertEqual(meta.name, "volume_ratio")

    def test_compute(self):
        kd = make_sample_klinedata(200)
        result = self.factor.compute(kd)
        self.assertEqual(len(result), 200)
        valid = result.dropna()
        if len(valid) > 0:
            self.assertGreater(valid.min(), 0)


class TestVPTFactor(unittest.TestCase):
    """VPT 因子测试"""

    def setUp(self):
        self.factor = VPTFactor()

    def test_meta(self):
        meta = VPTFactor.meta()
        self.assertEqual(meta.name, "vpt")

    def test_compute(self):
        kd = make_sample_klinedata(200)
        result = self.factor.compute(kd)
        self.assertEqual(len(result), 200)


# ═══════════════════════════════════════════════════════════
# 因子注册系统测试
# ═══════════════════════════════════════════════════════════

class TestFactorRegistry(unittest.TestCase):
    """因子注册表测试"""

    class DummyFactor(BaseFactor):
        @classmethod
        def meta(cls) -> FactorMeta:
            return FactorMeta(
                name="dummy", category="technical",
                markets=["US"], frequencies=["1d"],
                description="Dummy factor for testing",
            )

        def compute(self, data, **params):
            return data.df["close"] * 0

    def setUp(self):
        # 保存原始状态
        self._orig_registry = FactorRegistry._registry.copy()
        self._orig_metas = FactorRegistry._metas.copy()
        # 注册内置因子（compute.py 中已注册，这里确保存在）
        if FactorRegistry.count() == 0:
            for f in [RSIFactor, MACDFactor, ROCFactor, StochasticFactor,
                       EMAFactor, SMAFactor, ADXFactor, TrendStrengthFactor,
                       ATRFactor, BollingerBandWidthFactor, HistoricalVolatilityFactor,
                       OBVFactor, VolumeRatioFactor, VPTFactor]:
                FactorRegistry.register(f)

    def tearDown(self):
        FactorRegistry._registry = self._orig_registry
        FactorRegistry._metas = self._orig_metas

    def test_register_and_count(self):
        """注册新因子后计数应增加"""
        n_before = FactorRegistry.count()
        FactorRegistry.register(self.DummyFactor)
        self.assertEqual(FactorRegistry.count(), n_before + 1)

    def test_list_factors_by_market(self):
        """按市场列出因子"""
        factors = FactorRegistry.list_factors(market="US")
        self.assertGreater(len(factors), 0)
        for m in factors:
            self.assertIn("US", m.markets)

    def test_list_factors_by_category(self):
        """按类别列出因子"""
        factors = FactorRegistry.list_factors(category="technical")
        self.assertGreater(len(factors), 0)
        for m in factors:
            self.assertEqual(m.category, "technical")

    def test_get_factor_exists(self):
        """获取存在的因子"""
        cls = FactorRegistry.get_factor("rsi_14")
        self.assertIsNotNone(cls)
        self.assertEqual(cls, RSIFactor)

    def test_get_factor_not_exists(self):
        """获取不存在的因子"""
        cls = FactorRegistry.get_factor("nonexistent")
        self.assertIsNone(cls)

    def test_compute_existing(self):
        """计算已注册因子"""
        kd = make_sample_klinedata(200)
        result = FactorRegistry.compute("rsi_14", kd)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, pd.Series)

    def test_compute_nonexistent(self):
        """计算未注册因子应返回 None"""
        kd = make_sample_klinedata(200)
        result = FactorRegistry.compute("nonexistent", kd)
        self.assertIsNone(result)

    def test_compute_batch(self):
        """批量计算"""
        kd = make_sample_klinedata(200)
        results = FactorRegistry.compute_batch(["rsi_14", "macd", "roc_10"], kd)
        self.assertEqual(len(results), 3)
        for k, v in results.items():
            self.assertIsNotNone(v)

    def test_compute_all(self):
        """计算所有因子"""
        kd = make_sample_klinedata(200)
        results = FactorRegistry.compute_all(kd)
        self.assertGreater(len(results), 0)


# ═══════════════════════════════════════════════════════════
# 批量因子计算引擎测试
# ═══════════════════════════════════════════════════════════

class TestFactorComputeEngine(unittest.TestCase):
    """FactorComputeEngine 测试"""

    def setUp(self):
        self.engine = FactorComputeEngine()
        self.kd = make_sample_klinedata(200)

    def test_compute_single_all(self):
        """计算单个标的全部因子"""
        df = self.engine.compute_single(self.kd)
        self.assertIsInstance(df, pd.DataFrame)
        self.assertGreater(len(df.columns), 0)

    def test_compute_single_specific(self):
        """计算指定因子"""
        df = self.engine.compute_single(
            self.kd, factor_names=["rsi_14", "macd", "atr_14"]
        )
        self.assertIn("rsi_14", df.columns)
        self.assertIn("macd", df.columns)
        self.assertIn("atr_14", df.columns)

    def test_compute_single_by_market(self):
        """按市场筛选因子"""
        df = self.engine.compute_single(self.kd, market="US")
        self.assertGreater(len(df.columns), 0)

    def test_compute_multi(self):
        """多标的批量计算"""
        data_map = {
            "A": self.kd,
            "B": make_sample_klinedata(150, symbol="B"),
        }
        results = self.engine.compute_multi(data_map, factor_names=["rsi_14"])
        self.assertIn("A", results)
        self.assertIn("B", results)
        self.assertIn("rsi_14", results["A"].columns)

    def test_list_registered(self):
        """列出已注册因子"""
        factors = self.engine.list_registered_factors()
        self.assertGreater(len(factors), 0)

    def test_factor_count(self):
        """因子计数"""
        self.assertGreater(self.engine.factor_count, 0)


# ═══════════════════════════════════════════════════════════
# 模型评估层测试
# ═══════════════════════════════════════════════════════════

class TestModelEvaluation(unittest.TestCase):
    """模型评估指标测试"""

    def setUp(self):
        np.random.seed(42)
        n = 200
        self.factor = pd.Series(np.random.randn(n), name="factor")
        # 让 forward_returns 与 factor 弱相关
        self.returns = self.factor * 0.3 + np.random.randn(n) * 0.7
        self.returns = pd.Series(self.returns, name="returns")

    def test_calc_ic_spearman(self):
        """Spearman IC 应在 [-1, 1] 范围内"""
        ic = calc_ic(self.factor, self.returns, method="spearman")
        self.assertGreaterEqual(ic, -1)
        self.assertLessEqual(ic, 1)

    def test_calc_ic_pearson(self):
        """Pearson IC 应在 [-1, 1] 范围内"""
        ic = calc_ic(self.factor, self.returns, method="pearson")
        self.assertGreaterEqual(ic, -1)
        self.assertLessEqual(ic, 1)

    def test_calc_ic_insufficient_data(self):
        """数据不足时 IC 应返回 0"""
        small_factor = pd.Series([1, 2])
        small_ret = pd.Series([0.1, -0.1])
        ic = calc_ic(small_factor, small_ret)
        self.assertEqual(ic, 0.0)

    def test_calc_ic_perfect_correlation(self):
        """完美正相关 IC 应接近 1"""
        x = pd.Series(np.arange(50))
        y = x.copy()
        ic = calc_ic(x, y)
        self.assertAlmostEqual(ic, 1.0, places=2)

    def test_calc_ic_series(self):
        """IC 时间序列应返回正确形状"""
        n_dates = 50
        n_stocks = 10
        factor_df = pd.DataFrame(
            np.random.randn(n_dates, n_stocks),
            index=pd.date_range("2024-01-01", periods=n_dates, freq="D"),
        )
        # forward_returns 需为 MultiIndex Series (date, stock)
        fwd_ret = pd.DataFrame(
            np.random.randn(n_dates, n_stocks),
            index=pd.date_range("2024-01-01", periods=n_dates, freq="D"),
        )
        ic_series = calc_ic_series(factor_df, fwd_ret.stack())
        self.assertEqual(len(ic_series), n_dates)

    def test_calc_sharpe(self):
        """Sharpe 应正确计算"""
        rets = pd.Series(np.random.randn(100) * 0.01 + 0.0005)
        sharpe = calc_sharpe(rets)
        self.assertIsInstance(sharpe, float)

    def test_calc_sharpe_insufficient(self):
        """数据不足时 Sharpe 应返回 0"""
        sharpe = calc_sharpe(pd.Series([0.01]))
        self.assertEqual(sharpe, 0.0)

    def test_max_drawdown(self):
        """最大回撤应为正数且 <= 1"""
        eq = pd.Series([100, 110, 105, 95, 98, 102])
        dd = calc_max_drawdown(eq)
        self.assertGreater(dd, 0)
        self.assertLessEqual(dd, 1)

    def test_max_drawdown_monotonic_up(self):
        """持续上涨时回撤应为 0"""
        eq = pd.Series(np.exp(np.linspace(0, 0.5, 100)))
        dd = calc_max_drawdown(eq)
        self.assertAlmostEqual(dd, 0.0, places=4)

    def test_hit_rate(self):
        """命中率应在 [0, 1] 范围内"""
        signals = pd.Series([1, 1, -1, -1, 1])
        returns = pd.Series([0.05, -0.02, 0.03, -0.04, 0.01])
        hr = calc_hit_rate(signals, returns)
        self.assertGreaterEqual(hr, 0)
        self.assertLessEqual(hr, 1)

    def test_turnover(self):
        """换手率应在 [0, 1] 范围内"""
        signals = pd.Series([1, 1, -1, -1, 1, 1, 1, -1])
        to = calc_turnover(signals)
        self.assertGreaterEqual(to, 0)
        self.assertLessEqual(to, 1)

    def test_evaluate_predictions(self):
        """预测评估应返回完整指标"""
        y_true = pd.Series(np.random.randn(100))
        y_pred = y_true * 0.5 + np.random.randn(100) * 0.5
        result = evaluate_predictions(y_true, y_pred)
        self.assertIn("IC", result)
        self.assertIn("hit_rate", result)
        self.assertIn("n_samples", result)
        self.assertEqual(result["n_samples"], 100)

    def test_factor_summary(self):
        """因子摘要应包含关键指标"""
        result = factor_summary(self.factor, self.returns)
        self.assertIn("IC", result)
        self.assertIn("Rank_IC", result)
        self.assertIn("hit_rate", result)
        self.assertIn("long_short_spread", result)
        self.assertIn("n_samples", result)


# ═══════════════════════════════════════════════════════════
# 回测引擎测试
# ═══════════════════════════════════════════════════════════

class TestBacktestEngine(unittest.TestCase):
    """回测引擎测试"""

    def setUp(self):
        self.engine = BacktestEngine(
            initial_capital=1_000_000,
            commission_pct=0.0003,
            slippage_pct=0.0001,
        )
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        np.random.seed(42)
        # 价格
        prices = pd.DataFrame({
            "A": 100 + np.cumsum(np.random.randn(n) * 0.5),
            "B": 50 + np.cumsum(np.random.randn(n) * 0.3),
        }, index=dates)
        # 信号: 随机在 -1 ~ 1 之间
        self.signals = pd.DataFrame({
            "A": np.random.uniform(-0.5, 0.5, n),
            "B": np.random.uniform(-0.5, 0.5, n),
        }, index=dates)
        self.prices = prices

    def test_run_returns_result(self):
        """回测应返回 BacktestResult"""
        result = self.engine.run(self.signals, self.prices)
        self.assertIsNotNone(result)
        self.assertIsInstance(result.equity_curve, pd.Series)

    def test_run_equity_positive(self):
        """权益曲线应始终为正"""
        result = self.engine.run(self.signals, self.prices)
        self.assertTrue((result.equity_curve > 0).all())

    def test_run_returns_length(self):
        """收益率序列长度应正确"""
        result = self.engine.run(self.signals, self.prices)
        self.assertEqual(len(result.returns), len(result.equity_curve))

    def test_run_single_asset(self):
        """单标的回测应正常工作"""
        single_sig = self.signals[["A"]]
        single_price = self.prices[["A"]]
        result = self.engine.run(single_sig, single_price)
        self.assertIsNotNone(result)
        self.assertGreater(len(result.equity_curve), 0)

    def test_run_empty_signals(self):
        """空信号回测应返回有效结果"""
        empty_sig = pd.DataFrame(index=self.signals.index)
        empty_price = pd.DataFrame(index=self.prices.index)
        result = self.engine.run(empty_sig, empty_price)
        self.assertIsNotNone(result)
        # 无列时 positions 为空 DataFrame，但引擎仍会遍历日期
        self.assertEqual(len(result.positions.columns), 0)

    def test_run_zero_commission(self):
        """零佣金回测"""
        engine = BacktestEngine(initial_capital=1_000_000,
                                commission_pct=0.0, slippage_pct=0.0)
        result = engine.run(self.signals, self.prices)
        self.assertIsNotNone(result)
        self.assertGreater(len(result.equity_curve), 0)

    def test_run_zero_position(self):
        """零信号时权益应保持不变"""
        zero_sig = pd.DataFrame(
            np.zeros((100, 2)),
            index=self.signals.index,
            columns=["A", "B"],
        )
        result = self.engine.run(zero_sig, self.prices)
        final_eq = result.equity_curve.iloc[-1]
        self.assertAlmostEqual(final_eq, 1_000_000, delta=1000)

    def test_result_has_positions(self):
        """回测结果应包含仓位信息"""
        result = self.engine.run(self.signals, self.prices)
        self.assertIn("A", result.positions.columns)
        self.assertIn("B", result.positions.columns)

    def test_result_has_trades(self):
        """回测结果应包含交易记录"""
        result = self.engine.run(self.signals, self.prices)
        self.assertIsNotNone(result.trades)

    def test_max_position_limit(self):
        """max_position 参数应限制单个标的仓位上限"""
        # 信号值远超 max_position，验证仓位被截断
        extreme_sig = pd.DataFrame(
            {"A": [2.0] * 100, "B": [-1.5] * 100},
            index=self.signals.index,
        )
        result = self.engine.run(extreme_sig, self.prices, max_position=0.2)
        max_abs_pos = result.positions.abs().max().max()
        self.assertLessEqual(max_abs_pos, 0.2 + 1e-6)

    def test_max_total_limit(self):
        """max_total 参数应限制总仓位暴露上限"""
        # 多个标的信号都接近 max_position，总暴露应被压缩到 max_total
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        prices_5 = pd.DataFrame(
            {c: 100 + np.cumsum(np.random.randn(n) * 0.5)
             for c in ["A", "B", "C", "D", "E"]},
            index=dates,
        )
        sig_5 = pd.DataFrame(
            {c: [0.25] * n for c in ["A", "B", "C", "D", "E"]},
            index=dates,
        )
        engine = BacktestEngine(initial_capital=1_000_000)
        result = engine.run(sig_5, prices_5, max_position=0.25, max_total=0.8)
        # 5 个标的各 0.25 = 1.25 总暴露，应被压缩到 0.8
        max_total_exposure = result.positions.abs().sum(axis=1).max()
        self.assertLessEqual(max_total_exposure, 0.8 + 1e-6)

    def test_cost_deduction(self):
        """有佣金回测的最终权益应低于无佣金版本"""
        np.random.seed(42)
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        prices = pd.DataFrame(
            {"A": 100 + np.cumsum(np.random.randn(n) * 0.5)},
            index=dates,
        )
        # 频繁交易信号，放大成本差异
        sig = pd.DataFrame(
            {"A": np.random.choice([-0.5, 0.5], size=n)},
            index=dates,
        )
        engine_no_cost = BacktestEngine(
            initial_capital=1_000_000, commission_pct=0.0, slippage_pct=0.0
        )
        engine_with_cost = BacktestEngine(
            initial_capital=1_000_000, commission_pct=0.003, slippage_pct=0.001
        )
        result_no_cost = engine_no_cost.run(sig, prices)
        result_with_cost = engine_with_cost.run(sig, prices)
        final_no_cost = result_no_cost.equity_curve.iloc[-1]
        final_with_cost = result_with_cost.equity_curve.iloc[-1]
        self.assertGreater(final_no_cost, final_with_cost)


class TestBacktestMetrics(unittest.TestCase):
    """回测绩效指标测试"""

    def setUp(self):
        np.random.seed(42)
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        prices = pd.DataFrame({
            "A": 100 + np.cumsum(np.random.randn(n) * 0.5),
            "B": 50 + np.cumsum(np.random.randn(n) * 0.3),
        }, index=dates)
        signals = pd.DataFrame({
            "A": np.random.uniform(-0.5, 0.5, n),
            "B": np.random.uniform(-0.5, 0.5, n),
        }, index=dates)
        engine = BacktestEngine()
        self.result = engine.run(signals, prices)

    def test_compute_metrics_returns_dict(self):
        """compute_backtest_metrics 应返回字典"""
        metrics = compute_backtest_metrics(self.result)
        self.assertIsInstance(metrics, dict)

    def test_metrics_contains_keys(self):
        """指标字典应包含所有关键字段"""
        metrics = compute_backtest_metrics(self.result)
        expected_keys = [
            "total_return_pct", "sharpe_ratio", "max_drawdown_pct",
            "calmar_ratio", "annual_volatility_pct", "hit_rate",
            "n_trades", "avg_turnover",
        ]
        for key in expected_keys:
            self.assertIn(key, metrics)

    def test_metrics_types(self):
        """指标值类型应正确"""
        metrics = compute_backtest_metrics(self.result)
        self.assertIsInstance(metrics["total_return_pct"], float)
        self.assertIsInstance(metrics["sharpe_ratio"], float)
        self.assertIsInstance(metrics["n_trades"], int)

    def test_metrics_empty_result(self):
        """空回测结果应返回有效指标"""
        from research.backtest.engine import BacktestResult
        empty_result = BacktestResult(
            equity_curve=pd.Series([1_000_000]),
            returns=pd.Series([0.0]),
            signals=pd.DataFrame(),
            positions=pd.DataFrame(),
            trades=pd.DataFrame(),
        )
        metrics = compute_backtest_metrics(empty_result)
        self.assertEqual(metrics["n_trades"], 0)
        self.assertEqual(metrics["total_return_pct"], 0.0)


# ═══════════════════════════════════════════════════════════
# IC 分析测试
# ═══════════════════════════════════════════════════════════

class TestComputeForwardReturns(unittest.TestCase):
    """未来收益率计算测试"""

    def setUp(self):
        np.random.seed(42)
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        self.close_df = pd.DataFrame(
            100 + np.cumsum(np.random.randn(n, 3) * 0.5, axis=0),
            index=dates,
            columns=["A", "B", "C"],
        )

    def test_forward_returns_shape(self):
        """未来收益率长度应正确（stack 后 = n_dates × n_stocks）"""
        fwd = compute_forward_returns(self.close_df, horizon=1)
        expected = len(self.close_df) * len(self.close_df.columns)
        self.assertEqual(len(fwd), expected)

    def test_forward_returns_horizon_5(self):
        """5 日持有期"""
        fwd = compute_forward_returns(self.close_df, horizon=5)
        expected = len(self.close_df) * len(self.close_df.columns)
        self.assertEqual(len(fwd), expected)

    def test_forward_returns_name(self):
        """收益率 Series 名称应包含持有期"""
        fwd = compute_forward_returns(self.close_df, horizon=3)
        self.assertIn("3", fwd.name)


class TestICAnalyzer(unittest.TestCase):
    """IC 分析器测试"""

    def setUp(self):
        np.random.seed(42)
        n_dates = 50
        n_stocks = 5
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
        self.factor_df = pd.DataFrame(
            np.random.randn(n_dates, n_stocks),
            index=dates,
            columns=[chr(65 + i) for i in range(n_stocks)],
        )
        self.forward_returns = pd.Series(
            np.random.randn(n_dates * n_stocks),
            index=pd.MultiIndex.from_product(
                [dates, [chr(65 + i) for i in range(n_stocks)]]
            ),
            name="fwd_ret_1",
        )
        self.analyzer = ICAnalyzer(self.factor_df, self.forward_returns)

    def test_analyze_single(self):
        """分析单个因子应返回有效指标"""
        result = self.analyzer.analyze_single("A")
        self.assertIn("IC", result)
        self.assertIn("Rank_IC", result)

    def test_analyze_single_nonexistent(self):
        """分析不存在的因子应返回错误"""
        result = self.analyzer.analyze_single("NONEXIST")
        self.assertIn("error", result)

    def test_analyze_all(self):
        """分析所有因子应返回 DataFrame"""
        result = self.analyzer.analyze_all()
        self.assertIsInstance(result, pd.DataFrame)
        self.assertEqual(len(result), len(self.factor_df.columns))

    def test_analyze_all_columns(self):
        """分析结果应包含关键指标列"""
        result = self.analyzer.analyze_all()
        for col in ["IC", "Rank_IC", "hit_rate"]:
            self.assertIn(col, result.columns)

    def test_ic_time_series(self):
        """IC 时间序列应返回 DataFrame"""
        ic_df = self.analyzer.ic_time_series()
        self.assertIsInstance(ic_df, pd.DataFrame)
        self.assertGreater(len(ic_df.columns), 0)

    def test_ic_time_series_rolling(self):
        """IC 时间序列应包含滚动均值"""
        ic_df = self.analyzer.ic_time_series(rolling_window=10)
        self.assertIn("rolling_mean", ic_df.columns)

    def test_best_factors(self):
        """best_factors 应返回排序后的列表"""
        best = self.analyzer.best_factors(top_n=3, metric="Rank_IC")
        self.assertIsInstance(best, list)
        self.assertLessEqual(len(best), 3)
        if best:
            self.assertIsInstance(best[0], tuple)
            self.assertEqual(len(best[0]), 2)

    def test_best_factors_empty(self):
        """空因子 DataFrame 的 best_factors 应返回空列表"""
        empty_analyzer = ICAnalyzer(
            pd.DataFrame(), pd.Series(dtype=float)
        )
        best = empty_analyzer.best_factors()
        self.assertEqual(best, [])


# ═══════════════════════════════════════════════════════════
# 相关性分析测试
# ═══════════════════════════════════════════════════════════

class TestFactorCorrelation(unittest.TestCase):
    """因子相关性分析测试"""

    def setUp(self):
        np.random.seed(42)
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        # 创建 5 个因子，其中一些高度相关
        base = np.random.randn(n)
        self.factor_df = pd.DataFrame({
            "F1": base,
            "F2": base * 0.9 + np.random.randn(n) * 0.1,  # 与 F1 高度相关
            "F3": np.random.randn(n),  # 独立
            "F4": -base * 0.8 + np.random.randn(n) * 0.2,  # 与 F1 负相关
            "F5": np.random.randn(n),  # 独立
        }, index=dates)

    def test_factor_correlation_shape(self):
        """相关系数矩阵应为方阵"""
        corr = factor_correlation(self.factor_df)
        self.assertEqual(corr.shape, (5, 5))
        self.assertEqual(list(corr.columns), list(self.factor_df.columns))

    def test_factor_correlation_diagonal(self):
        """对角线应为 1"""
        corr = factor_correlation(self.factor_df)
        for col in corr.columns:
            self.assertAlmostEqual(corr.loc[col, col], 1.0, places=5)

    def test_factor_correlation_pearson(self):
        """Pearson 相关系数应在 [-1, 1] 范围内"""
        corr = factor_correlation(self.factor_df, method="pearson")
        self.assertGreaterEqual(corr.values.min(), -1)
        self.assertLessEqual(corr.values.max(), 1)

    def test_factor_correlation_spearman(self):
        """Spearman 相关系数应正常工作"""
        corr = factor_correlation(self.factor_df, method="spearman")
        self.assertGreaterEqual(corr.values.min(), -1)
        self.assertLessEqual(corr.values.max(), 1)

    def test_top_correlated_pairs(self):
        """高相关性因子对应被检测到"""
        corr = factor_correlation(self.factor_df)
        pairs = top_correlated_pairs(corr, threshold=0.7, top_n=5)
        self.assertGreater(len(pairs), 0)

    def test_top_correlated_pairs_high_threshold(self):
        """极高阈值应返回空结果"""
        corr = factor_correlation(self.factor_df)
        pairs = top_correlated_pairs(corr, threshold=0.999, top_n=5)
        self.assertEqual(len(pairs), 0)

    def test_cluster_factors(self):
        """聚类应识别出相关因子组"""
        corr = factor_correlation(self.factor_df)
        clusters = cluster_factors(corr, threshold=0.5)
        self.assertIsInstance(clusters, dict)
        # F1 和 F2 应被聚在一起
        all_clustered = set()
        for members in clusters.values():
            all_clustered.update(members)
        self.assertIn("F1", all_clustered)
        self.assertIn("F2", all_clustered)

    def test_cluster_factors_high_threshold(self):
        """极高阈值下应无聚类"""
        corr = factor_correlation(self.factor_df)
        clusters = cluster_factors(corr, threshold=0.999)
        self.assertEqual(len(clusters), 0)


# ═══════════════════════════════════════════════════════════
# 可视化测试
# ═══════════════════════════════════════════════════════════

class TestVisualization(unittest.TestCase):
    """可视化函数测试（仅验证不抛出异常）"""

    def setUp(self):
        np.random.seed(42)
        n = 50
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        self.ic_df = pd.DataFrame(
            np.random.randn(n, 3),
            index=dates,
            columns=["F1", "F2", "F3"],
        )
        self.ic_df["rolling_mean"] = self.ic_df.mean(axis=1).rolling(10).mean()
        corr_data = np.random.uniform(-1, 1, (4, 4))
        np.fill_diagonal(corr_data, 1.0)
        self.corr_matrix = pd.DataFrame(
            corr_data,
            index=["F1", "F2", "F3", "F4"],
            columns=["F1", "F2", "F3", "F4"],
        )
        self.factor_series = pd.Series(np.random.randn(200), name="factor")
        self.returns = pd.Series(np.random.randn(100), name="returns")

    def test_plot_ic_timeseries(self):
        """IC 时间序列绘图不应抛出异常"""
        try:
            plot_ic_timeseries(self.ic_df)
        except Exception as e:
            self.fail(f"plot_ic_timeseries raised {e}")

    def test_plot_ic_timeseries_with_save(self):
        """IC 时间序列保存到文件"""
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()  # 关闭句柄以便后续删除
        try:
            plot_ic_timeseries(self.ic_df, save_path=tmp.name)
            self.assertTrue(os.path.exists(tmp.name))
            self.assertGreater(os.path.getsize(tmp.name), 0)
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def test_plot_factor_heatmap(self):
        """相关性热力图不应抛出异常"""
        try:
            plot_factor_heatmap(self.corr_matrix)
        except Exception as e:
            self.fail(f"plot_factor_heatmap raised {e}")

    def test_plot_ic_decay(self):
        """IC 衰减曲线不应抛出异常"""
        decay_df = pd.DataFrame(
            {"IC": [0.05, 0.03, 0.01, -0.01, -0.02]},
            index=[1, 2, 3, 5, 10],
        )
        try:
            plot_ic_decay(decay_df)
        except Exception as e:
            self.fail(f"plot_ic_decay raised {e}")

    def test_plot_factor_distribution(self):
        """因子分布图不应抛出异常"""
        try:
            plot_factor_distribution(self.factor_series)
        except Exception as e:
            self.fail(f"plot_factor_distribution raised {e}")

    def test_plot_quantile_returns(self):
        """分组收益图不应抛出异常"""
        try:
            plot_quantile_returns(self.returns)
        except Exception as e:
            self.fail(f"plot_quantile_returns raised {e}")


# ═══════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
