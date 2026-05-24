"""
research/factors/alternative/kronos.py — Kronos 基础模型预测因子

使用 Kronos (AAAI 2026) 金融 K 线基础模型预测未来收益。
通过 walk-forward 方式为每个 bar 生成预测信号。

设计要点:
- 延迟加载模型（首次 compute() 时从 HuggingFace 加载）
- stride 参数控制计算间隔（平衡速度与精度）
- 未计算的 bar 通过线性插值填充

参考: https://github.com/shiyu-coder/Kronos
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from ..base import BaseFactor
from ..registry import FactorMeta
from ...data.base import KLineData


# ============================================================
# Kronos 模型路径注入
# ============================================================

_KRONOS_DIR = Path(__file__).resolve().parent.parent.parent / "kronos"
if str(_KRONOS_DIR) not in sys.path:
    sys.path.insert(0, str(_KRONOS_DIR))


# ============================================================
# 全局模型缓存（避免重复加载）
# ============================================================

_KRONOS_MODEL_CACHE: dict = {}


def _load_kronos(model_name: str = "NeoQuasar/Kronos-small",
                 device: str = "cpu") -> Tuple:
    """加载 Kronos 模型 + Tokenizer（带缓存）

    Kronos 的 Tokenizer 和 Model 存储在独立的 HuggingFace 仓库中:
    - Tokenizer: NeoQuasar/Kronos-Tokenizer-base (或 -2k for mini)
    - Model: NeoQuasar/Kronos-small (或 -mini / -base)

    Args:
        model_name: HuggingFace 模型名（用于 Model 主体）
        device: 计算设备

    Returns:
        (model, tokenizer, predictor) 元组
    """
    tokenizer_repo = _get_tokenizer_repo(model_name)
    cache_key = f"{model_name}:{device}"
    if cache_key in _KRONOS_MODEL_CACHE:
        return _KRONOS_MODEL_CACHE[cache_key]

    try:
        from model.kronos import Kronos, KronosTokenizer, KronosPredictor
    except ImportError as e:
        raise ImportError(
            f"无法导入 Kronos 模块。请确保 kronos/ 目录完整: {e}"
        )

    print(f"[KronosFactor] 加载 Tokenizer ({tokenizer_repo}) + Model ({model_name})...")
    tokenizer = KronosTokenizer.from_pretrained(tokenizer_repo)
    model = Kronos.from_pretrained(model_name)
    model.eval()
    tokenizer.eval()

    # 如果设备不是 cpu，移到对应设备
    if device != "cpu":
        tokenizer = tokenizer.to(device)
        model = model.to(device)

    predictor = KronosPredictor(model, tokenizer, device=device)
    _KRONOS_MODEL_CACHE[cache_key] = (model, tokenizer, predictor)
    print(f"[KronosFactor] 模型加载完成")
    return model, tokenizer, predictor


def _get_tokenizer_repo(model_name: str) -> str:
    """根据模型名推导对应的 Tokenizer HuggingFace 仓库名"""
    mapping = {
        "NeoQuasar/Kronos-mini": "NeoQuasar/Kronos-Tokenizer-2k",
        "NeoQuasar/Kronos-small": "NeoQuasar/Kronos-Tokenizer-base",
        "NeoQuasar/Kronos-base": "NeoQuasar/Kronos-Tokenizer-base",
    }
    return mapping.get(model_name, "NeoQuasar/Kronos-Tokenizer-base")


def _generate_future_timestamps(last_ts: pd.Timestamp,
                                pred_len: int) -> pd.DatetimeIndex:
    """生成未来时间戳（基于最后一个已知时间戳）

    Args:
        last_ts: 最后一个已知时间戳
        pred_len: 预测步数

    Returns:
        未来时间戳 DatetimeIndex
    """
    # 对于日线数据，使用日历日（假设 7 天连续交易）
    # 对于更高频数据，按频率推断
    freq = None
    if hasattr(last_ts, 'freq') and last_ts.freq is not None:
        freq = last_ts.freq
    else:
        # 默认推断：如果时间是 9:30-16:00 之间，可能是交易时间数据
        # 简单处理：使用日历日
        freq = 'D'

    future_dates = pd.date_range(
        start=last_ts + pd.Timedelta(days=1),
        periods=pred_len,
        freq=freq
    )
    return future_dates


# ============================================================
# KronosFactor
# ============================================================

class KronosFactor(BaseFactor):
    """Kronos 基础模型预测因子

    使用 Kronos 预测模型生成未来收益预测信号。
    通过 walk-forward 方式在每个 bar 上计算预测收益。

    因子值含义:
    - 正值: 模型预测未来上涨（买入信号）
    - 负值: 模型预测未来下跌（卖出信号）
    - 绝对值越大，置信度越高
    """

    # 类级模型缓存，避免跨实例重复加载
    _model = None
    _tokenizer = None
    _predictor = None

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="kronos_predicted_return",
            category="alternative",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "5m", "15m"],
            description="Kronos foundation model predicted forward return "
                        "(walk-forward, stride-controlled)",
            default_params={
                "model_name": "NeoQuasar/Kronos-small",
                "tokenizer_repo": "NeoQuasar/Kronos-Tokenizer-base",
                "pred_len": 5,          # 预测步数
                "context_len": 60,        # 上下文长度（用于预测的 K 线数量）
                "stride": 5,              # 计算间隔（每 N 个 bar 计算一次）
                "T": 1.0,                 # 采样温度
                "top_k": 0,               # top-k 过滤
                "top_p": 0.9,             # nucleus 采样阈值
                "sample_count": 3,        # 并行采样数（自动平均）
                "device": "cpu",          # 计算设备
            },
            version="1.0.0",
        )

    def _ensure_model(self, model_name: str, device: str):
        """确保模型已加载"""
        if self._predictor is not None:
            return
        model, tokenizer, predictor = _load_kronos(model_name, device)
        KronosFactor._model = model
        KronosFactor._tokenizer = tokenizer
        KronosFactor._predictor = predictor

    def _predict_single(self, df_slice: pd.DataFrame,
                        pred_len: int,
                        T: float, top_k: int, top_p: float,
                        sample_count: int) -> Optional[float]:
        """对单个窗口进行预测

        Args:
            df_slice: 历史数据窗口
            pred_len: 预测步数
            T, top_k, top_p, sample_count: 预测参数

        Returns:
            预测收益（未来 close 相对于当前 close 的涨跌幅），或 None（失败时）
        """
        try:
            # 确保有 amount 列
            df = df_slice.copy()
            if 'amount' not in df.columns:
                df['amount'] = df['volume'] * df[['open', 'high', 'low', 'close']].mean(axis=1)

            # 时间戳
            x_timestamp = df.index
            last_ts = x_timestamp[-1]
            y_timestamp = _generate_future_timestamps(last_ts, pred_len)

            # 预测
            pred_df = self._predictor.predict(
                df, x_timestamp, y_timestamp, pred_len,
                T=T, top_k=top_k, top_p=top_p,
                sample_count=sample_count, verbose=False
            )

            # 提取预测收益
            predicted_close = pred_df['close'].iloc[-1]
            current_close = df['close'].iloc[-1]
            predicted_return = predicted_close / current_close - 1
            return float(predicted_return)

        except Exception as e:
            print(f"[KronosFactor] 预测失败: {e}")
            return None

    def compute(self, data: KLineData, **params) -> pd.Series:
        """计算 Kronos 预测因子

        Args:
            data: K 线数据
            params:
                model_name: 模型名（默认 NeoQuasar/Kronos-small）
                pred_len: 预测步数（默认 5）
                context_len: 上下文长度（默认 60）
                stride: 计算间隔（默认 5，每 5 个 bar 计算一次）
                T: 采样温度（默认 1.0）
                top_k: top-k 过滤（默认 0）
                top_p: nucleus 采样（默认 0.9）
                sample_count: 并行采样数（默认 3）
                device: 设备（默认 cpu）

        Returns:
            与 data.df 等长的 pd.Series，值为预测收益
        """
        model_name = params.get("model_name", "NeoQuasar/Kronos-small")
        pred_len = params.get("pred_len", 5)
        context_len = params.get("context_len", 60)
        stride = params.get("stride", 5)
        T = params.get("T", 1.0)
        top_k = params.get("top_k", 0)
        top_p = params.get("top_p", 0.9)
        sample_count = params.get("sample_count", 3)
        device = params.get("device", "cpu")

        df = data.df
        n = len(df)

        # 初始化结果序列
        result = pd.Series(
            index=df.index, dtype=float,
            name="kronos_predicted_return"
        )

        # 数据太少无法预测
        if n < context_len + 2:
            print(f"[KronosFactor] 数据不足 ({n} bars, 需要至少 {context_len + 2})")
            return result

        # 确保 index 是 DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            if 'date' in df.columns:
                df = df.set_index('date')
                result.index = df.index
            else:
                print("[KronosFactor] 数据缺少 DatetimeIndex，无法生成时间戳")
                return result

        # 确保模型已加载
        self._ensure_model(model_name, device)

        # Walk-forward 预测
        computed_count = 0
        for i in range(context_len, n, stride):
            window = df.iloc[:i + 1]
            ret = self._predict_single(
                window, pred_len, T, top_k, top_p, sample_count
            )
            if ret is not None:
                result.iloc[i] = ret
                computed_count += 1

        # 如果还有最后一个 bar 没被 stride 覆盖到，额外计算一次
        last_idx = n - 1
        if last_idx >= context_len and (last_idx - context_len) % stride != 0:
            window = df.iloc[:last_idx + 1]
            ret = self._predict_single(
                window, pred_len, T, top_k, top_p, sample_count
            )
            if ret is not None:
                result.iloc[last_idx] = ret
                computed_count += 1

        # 插值填充未计算的 bar
        if stride > 1 and computed_count > 1:
            result = result.interpolate(method='linear')

        print(f"[KronosFactor] 完成: {computed_count}/{n} bars 计算, "
              f"{stride} stride")

        return result