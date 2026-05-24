# -*- coding: utf-8 -*-
"""
信号类型定义
统一的信号格式，适配融合模型和XMM（徐小明三周期策略）
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any
import numpy as np


class SignalLevel(Enum):
    """信号强度枚举"""
    STRONG_BUY  = "STRONG_BUY"   # 双BUY共振
    BUY         = "BUY"          # 单系统BUY
    HOLD        = "HOLD"         # 观望
    SELL        = "SELL"         # 单系统SELL
    STRONG_SELL = "STRONG_SELL"  # 双SELL共振
    REDUCED     = "REDUCED"      # 信号矛盾，降仓


# ─── 仓位映射 ────────────────────────────────────────────
POSITION_MAP = {
    SignalLevel.STRONG_BUY:  0.80,  # 80% 仓位
    SignalLevel.BUY:         0.50,  # 50% 仓位
    SignalLevel.HOLD:        0.00,  # 空仓观望
    SignalLevel.SELL:        0.00,  # 卖出清仓（港股不支持做空）
    SignalLevel.STRONG_SELL: 0.00,  # 强制卖出清仓（港股不支持做空）
    SignalLevel.REDUCED:     0.20,  # 20% 降仓观望
}


@dataclass
class FusionModelSignal:
    """
    融合模型信号
    来源: 缠论分型分析（分型类型 + RSI特征）
    """
    symbol: str
    date: str
    score: float        # 综合打分 [-100, +100]
    weekly_rsi: float   # 周线RSI
    monthly_rsi: float  # 月线RSI
    resonance: float    # 共振因子
    raw_signal: str     # 'BUY' / 'HOLD' / 'SELL'
    confidence: float   # 置信度 [0, 1]
    
    # 原始因子（用于调试）
    factors: Dict[str, float] = field(default_factory=dict)
    
    @property
    def is_bullish(self) -> bool:
        return self.raw_signal in ('BUY', 'STRONG_BUY')
    
    @property
    def is_bearish(self) -> bool:
        return self.raw_signal in ('SELL', 'STRONG_SELL')


@dataclass
class XMMSignal:
    """
    徐小明三周期策略信号
    来源: XMMSignalEngine（月EMA / 周RSI / 日MACD 三周期共振）
    """
    symbol: str
    date: str
    action: str         # 'BUY' / 'HOLD' / 'SELL'
    confidence: float   # 置信度 [0, 1]
    rsi: float          # 当日RSI
    sma_pos: float      # 价格 vs SMA50 位置
    macd_hist: float = 0.0   # MACD柱状图
    
    # 多Agent分析
    bull_reasoning: str = ""
    bear_reasoning: str = ""
    technical_analysis: str = ""
    
    # MACD 钝化/结构状态机（2026-05-19 徐小明结构系统增强）
    macd_structure_desc: str = ""      # "底结构形成" / "顶钝化" / ""
    macd_structure_level: int = 0      # 0=无, 1=钝化, 2=结构, 3=结构+确认
    
    @property
    def is_bullish(self) -> bool:
        return self.action == 'BUY'
    
    @property
    def is_bearish(self) -> bool:
        return self.action == 'SELL'


@dataclass
class FusionSignal:
    """
    融合信号（最终输出）
    整合两个系统的信号 + LLM因子打分，生成统一决策
    """
    symbol: str
    date: str
    level: SignalLevel

    # 两个子信号
    fusion_model: Optional[FusionModelSignal] = None
    xmm_signal: Optional[XMMSignal] = None

    # LLM 因子打分（可选）
    llm_factor_score: float = 0.0   # LLM因子贡献的信号分 [-100, +100]
    llm_factor_summary: str = ""    # 因子打分摘要文本

    # 综合评估
    position_pct: float = 0.0       # 建议仓位
    confidence: float = 0.0         # 综合置信度
    score: float = 0.0              # 综合打分（含因子贡献）

    # 风险评估
    risk_level: str = "MEDIUM"      # HIGH / MEDIUM / LOW
    warnings: list = field(default_factory=list)

    # 详细原因
    reasoning: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'date': self.date,
            'level': self.level.value,
            'position_pct': self.position_pct,
            'confidence': self.confidence,
            'score': self.score,
            'risk_level': self.risk_level,
            'warnings': self.warnings,
            'reasoning': self.reasoning,
            'llm_factor_score': self.llm_factor_score,
            'llm_factor_summary': self.llm_factor_summary,
            'fusion_model': {
                'signal': self.fusion_model.raw_signal if self.fusion_model else None,
                'score': self.fusion_model.score if self.fusion_model else None,
                'weekly_rsi': self.fusion_model.weekly_rsi if self.fusion_model else None,
            } if self.fusion_model else None,
            'xmm_signal': {
                'action': self.xmm_signal.action if self.xmm_signal else None,
                'confidence': self.xmm_signal.confidence if self.xmm_signal else None,
                'rsi': self.xmm_signal.rsi if self.xmm_signal else None,
                'macd_structure_desc': self.xmm_signal.macd_structure_desc if self.xmm_signal else None,
                'macd_structure_level': self.xmm_signal.macd_structure_level if self.xmm_signal else None,
            } if self.xmm_signal else None,
        }


def fusion_score_to_signal(score: float, threshold: float = 15.0) -> str:
    """融合打分转信号"""
    if score >= threshold:
        return 'BUY'
    elif score <= -threshold:
        return 'SELL'
    else:
        return 'HOLD'


def rsi_to_signal(rsi: float, oversold: float = 30.0, overbought: float = 70.0) -> str:
    """RSI值转信号"""
    if rsi <= oversold:
        return 'BUY'    # 超卖 → 抄底
    elif rsi >= overbought:
        return 'SELL'   # 超买 → 减仓
    else:
        return 'HOLD'


# ═══════════════════════════════════════════════════════════
# 三层架构核心接口定义 (2026-05-08)
# 研究层 → DecisionSignal → 决策层 → OrderDirective → 执行层 → Order
# ═══════════════════════════════════════════════════════════

@dataclass
class DecisionSignal:
    """
    研究层输出 → 决策层输入
    所有研究模块的标准化输出格式。FusionEngine 的输入。
    """
    # --- 基本信息 ---
    ticker: str                    # 标的代码 e.g. "00700.HK", "SPY.US"
    direction: str                 # "BUY" | "SELL" | "HOLD"
    confidence: float              # 0.0 ~ 1.0
    date: str                      # 信号日期

    # --- 因子层 ---
    factor_score: float = 0.0      # 传统因子加权得分
    factor_details: dict = field(default_factory=dict)

    # --- LLM 层 ---
    llm_factor_score: float = 0.0  # LLM 因子得分（离线公式计算）
    llm_factor_summary: str = ""

    # --- 情绪/事件层 ---
    event_sentiment_score: float = 0.0  # LLM 情绪得分 [-100, +100]
    event_type: str = "none"            # none/momentum_shift/volume_anomaly/reversal_signal/breakout
    event_summary: str = ""
    event_confidence: float = 0.0

    # --- 融合层 ---
    fusion_score: float = 0.0      # FusionEngine 加权融合得分
    signal_level: str = "HOLD"     # "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL"

    # --- 市场状态 ---
    regime: str = "CRAB"           # BULL/BEAR/CRAB/RECOVERY/CORRECTION
    vix_regime: str = "CANDIDATE"  # SUPPRESSED/CANDIDATE/RELEASED

    # --- 风险标注 ---
    risk_flags: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    # --- 元数据 ---
    llm_weight_used: float = 0.30  # 本次使用的 LLM 权重 (regime-dependent)
    source: str = ""               # 数据源 (Futu/TickFlow)


@dataclass
class OrderDirective:
    """
    决策层输出 → 执行层输入
    HardGate 审批通过后的执行指令。执行层只接受此格式。
    """
    ticker: str
    action: str                    # "BUY" | "SELL" | "HOLD" | "REDUCE" | "BLOCKED"
    approved: bool                 # HardGate 审批结果
    reject_reasons: list = field(default_factory=list)

    # --- 仓位 ---
    target_position_pct: float = 0.0
    batch_plan: list = field(default_factory=list)

    # --- 止损 ---
    stop_loss_pct: float = -0.12
    trailing_stop_pct: float = -0.12

    # --- 元数据 ---
    signal_ref: Optional[DecisionSignal] = None  # 原始信号引用（审计用）
    decision_timestamp: str = ""
    confidence: float = 0.0
    regime: str = "CRAB"
    warnings: list = field(default_factory=list)


@dataclass
class Order:
    """
    执行层最终提交给 Futu API 的订单。
    零 LLM 参与。纯确定性。
    """
    ticker: str
    action: str                    # "BUY" | "SELL"
    quantity: int                  # 股数
    price_type: str = "MARKET"     # "MARKET" | "LIMIT"
    limit_price: float = 0.0
    order_type: str = "NORMAL"     # "NORMAL" | "STOP_LOSS" | "TRAILING_STOP"
    account_id: str = ""
