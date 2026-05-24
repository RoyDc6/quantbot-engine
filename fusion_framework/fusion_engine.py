# -*- coding: utf-8 -*-
"""
双系统融合引擎
核心：融合模型 + 徐小明(XMM)三周期信号融合 + LLM因子打分
"""

import sys, warnings, importlib
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import asdict

# 安全导入 signal_types（兼容直接运行和包内导入）
if 'fusion_framework.signal_types' in sys.modules:
    from .signal_types import (
        SignalLevel, FusionSignal, FusionModelSignal, XMMSignal,
        POSITION_MAP, fusion_score_to_signal, rsi_to_signal
    )
elif 'signal_types' in sys.modules:
    import signal_types as _st; SignalLevel=_st.SignalLevel; FusionSignal=_st.FusionSignal
    FusionModelSignal=_st.FusionModelSignal; XMMSignal=_st.XMMSignal
    POSITION_MAP=_st.POSITION_MAP; fusion_score_to_signal=_st.fusion_score_to_signal; rsi_to_signal=_st.rsi_to_signal
else:
    from fusion_framework.signal_types import (
        SignalLevel, FusionSignal, FusionModelSignal, XMMSignal,
        POSITION_MAP, fusion_score_to_signal, rsi_to_signal
    )

# LLM 因子打分器
sys.path.insert(0, r'E:\quant')
try:
    from llm_factor_factory.factor_scorer import factor_to_signal_score, FACTORS, score_factors, get_factor_summary
except ImportError:
    factor_to_signal_score = lambda *a, **k: 0.0
    score_factors = lambda *a, **k: {}
    get_factor_summary = lambda *a, **k: ''
    FACTORS = []

warnings.filterwarnings('ignore')


class FusionEngine:
    """
    双系统融合引擎
    
    融合规则矩阵：
    
                    TA BUY     TA HOLD    TA SELL
    FM BUY      STRONG_BUY  BUY        REDUCED
    FM HOLD     BUY         HOLD       SELL
    FM SELL     REDUCED     SELL       STRONG_SELL
    
    关键设计：
    1. 双系统一致 → 高仓位
    2. 信号矛盾 → 降仓观望
    3. 单一系统BUY → 中等仓位
    4. VIX风控 → 高位市场降低仓位
    """
    
    def __init__(self, config: dict = None):
        self.config = config or {}
        
        # 信号阈值
        self.score_threshold = self.config.get('score_threshold', 15.0)
        self.rsi_oversold = self.config.get('rsi_oversold', 30.0)
        self.rsi_overbought = self.config.get('rsi_overbought', 70.0)
        
        # VIX风控参数 (真实VIX数据驱动的分位数阈值)
        self.vix_high_pct = self.config.get('vix_high_pct', 0.80)   # >80%分位=恐慌
        self.vix_panic_pct = self.config.get('vix_panic_pct', 0.95)  # >95%分位=极度恐慌
        self.vix_low_pct = self.config.get('vix_low_pct', 0.20)     # <20%分位=低波动
        self.vix_high_weight = self.config.get('vix_high_weight', 0.5)   # 恐慌降仓至50%
        self.vix_panic_weight = self.config.get('vix_panic_weight', 0.3)  # 极度恐慌降仓至30%
        
        # 真实VIX时间序列 (date -> vix_value)
        self._vix_series: Dict[str, float] = {}
        self._vix_percentile: Dict[str, float] = {}  # date -> 分位数(0~1)
        
        # 市场分工
        self.market_lead = self.config.get('market_lead', {
            'HK': 'fusion_model',   # 港股：融合模型主导
            'CSI300': 'fusion_model',  # A股：融合模型主导
            'SPX': 'xmm_system',     # 美股ETF：徐小明主导
        })

        # ═══════════════════════════════════════════════════════════
        # 静态绝对权重（战术指挥部终极锚定）
        # 主攻手 XMM 60% | 阵地盾 VP 25% | 侦察兵 LLM 15%
        # 此权重为硬编码常量，不随市场状态变化。
        # 断流时：异常源权重归零，剩余存活源按比例重新归一化。
        # ═══════════════════════════════════════════════════════════
        self.BASE_WEIGHTS = {'XMM': 0.60, 'VP': 0.25, 'LLM': 0.15}

    def fuse(self, fm_signal: Optional[FusionModelSignal],
             xmm_signal: Optional[XMMSignal],
             market: str = 'HK',
             llm_factor_score: float = 0.0,
             llm_factor_summary: str = '',
             event_sentiment_score: float = 0.0,
             event_summary: str = '',
             market_state: str = 'CRAB') -> FusionSignal:
        """
        融合两个系统的信号 + LLM因子打分 + 情绪/事件因子

        Args:
            fm_signal: 融合模型信号（来源：缠论）
            xmm_signal: 徐小明三周期信号
            market: 市场代码 (HK/SPX/CSI300)
            llm_factor_score: LLM因子贡献的信号分 [-100, +100]
            llm_factor_summary: LLM因子打分摘要文本
            event_sentiment_score: LLM情绪/事件得分 [-100, +100]
            event_summary: 情绪/事件摘要文本
            market_state: 市场状态 (BULL/BEAR/CRAB/RECOVERY/CORRECTION)，预留自适应权重接口
        """
        if fm_signal is None and xmm_signal is None:
            return self._empty_signal(market)

        # 确定融合信号方向
        level = self._fuse_signals(fm_signal, xmm_signal)

        # P0-3: LLM score 信号级别升降级（最多 1 级，硬上限）
        level = self._apply_llm_level_adjustment(level, llm_factor_score)

        # 计算仓位
        base_position = POSITION_MAP.get(level, 0.0)

        # VIX风控调整
        vix_adjust = self._vix_adjustment(xmm_signal, level)
        position = base_position * vix_adjust

        # 计算综合置信度
        confidence = self._compute_confidence(fm_signal, xmm_signal)

        # 计算静态权重（断流降级：异常源归零，剩余重新归一化）
        sources_active = {
            'XMM': xmm_signal is not None,
            'VP': True,   # VP 信号通过 event_sentiment_score 和 llm_factor_score 注入
            'LLM': abs(llm_factor_score) > 1 or bool(event_summary),
        }
        active_weights = self._compute_active_weights(sources_active)
        xmm_weight = active_weights.get('XMM', 0.0)
        llm_weight  = active_weights.get('LLM', 0.0)
        vp_weight   = active_weights.get('VP', 0.0)

        # 计算综合打分（静态权重护航）
        score = self._compute_score(
            fm_signal, xmm_signal, llm_factor_score,
            xmm_weight=xmm_weight, vp_weight=vp_weight, llm_weight=llm_weight,
            event_sentiment_score=event_sentiment_score,
        )

        # 风险评估
        risk_level, warnings_list = self._assess_risk(fm_signal, xmm_signal, level)

        # 生成推理
        reasoning = self._generate_reasoning(fm_signal, xmm_signal, level, vix_adjust)
        if llm_factor_summary:
            reasoning = f"{llm_factor_summary} | {reasoning}"
        if event_summary:
            reasoning = f"[情绪] {event_summary} | {reasoning}"

        return FusionSignal(
            symbol=fm_signal.symbol if fm_signal else (xmm_signal.symbol if xmm_signal else 'UNKNOWN'),
            date=fm_signal.date if fm_signal else (xmm_signal.date if xmm_signal else ''),
            level=level,
            fusion_model=fm_signal,
            xmm_signal=xmm_signal,
            llm_factor_score=llm_factor_score,
            llm_factor_summary=llm_factor_summary,
            position_pct=round(position, 3),
            confidence=round(confidence, 3),
            score=round(score, 3),
            risk_level=risk_level,
            warnings=warnings_list,
            reasoning=reasoning
        )
    
    def _fuse_signals(self, fm: Optional[FusionModelSignal],
                      xmm: Optional[XMMSignal]) -> SignalLevel:
        """根据信号矩阵确定融合级别（含趋势自适应）"""
        fm_action = fm.raw_signal if fm else 'HOLD'
        xmm_action = xmm.action if xmm else 'HOLD'
        
        # 从FM信号提取趋势信息
        trend_up = False
        if fm and hasattr(fm, 'factors') and fm.factors:
            trend_up = fm.factors.get('trend_up', False)
        
        if trend_up:
            # 上升趋势：XMM均线多头 > FM周RSI超买
            matrix = {
                ('BUY', 'BUY'):   SignalLevel.STRONG_BUY,
                ('BUY', 'HOLD'):  SignalLevel.BUY,
                ('BUY', 'SELL'):  SignalLevel.BUY,      # 趋势中降级
                ('HOLD', 'BUY'): SignalLevel.BUY,
                ('HOLD', 'HOLD'):SignalLevel.HOLD,
                ('HOLD', 'SELL'):SignalLevel.HOLD,     # 趋势中降级
                ('SELL', 'BUY'): SignalLevel.BUY,      # 核心修复
                ('SELL', 'HOLD'):SignalLevel.HOLD,
                ('SELL', 'SELL'):SignalLevel.SELL,
            }
        else:
            # 下降趋势：FM权重更高
            matrix = {
                ('BUY', 'BUY'):   SignalLevel.STRONG_BUY,
                ('BUY', 'HOLD'):  SignalLevel.BUY,
                ('BUY', 'SELL'):  SignalLevel.REDUCED,
                ('HOLD', 'BUY'):  SignalLevel.HOLD,
                ('HOLD', 'HOLD'):SignalLevel.HOLD,
                ('HOLD', 'SELL'):SignalLevel.SELL,
                ('SELL', 'BUY'):  SignalLevel.REDUCED,
                ('SELL', 'HOLD'):SignalLevel.SELL,
                ('SELL', 'SELL'):SignalLevel.STRONG_SELL,
            }
        
        return matrix.get((fm_action, xmm_action), SignalLevel.HOLD)

    # ─── LLM 信号级别调整（P0-3）───────────────────────────
    LLM_UPGRADE_THRESHOLD = 25.0     # LLM score >= 25 → 升级 1 级
    LLM_DOWNGRADE_THRESHOLD = -25.0  # LLM score <= -25 → 降级 1 级

    _LEVEL_ORDER = [                 # 从最熊到最牛
        SignalLevel.STRONG_SELL,     # index 0
        SignalLevel.SELL,            # index 1
        SignalLevel.REDUCED,         # index 2
        SignalLevel.HOLD,            # index 3
        SignalLevel.BUY,             # index 4
        SignalLevel.STRONG_BUY,      # index 5
    ]

    def _apply_llm_level_adjustment(self, level: SignalLevel, llm_score: float) -> SignalLevel:
        """
        P0-3: LLM score 调节信号级别
          - LLM score >= 25+ → 升级 1 级（更看涨）
          - LLM-25- → 降级 1 级（更看跌）
          - 最多 1 级，硬上限不超过 STRONG_BUY / STRONG_SELL
        """
        if abs(llm_score) < 25.0:
            return level
        try:
            idx = self._LEVEL_ORDER.index(level)
        except ValueError:
            return level
        if llm_score >= self.LLM_UPGRADE_THRESHOLD:
            idx = min(idx + 1, len(self._LEVEL_ORDER) - 1)
        elif llm_score <= self.LLM_DOWNGRADE_THRESHOLD:
            idx = max(idx - 1, 0)
        return self._LEVEL_ORDER[idx]

    def set_vix_data(self, vix_series: Dict[str, float]):
        """
        设置真实VIX/VXX时间序列，自动计算滚动分位数
        
        Args:
            vix_series: {date_str: vix_value}，按日期排序
        """
        self._vix_series = vix_series
        # 计算每个日期的滚动分位数（用lookback 252天≈1年）
        dates = sorted(vix_series.keys())
        values = [vix_series[d] for d in dates]
        n = len(values)
        lookback = 252
        
        for i, d in enumerate(dates):
            start = max(0, i - lookback + 1)
            window = values[start:i+1]
            pct = sum(1 for v in window if v <= values[i]) / len(window)
            self._vix_percentile[d] = pct
    
    def get_vix_pct(self, date: str) -> Optional[float]:
        """获取某日的VIX分位数"""
        return self._vix_percentile.get(date)
    
    def _vix_adjustment(self, xmm: Optional[XMMSignal], level: SignalLevel) -> float:
        """
        真实VIX + RSI 双层风控调整
        
        核心逻辑（基于VIX研究成果 2026-04-16）:
        - VIX高位 = 恐慌 = SPY买点（胜率83%）→ 不降BUY仓位，反而可加
        - VIX低位 = 自满 = 隐藏风险 → 略微降仓
        - VIX突然飙升 + 已持仓 → 保护利润，适度减仓
        - RSI超买补充风控
        """
        adjust = 1.0
        is_buy = level in (SignalLevel.STRONG_BUY, SignalLevel.BUY, SignalLevel.REDUCED)
        is_sell = level in (SignalLevel.STRONG_SELL, SignalLevel.SELL)

        # 1. 真实VIX风控（主风控层）
        if xmm is not None and xmm.date and xmm.date in self._vix_percentile:
            vix_pct = self._vix_percentile[xmm.date]
            vix_val = self._vix_series.get(xmm.date, 0)
            
            if is_buy:
                # BUY方向：VIX高位是机会，不减仓
                if vix_pct >= self.vix_panic_pct:
                    # 极度恐慌=历史性买点，保持满仓（不砍仓）
                    adjust *= 1.0  # 不变
                elif vix_pct >= self.vix_high_pct:
                    # 高位恐慌=买点区域，保持满仓
                    adjust *= 1.0  # 不变
                elif vix_pct <= self.vix_low_pct:
                    # VIX低位=自满期，适度降仓（防突然暴跌）
                    adjust *= 0.8
            elif is_sell:
                # SELL方向：VIX高位恐慌时卖出要谨慎（可能是恐慌底）
                if vix_pct >= self.vix_high_pct:
                    # 恐慌中不加码卖出（避免恐慌底割肉）
                    adjust *= 0.5  # 减半卖出意愿
        
        # 2. RSI超买补充风控（辅助层）
        if xmm is not None:
            if xmm.rsi >= 78:
                adjust *= 0.4
            elif xmm.rsi >= 72:
                adjust *= 0.7
        
        return adjust
    
    # ─── 断流降级权重计算 ──────────────────────────
    def _compute_active_weights(self, sources_active: Dict[str, bool]) -> Dict[str, float]:
        """
        静态权重断流降级。
        存活源的 BASE_WEIGHTS 重新归一化至总和 = 1.0，异常源权重归零。

        Args:
            sources_active: {'XMM': bool, 'VP': bool, 'LLM': bool}
        
        测试：LLM 缺失 → XMM/(XMM+VP)=0.60/0.85=70.6%, VP/(XMM+VP)=0.25/0.85=29.4%
        """
        total = sum(self.BASE_WEIGHTS[s] for s in self.BASE_WEIGHTS if sources_active.get(s, False))
        if total <= 0:
            # 所有源都挂了 → 均匀降级（不应发生）
            return {s: 1.0 / len(self.BASE_WEIGHTS) for s in self.BASE_WEIGHTS}
        return {
            s: (self.BASE_WEIGHTS[s] / total if sources_active.get(s, False) else 0.0)
            for s in self.BASE_WEIGHTS
        }

    def _compute_confidence(self, fm: Optional[FusionModelSignal],
                            xmm: Optional[XMMSignal]) -> float:
        """计算综合置信度"""
        scores = []
        if fm:
            scores.append(fm.confidence)
        if xmm:
            scores.append(xmm.confidence)
        if not scores:
            return 0.0
        return np.mean(scores)
    
    def _compute_score(self, fm: Optional[FusionModelSignal],
                       xmm: Optional[XMMSignal],
                       llm_factor_score: float = 0.0,
                       xmm_weight: float = 0.60,
                       vp_weight: float = 0.25,
                       llm_weight: float = 0.15,
                       event_sentiment_score: float = 0.0) -> float:
        """计算综合打分（静态权重三路加权融合 60/25/15）"""

        raw_scores = []
        if fm:
            raw_scores.append(fm.score)
        if xmm:
            xmm_score = (xmm.confidence - 0.5) * 200 if xmm.confidence > 0.5 else -(50 - xmm.confidence * 100)
            raw_scores.append(xmm_score)

        if not raw_scores:
            return float(llm_factor_score) if abs(llm_factor_score) > 1 else 0.0

        base_score = np.mean(raw_scores)

        # 无因子分数时直接返回
        if abs(llm_factor_score) < 1 and abs(event_sentiment_score) < 1:
            return round(float(base_score), 1)

        # 三路加权融合（静态权重，已归一化）：
        # base × (1 - llm - vp) + llm_factor × llm_weight + event × vp_weight
        # 其中 base 代表 XMM 贡献，llm 独立，event 归入 VP 通道
        factor_weight = max(0.1, 1 - llm_weight - vp_weight)
        combined = base_score * factor_weight + llm_factor_score * llm_weight + event_sentiment_score * vp_weight
        return round(float(combined), 1)
    
    def _assess_risk(self, fm: Optional[FusionModelSignal],
                     xmm: Optional[XMMSignal],
                     level: SignalLevel) -> Tuple[str, List[str]]:
        """评估风险"""
        warnings = []
        
        # 1. RSI超买风险
        if xmm and xmm.rsi > 70:
            warnings.append(f"日线RSI超买({xmm.rsi:.1f})")
        if fm and fm.weekly_rsi > 70:
            warnings.append(f"周线RSI超买({fm.weekly_rsi:.1f})")
        if fm and fm.monthly_rsi > 70:
            warnings.append(f"月线RSI超买({fm.monthly_rsi:.1f})")
        
        # 2. 双系统矛盾风险
        if level == SignalLevel.REDUCED:
            warnings.append("双系统信号矛盾")
        
        # 3. 极度超买风险
        if xmm and xmm.rsi > 80:
            warnings.append(f"⚠️ 极度超买({xmm.rsi:.1f})，追涨风险极高")
        
        # 4. 低置信度风险
        confidence = self._compute_confidence(fm, xmm)
        if confidence < 0.4:
            warnings.append(f"置信度低({confidence:.1%})")
        
        # 风险等级
        if any('极度' in w for w in warnings) or len(warnings) >= 3:
            risk = "HIGH"
        elif warnings:
            risk = "MEDIUM"
        else:
            risk = "LOW"
        
        return risk, warnings
    
    def _generate_reasoning(self, fm: Optional[FusionModelSignal],
                            xmm: Optional[XMMSignal],
                            level: SignalLevel,
                            vix_adj: float) -> str:
        """生成决策推理"""
        parts = []
        
        # 融合级别说明
        level_desc = {
            SignalLevel.STRONG_BUY: "双系统共振买入",
            SignalLevel.BUY: "单系统买入信号",
            SignalLevel.HOLD: "观望",
            SignalLevel.SELL: "单系统卖出信号",
            SignalLevel.STRONG_SELL: "双系统共振卖出",
            SignalLevel.REDUCED: "信号矛盾，降仓观望",
        }
        parts.append(level_desc.get(level, ""))
        
        # 融合模型说明
        if fm:
            parts.append(f"融合模型打分{fm.score:.1f}(RSI周{fm.weekly_rsi:.1f}月{fm.monthly_rsi:.1f})")
        
        # 徐小明说明
        if xmm:
            parts.append(f"XMM系统RSI={xmm.rsi:.1f}信心{xmm.confidence:.1%}")
        
        # VIX调整
        if vix_adj < 0.99:
            vix_pct = None
            if xmm and xmm.date and xmm.date in self._vix_percentile:
                vix_pct = self._vix_percentile[xmm.date]
            if vix_pct is not None:
                parts.append(f"VIX分位{vix_pct:.0%}→仓位×{vix_adj:.0%}")
            else:
                parts.append(f"风控→仓位×{vix_adj:.0%}")
        
        return " | ".join(parts)
    
    def _empty_signal(self, market: str) -> FusionSignal:
        """空信号"""
        return FusionSignal(
            symbol='UNKNOWN',
            date='',
            level=SignalLevel.HOLD,
            position_pct=0.0,
            confidence=0.0,
            score=0.0,
            risk_level="MEDIUM",
            warnings=["缺少信号数据"],
            reasoning="无有效信号"
        )


# ─── 批量融合 ────────────────────────────────────────────
def fuse_batch(fm_signals: List[FusionModelSignal],
               xmm_signals: List[XMMSignal],
               market: str = 'HK') -> List[FusionSignal]:
    """
    批量融合信号
    按symbol匹配两个系统的信号
    """
    engine = FusionEngine()
    
    # 按symbol索引
    fm_dict = {s.symbol: s for s in fm_signals}
    xmm_dict = {s.symbol: s for s in xmm_signals}
    
    all_symbols = set(fm_dict.keys()) | set(xmm_dict.keys())
    results = []
    
    for sym in all_symbols:
        fm = fm_dict.get(sym)
        xmm = xmm_dict.get(sym)
        result = engine.fuse(fm, xmm, market)
        results.append(result)
    
    # 按综合打分排序
    results.sort(key=lambda x: x.score, reverse=True)
    return results


# ─── 快速测试 ────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 60)
    print("双系统融合引擎 - 快速测试")
    print("=" * 60)
    
    engine = FusionEngine()
    
    # 测试用例
    test_cases = [
        # 双XMM共振
        {
            'name': '双系统共振',
            'fm': FusionModelSignal('SPY.US', '2026-04-17', 30.0, 25.0, 35.0, 1.5, 'BUY', 0.80),
            'xmm': XMMSignal('SPY.US', '2026-04-17', 'BUY', 0.85, 1.05, 0.3),
        },
        # 融合模型BUY，XMM HOLD
        {
            'name': '融合模型主导',
            'fm': FusionModelSignal('00700.HK', '2026-04-17', 45.0, 28.0, 40.0, 1.0, 'BUY', 0.75),
            'xmm': XMMSignal('00700.HK', '2026-04-17', 'HOLD', 0.55, 1.0, 0.1),
        },
        # 信号矛盾
        {
            'name': '信号矛盾',
            'fm': FusionModelSignal('CSI300', '2026-04-17', 50.0, 75.0, 80.0, -1.5, 'BUY', 0.70),
            'xmm': XMMSignal('CSI300', '2026-04-17', 'SELL', 0.75, 0.95, -0.2),
        },
        # 双SELL共振
        {
            'name': '双SELL共振',
            'fm': FusionModelSignal('AAPL.US', '2026-04-17', -40.0, 72.0, 78.0, -1.5, 'SELL', 0.80),
            'xmm': XMMSignal('AAPL.US', '2026-04-17', 'SELL', 0.80, 0.90, -0.4),
        },
        # 极度超买降仓
        {
            'name': '极度超买降仓',
            'fm': FusionModelSignal('NVDA.US', '2026-04-17', 35.0, 80.0, 85.0, -1.0, 'BUY', 0.85),
            'xmm': XMMSignal('NVDA.US', '2026-04-17', 'BUY', 0.90, 1.15, 0.5),
        },
    ]
    
    for tc in test_cases:
        print(f"\n{'─'*60}")
        print(f"测试: {tc['name']}")
        
        result = engine.fuse(tc['fm'], tc['xmm'])
        
        print(f"  融合级别: {result.level.value}")
        print(f"  仓位: {result.position_pct:.0%}")
        print(f"  置信度: {result.confidence:.1%}")
        print(f"  风险: {result.risk_level}")
        print(f"  推理: {result.reasoning}")
        if result.warnings:
            print(f"  ⚠️ 警告: {'; '.join(result.warnings)}")
