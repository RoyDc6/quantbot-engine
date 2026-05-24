# -*- coding: utf-8 -*-
"""
HardGate — 硬门槛检查层
决策层核心：集中所有信号准入规则，任何一条不通过 → 拒绝

架构位置：
  研究层(信号) → HardGate(准入检查) → 执行层(纯规则下单)
  LLM 自由度: 无（纯规则，零 LLM 调用）
"""

from dataclasses import dataclass, field
from typing import Optional


# ─── 配置常量 ─────────────────────────────────────────────
class GateConfig:
    """硬门槛配置。修改需人工审核。"""

    # 置信度门槛
    MIN_CONFIDENCE = 0.60

    # 信号等级 → 最低融合得分
    MIN_FUSION_SCORE = {
        'STRONG_BUY':  60,
        'BUY':         40,
        'HOLD':         0,
        'SELL':        40,
        'STRONG_SELL': 60,
        'REDUCED':     30,
    }

    # 信号等级 → 最低置信度（覆盖全局 MIN_CONFIDENCE）
    MIN_LEVEL_CONFIDENCE = {
        'STRONG_BUY':  0.75,
        'BUY':         0.65,
        'HOLD':         0.00,
        'SELL':        0.65,
        'STRONG_SELL': 0.75,
        'REDUCED':     0.60,
    }

    # 市场状态 → 仓位上限
    MARKET_STATE_POSITION_LIMIT = {
        'BULL':       0.80,
        'RECOVERY':   0.60,
        'CRAB':       0.40,
        'CORRECTION': 0.20,
        'BEAR':       0.10,
    }

    # VIX Regime → 是否压制买入
    VIX_BLOCK_BUY = {
        'SUPPRESSED': True,   # VIX 高恐惧 → 禁止新开多仓
        'CANDIDATE':  False,
        'RELEASED':   False,
    }

    # 仓位约束
    MAX_SINGLE_POSITION_PCT = 20   # 单只最大 20%
    MAX_TOTAL_POSITION_PCT  = 80   # 总仓位最大 80%

    # 信号一致性：因子方向 vs LLM 方向必须一致
    REQUIRE_FACTOR_LLM_AGREEMENT = True

    # 极端情绪检查
    EXTREME_SENTIMENT_BLOCK_BUY = -80   # sentiment < -80 → 禁止买入
    EXTREME_SENTIMENT_BLOCK_SELL = 80   # sentiment > 80 → 禁止卖出（可能过度乐观反转）


# ─── 审批结果 ─────────────────────────────────────────────
@dataclass
class GateResult:
    """HardGate 审批结果"""
    approved: bool
    symbol: str
    action: str                     # 'BUY' | 'SELL' | 'HOLD' | 'REDUCE' | 'BLOCKED'
    reject_reasons: list = field(default_factory=list)
    adjusted_position_pct: float = 0.0
    adjusted_max_exposure: float = 0.80
    warnings: list = field(default_factory=list)

    def summary(self) -> str:
        if self.approved:
            return f'✅ {self.symbol} {self.action} (仓位上限: {self.adjusted_position_pct:.0%})'
        reasons = ' | '.join(self.reject_reasons)
        return f'🚫 {self.symbol} BLOCKED: {reasons}'


# ─── HardGate ─────────────────────────────────────────────
class HardGate:
    """
    硬门槛检查引擎。
    输入: signal dict（来自 fetch_and_analyze）
    输出: GateResult（approved/reject_reasons/adjusted_position）

    职责边界：
    - 只做准入检查，不修改信号内容
    - 只输出批准/拒绝 + 仓位调整，不输出交易指令
    - 零 LLM 调用，纯规则
    """

    def __init__(self, config: GateConfig = None):
        self.config = config or GateConfig()

    def check(self, signal: dict, market_state: str = 'CRAB',
              vix_regime: str = 'CANDIDATE',
              current_positions: list = None,
              total_value: float = 0) -> GateResult:
        """
        执行全部硬门槛检查。

        Args:
            signal: fetch_and_analyze 输出的信号 dict
            market_state: BULL/BEAR/CRAB/RECOVERY/CORRECTION
            vix_regime: SUPPRESSED/CANDIDATE/RELEASED
            current_positions: 当前持仓列表
            total_value: 总资产

        Returns:
            GateResult
        """
        symbol = signal.get('symbol', 'UNKNOWN')
        level = signal.get('fusion_level', 'HOLD')
        score = signal.get('fusion_score', 0)
        confidence = signal.get('fusion_confidence', 0)
        reject_reasons = []
        warnings = []

        # ─── 1. 置信度检查 ──────────────────────────────
        level_conf = self.config.MIN_LEVEL_CONFIDENCE.get(level, self.config.MIN_CONFIDENCE)
        if confidence < level_conf:
            reject_reasons.append(
                f'置信度 {confidence:.2f} < {level_conf:.2f} (等级: {level})'
            )

        # ─── 2. 融合得分检查 ────────────────────────────
        level_score = self.config.MIN_FUSION_SCORE.get(level, 40)
        if level not in ('HOLD',) and abs(score) < level_score:
            reject_reasons.append(
                f'融合得分 {score:.1f} < {level_score} (等级: {level})'
            )

        # ─── 3. 信号一致性检查 ─────────────────────────
        if self.config.REQUIRE_FACTOR_LLM_AGREEMENT:
            fm_dir = signal.get('fm_signal', 'HOLD')     # 因子方向
            xmm_dir = signal.get('xmm_signal', 'HOLD')     # XMM方向
            llm_dir = self._score_to_dir(signal.get('llm_factor_score', 0))

            # 如果 LLM 和因子方向相反且都有明确方向
            if (fm_dir in ('BUY', 'SELL') and llm_dir in ('BUY', 'SELL')
                    and fm_dir != llm_dir):
                if level in ('STRONG_BUY', 'STRONG_SELL'):
                    reject_reasons.append(
                        f'信号冲突: 因子={fm_dir} LLM={llm_dir} (强信号需一致)'
                    )
                else:
                    warnings.append(
                        f'信号分歧: 因子={fm_dir} LLM={llm_dir}'
                    )

        # ─── 4. VIX 约束（买入方向）───────────────────
        if level in ('STRONG_BUY', 'BUY'):
            vix_block = self.config.VIX_BLOCK_BUY.get(vix_regime, False)
            if vix_block:
                reject_reasons.append(
                    f'VIX {vix_regime} 环境禁止新开多仓'
                )

        # ─── 4b. 极端情绪检查 ────────────────────────
        event_score = signal.get('event_sentiment_score', 0)
        if event_score != 0:
            if event_score < self.config.EXTREME_SENTIMENT_BLOCK_BUY and level in ('STRONG_BUY', 'BUY'):
                reject_reasons.append(
                    f'极端看空情绪 ({event_score:.0f}) 禁止买入'
                )
            elif event_score > self.config.EXTREME_SENTIMENT_BLOCK_SELL and level in ('STRONG_SELL', 'SELL'):
                warnings.append(
                    f'极端看多情绪 ({event_score:.0f})，卖出信号可能反转'
                )

        # ─── 5. 仓位约束 ──────────────────────────────
        max_exposure = self.config.MARKET_STATE_POSITION_LIMIT.get(
            market_state, 0.40
        )
        max_exposure = max_exposure / 100.0 if max_exposure > 1 else max_exposure

        if current_positions and total_value > 0:
            # 总仓位检查
            pos_value = sum(p.get('shares', 0) * p.get('current_price', 0)
                           for p in current_positions)
            current_pct = pos_value / total_value
            if current_pct >= max_exposure:
                reject_reasons.append(
                    f'总仓位 {current_pct:.1%} >= 上限 {max_exposure:.0%} ({market_state})'
                )

            # 单只仓位检查
            for pos in current_positions:
                if pos.get('symbol') == symbol:
                    val = pos.get('shares', 0) * pos.get('current_price', 0)
                    pct = val / total_value
                    max_single = self.config.MAX_SINGLE_POSITION_PCT / 100.0
                    if pct >= max_single:
                        reject_reasons.append(
                            f'已持有 {symbol} 仓位 {pct:.1%} >= 单只上限 {max_single:.0%}'
                        )

        # ─── 6. 计算建议仓位 ─────────────────────────
        target_pct = signal.get('target_position', 0.10)
        # 限制不超过市场状态仓位上限
        adjusted_pct = min(target_pct, max_exposure)
        # 限制不超过单只上限
        adjusted_pct = min(adjusted_pct, self.config.MAX_SINGLE_POSITION_PCT / 100.0)

        # ─── 判定 ────────────────────────────────────
        approved = len(reject_reasons) == 0
        action = level if approved else 'BLOCKED'

        return GateResult(
            approved=approved,
            symbol=symbol,
            action=action,
            reject_reasons=reject_reasons,
            adjusted_position_pct=adjusted_pct if approved else 0.0,
            adjusted_max_exposure=max_exposure,
            warnings=warnings,
        )

    @staticmethod
    def _score_to_dir(score: float) -> str:
        """LLM因子得分 → 方向"""
        if score >= 15:
            return 'BUY'
        elif score <= -15:
            return 'SELL'
        return 'HOLD'
