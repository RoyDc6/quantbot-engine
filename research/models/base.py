"""
research/models/base.py — 模型抽象基类

定义 BaseModel 接口，所有预测模型实现此接口。
与因子计算引擎配合：因子 DataFrame → 模型 → 预测信号。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd
import numpy as np


@dataclass
class ModelMeta:
    """模型元数据"""
    name: str
    model_type: str             # "xgboost" | "ensemble" | "linear" | "nn"
    markets: List[str]          # 适用市场
    features: List[str]         # 使用的因子名列表
    description: str = ""
    version: str = "1.0.0"


@dataclass
class PredictionResult:
    """预测结果"""
    signals: pd.Series          # 预测信号（-1 ~ 1）
    confidence: Optional[pd.Series] = None  # 置信度
    metadata: dict = field(default_factory=dict)


class BaseModel(ABC):
    """模型抽象基类"""

    @abstractmethod
    def meta(self) -> ModelMeta:
        ...

    @abstractmethod
    def train(self, X: pd.DataFrame, y: pd.Series,
              **params) -> "BaseModel":
        """训练模型

        Args:
            X: 因子 DataFrame
            y: 目标变量（如未来收益率）
            params: 训练参数

        Returns:
            self (已训练)
        """
        ...

    @abstractmethod
    def predict(self, X: pd.DataFrame) -> PredictionResult:
        """预测

        Args:
            X: 因子 DataFrame

        Returns:
            PredictionResult
        """
        ...

    def evaluate(self, X: pd.DataFrame, y: pd.Series) -> Dict[str, float]:
        """评估模型性能

        Args:
            X: 因子 DataFrame
            y: 真实目标值

        Returns:
            指标字典
        """
        from .evaluation import evaluate_predictions
        pred = self.predict(X)
        return evaluate_predictions(y, pred.signals)