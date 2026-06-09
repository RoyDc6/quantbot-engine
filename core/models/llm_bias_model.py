# -*- coding: utf-8 -*-
"""
LLMBiasModel — LLM 独立偏向模型（Bias Model）

架构层: 信号模型层，与 XMM、VP 平级

职责:
  1. 按标的调用 MarketEventDetector 生成情绪偏向
  2. 输出标准 DecisionSignal 供 FusionEngine/HardGate 消费
  3. 不直接控制交易单 — 执行权在中枢

接口:
  generator(ticker, kline_data, market_state) -> DecisionSignal
  generate_batch(tickers, ...) -> Dict[str, DecisionSignal]

设计原则:
  - 复用现有 MarketEventDetector（NVIDIA NIM 管线），不重复造轮子
  - 缓存防重复（继承 event_cache 磁盘缓存 + 内存 last_run 检查）
  - LLM 异常 → bias=0, state=NEUTRAL（不干扰其他模型）
"""

import datetime
import logging
from pathlib import Path
from typing import Dict, Optional, List, Any

from fusion_framework.signal_types import DecisionSignal
from market_state.event_detector import MarketEventDetector
import config

logger = logging.getLogger('LLM_BIAS_MODEL')
logger.setLevel(logging.INFO)

from core.paths import EVENT_CACHE_DIR

CACHE_DIR = EVENT_CACHE_DIR


class LLMBiasModel:
    """
    LLM 独立偏向模型

    Use:
        model = LLMBiasModel()
        signal = model.generator("00700.HK", kline_dict, "BULL")
        # signal is DecisionSignal(event_sentiment_score=..., ...)
    """

    def __init__(self, name: str = "LLM_Bias_Model",
                 model_name: str = None):
        self.name = name
        self._detector = MarketEventDetector(model=model_name or config.LLM_MODEL)
        self._last_run_date: Optional[datetime.date] = None
        self._batch_cache: Dict[str, DecisionSignal] = {}

    # ── 核心方法 ─────────────────────────────────────────

    def generator(self, ticker: str, kline_data: dict,
                  market_state: str = 'CRAB',
                  recent_signals: Optional[List[Dict]] = None,
                  force: bool = False) -> DecisionSignal:
        """
        生成单标的偏向信号

        Args:
            ticker: 标的代码 (e.g. "00700.HK")
            kline_data: K 线 dict {close, high, low, volume, timestamp}
            market_state: 市场状态 BULL/BEAR/CRAB/RECOVERY/CORRECTION
            recent_signals: 近期该标的的信号列表（可选用于信号翻转检测）
            force: 是否强制重新调用（跳过缓存）

        Returns:
            DecisionSignal(event_sentiment_score=..., ...)
        """
        today = datetime.date.today()

        # 内存缓存（同一 ticker 一天只调一次）
        if not force and self._last_run_date == today and ticker in self._batch_cache:
            return self._batch_cache[ticker]

        # ── 钢化外层: 任何异常都兜底返回 NEUTRAL DecisionSignal ──
        try:
            # 调用 MarketEventDetector（含 LLM + 缓存）
            event_result = self._detector.analyze_sentiment(
                symbol=ticker,
                price_data=kline_data,
                recent_signals=recent_signals or [],
                market_state=market_state,
                force=force,
            )

            # 类型强转: 即使 LLM 返回垃圾也能安全提取
            if event_result is None or not isinstance(event_result, dict):
                raise ValueError(f'EventDetector 返回非 dict: {type(event_result).__name__}')

            sentiment_score = float(event_result.get('sentiment_score', 0.0))
            sentiment_confidence = MarketEventDetector._normalize_confidence(
                event_result.get('confidence', 0.0)
            )
            event_summary = str(event_result.get('event_summary', ''))
            event_type = str(event_result.get('event_type', 'none'))
            signal_warnings = list(event_result.get('warnings') or [])

            # 数值越界钳制
            sentiment_score = max(-100.0, min(100.0, sentiment_score))

            # 偏向状态映射
            if sentiment_score > 15:
                state = 'BULL'
            elif sentiment_score < -15:
                state = 'BEAR'
            else:
                state = 'NEUTRAL'

            # 标准化为 DecisionSignal
            signal = DecisionSignal(
                ticker=ticker,
                direction='HOLD',                   # Bias Model 不输出执行方向
                confidence=sentiment_confidence,
                date=today.isoformat(),
                factor_score=0.0,
                llm_factor_score=sentiment_score,   # 偏向分作为 LLM 因子
                event_sentiment_score=sentiment_score,
                event_type=event_type,
                event_summary=event_summary,
                event_confidence=sentiment_confidence,
                regime=market_state,
                source='LLMBiasModel',
                signal_level=state,                 # BULL/BEAR/NEUTRAL
                warnings=signal_warnings + (
                    [] if abs(sentiment_score) < 80 else [f'极端偏向: {state}({sentiment_score:.0f})']
                ),
            )

            logger.info(
                f'[{self.name}] {ticker} | {state} | '
                f'bias={sentiment_score:+.0f} conf={sentiment_confidence:.2f} '
                f'type={event_type}'
            )

        except Exception as e:
            # 兜底: 任何异常 → NEUTRAL，不影响其他模型
            logger.error(
                f'[{self.name}] {ticker} | 异常降级到 NEUTRAL: {str(e)[:100]}'
            )
            signal = DecisionSignal(
                ticker=ticker,
                direction='HOLD',
                confidence=0.0,
                date=today.isoformat(),
                factor_score=0.0,
                llm_factor_score=0.0,
                event_sentiment_score=0.0,
                event_type='none',
                event_summary=f'LLM Bias 异常降级: {str(e)[:60]}',
                event_confidence=0.0,
                regime=market_state,
                source='LLMBiasModel',
                signal_level='NEUTRAL',
                warnings=[f'异常降级: {str(e)[:60]}'],
            )

        # 缓存（无论正常或降级均缓存）
        self._batch_cache[ticker] = signal
        self._last_run_date = today

        return signal

    # ── 批量入口 ─────────────────────────────────────────

    def generate_batch(self, tickers: List[str],
                       kline_map: Dict[str, dict],
                       market_state: str = 'CRAB',
                       signal_map: Optional[Dict[str, List[Dict]]] = None) -> Dict[str, DecisionSignal]:
        """
        批量生成多标的偏向信号

        Args:
            tickers: 标的列表
            kline_map: {ticker: kline_data_dict}
            market_state: 统一市场状态（或按标的独立传入）
            signal_map: {ticker: [recent_signals]} 可选

        Returns:
            {ticker: DecisionSignal}
        """
        result = {}
        for ticker in tickers:
            kline = kline_map.get(ticker, {})
            signals = (signal_map or {}).get(ticker, None)
            result[ticker] = self.generator(
                ticker, kline, market_state,
                recent_signals=signals,
            )
        return result

    # ── 工具方法 ─────────────────────────────────────────

    def get_bias_score(self, ticker: str) -> float:
        """获取最近一次偏向分（-100~+100），未缓存则返回 0"""
        cached = self._batch_cache.get(ticker)
        if cached:
            return cached.event_sentiment_score
        return 0.0

    def clear_cache(self):
        """清空内存缓存（下次调用会重新请求 LLM）"""
        self._batch_cache.clear()
        self._last_run_date = None
        logger.info(f'[{self.name}] 内存缓存已清空')
