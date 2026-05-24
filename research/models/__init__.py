# research/models/__init__.py
from .base import BaseModel, ModelMeta, PredictionResult
from .evaluation import (
    calc_ic, calc_ic_series, calc_ic_decay,
    calc_sharpe, calc_max_drawdown, calc_hit_rate,
    calc_turnover, evaluate_predictions, factor_summary,
)