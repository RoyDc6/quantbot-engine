# -*- coding: utf-8 -*-
"""
[已归档] core/signal_engine.py — QuantBot 统一信号引擎（旧版）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
此文件于 2026-05-21 正式退役。
包含: 缠论分型(FM) + XMM + LLM因子 → FusionEngine 的旧调度逻辑。

替代方案:
  → FusionController (core/fusion_controller.py) 作为唯一战术中控台
     三驾马车: XMM + VP + LLM → FusionEngine → HardGate → OrderDirective
  → unified_runner.py 直接调度 FusionController，不再使用本模块

保留原因: XMMSignal 和 FusionModelSignal 类型定义和部分因子代码
          可能被其他模块引用，暂时保留以维护兼容性。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from .utils import calc_rsi, calc_weekly_rsi, calc_monthly_rsi
from .data_fetcher import DataFetcher

# === 模型依赖路径注入 ============================================
BASE = Path(__file__).resolve().parent.parent  # E:\quant
FW   = BASE / 'fusion_framework'
SKILL = Path('C:/Users/RoyGoode/.workbuddy/skills/xmm-strategy/scripts')

for p in [str(BASE), str(FW), str(SKILL)]:
    if p not in sys.path:
        sys.path.insert(0, p)

warnings.filterwarnings('ignore')

# === 导入现有模型（不改动）========================================
try:
    from chan.fractal import mark_fractals
except ImportError:
    mark_fractals = None

try:
    from xmm_signals import XMMSignalEngine
except ImportError:
    XMMSignalEngine = None

try:
    from fusion_engine import FusionEngine as FrameworkEngine
except ImportError:
    try:
        from fusion_framework.fusion_engine import FusionEngine as FrameworkEngine
    except ImportError:
        FrameworkEngine = None

try:
    from signal_types import FusionModelSignal, XMMSignal
except ImportError:
    try:
        from fusion_framework.signal_types import FusionModelSignal, XMMSignal
    except ImportError:
        FusionModelSignal = None
        XMMSignal = None

try:
    from llm_factor_factory.factor_scorer import score_factors, factor_to_signal_score, get_factor_summary
except ImportError:
    score_factors = None
    factor_to_signal_score = None
    get_factor_summary = None

try:
    from market_state.classifier import MarketStateClassifier
except ImportError:
    MarketStateClassifier = None

try:
    from market_state.event_detector import MarketEventDetector
except ImportError:
    MarketEventDetector = None

try:
    from fusion_framework.hard_gate import HardGate
except ImportError:
    HardGate = None


class SignalEngine:
    """统一信号引擎 — 港股/美股共用。"""

    def __init__(self, data_fetcher=None, vix_map=None):
        """
        Args:
            data_fetcher: DataFetcher 实例
            vix_map: VIX 数据 {date_str: value}
        """
        self.fetcher = data_fetcher or DataFetcher()

        # 初始化各子引擎
        self.fw_engine = None
        self.xmm_engine = None

        if FrameworkEngine:
            self.fw_engine = FrameworkEngine()
            if vix_map:
                self.fw_engine.set_vix_data(vix_map)

        if XMMSignalEngine:
            self.xmm_engine = XMMSignalEngine()

        # v2.2: EventDetector（研究层 LLM 情绪/事件检测）
        self.event_detector = None
        if MarketEventDetector:
            self.event_detector = MarketEventDetector()

        # v2.2: HardGate（决策层纯规则硬门槛）
        self.hard_gate = None
        if HardGate:
            self.hard_gate = HardGate()

    def analyze(self, symbol, market='HK', market_state='CRAB'):
        """对单个标的运行完整信号分析。
        
        Args:
            symbol: 标准符号，如 '00700.HK', 'SPY.US'
            market: 'HK' 或 'US'
            market_state: 市场状态 (BULL/BEAR/CRAB/RECOVERY/CORRECTION)
        
        Returns:
            dict: 完整信号结果，或 None（数据不足时）
        """
        # 1. 获取 K 线数据
        df = self.fetcher.fetch_klines(symbol, 252)
        if df is None or len(df) < 50:
            return None

        close = df['close'].values.astype(float)
        high = df['high'].values.astype(float)
        low = df['low'].values.astype(float)
        last_close = float(close[-1])
        date = str(df['date'].iloc[-1])[:10] if 'date' in df.columns else ''
        source = df.attrs.get('source', 'Unknown')

        # 2. 缠论分型 — 近期窗口评分（确保 fm_score ∈ [-100, +100]）
        ftype, top_cnt, bot_cnt = 'NONE', 0, 0
        if mark_fractals is not None:
            try:
                df2 = mark_fractals(df.copy())
                top_cnt_all = int((df2['fractal'] == 1).sum())
                bot_cnt_all = int((df2['fractal'] == -1).sum())
                # 近期分型窗口（最近 20 根 K 线），用于得分计算
                RECENT_WINDOW = 20
                recent = df2.iloc[-RECENT_WINDOW:]
                top_cnt = int((recent['fractal'] == 1).sum())
                bot_cnt = int((recent['fractal'] == -1).sum())
                lt = df2[df2['fractal'] == 1].tail(1)
                lb = df2[df2['fractal'] == -1].tail(1)
                if len(lt) and len(lb):
                    ftype = 'TOP' if lt.index[-1] > lb.index[-1] else 'BOTTOM'
                elif len(lt):
                    ftype = 'TOP'
                elif len(lb):
                    ftype = 'BOTTOM'
            except Exception as e:
                import traceback
                print(f'  [WARN] 缠论分型异常: {e}')
                traceback.print_exc()

        # 3. 融合模型信号 (FusionModelSignal)
        w_rsi = calc_weekly_rsi(close, 4)
        m_rsi = calc_monthly_rsi(close, 20)
        rsi_d = calc_rsi(close, 14)

        sma50 = np.full(len(close), np.nan)
        for i in range(49, len(close)):
            sma50[i] = sma50[i - 1] + (close[i] - sma50[i - 1]) / 50
        trend_up = close[-1] > sma50[-1] if not np.isnan(sma50[-1]) else False

        if ftype == 'BOTTOM':
            score, sig, conf = bot_cnt * 2.0, 'BUY', min(0.88, 0.5 + bot_cnt * 0.05)
        elif ftype == 'TOP':
            score, sig, conf = -top_cnt * 2.0, 'SELL', min(0.88, 0.5 + top_cnt * 0.05)
        else:
            score, sig, conf = 0.0, 'HOLD', 0.50

        fm_sig = None
        if FusionModelSignal:
            fm_sig = FusionModelSignal(symbol, date, float(score), float(w_rsi),
                                       float(m_rsi), 0.0, sig, float(conf),
                                       {'trend_up': trend_up})

        # 4. XMM 信号（徐小明三周期）
        xmm_action, xmm_conf, xmm_result = 'HOLD', 0.50, {}
        if self.xmm_engine:
            try:
                xmm_result = self.xmm_engine.analyze(df)
                xmm_action = xmm_result['signal']
                xmm_conf = {3: 0.85, 2: 0.72, 1: 0.60, 0: 0.50}.get(xmm_result.get('strength', 0), 0.50)
            except Exception as e:
                import traceback
                print(f'  [WARN] XMM引擎异常: {e}')
                traceback.print_exc()

        xmm_sig = None
        if XMMSignal:
            sma20 = np.full(len(close), np.nan)
            for i in range(19, len(close)):
                sma20[i] = sma20[i - 1] + (close[i] - sma20[i - 1]) / 20
            sma_pos = float(close[-1] / sma20[-1]) if not np.isnan(sma20[-1]) else 1.0

            xmm_sig = XMMSignal(symbol, date, xmm_action, xmm_conf,
                              float(rsi_d[-1]), sma_pos,
                              float(xmm_result.get('td9_count', 0)),
                              str(xmm_result.get('reason', '')), '',
                              str(xmm_result.get('macd_desc', '')),
                              macd_structure_desc=str(
                                  xmm_result.get('macd_structure', {}).get('state', '')
                              ),
                              macd_structure_level=xmm_result.get('macd_structure_level', 0))

        # 5. LLM 因子打分
        llm_factor_scores = {}
        llm_factor_score = 0.0
        llm_factor_summary = ''
        if score_factors and factor_to_signal_score and get_factor_summary:
            try:
                market_label = self._get_market_label(symbol)
                llm_factor_scores = score_factors(df, market_label)
                llm_factor_score = factor_to_signal_score(llm_factor_scores)
                llm_factor_summary = get_factor_summary(llm_factor_scores)
            except Exception as e:
                import traceback
                print(f'  [WARN] LLM因子打分异常: {e}')
                traceback.print_exc()

        # 5.6 v2.2: EventDetector（情绪/事件检测，研究层 LLM 高自由度）
        event_result = {'sentiment_score': 0.0, 'event_type': 'none', 'event_summary': '', 'confidence': 0.0}
        if self.event_detector:
            try:
                event_result = self.event_detector.analyze_sentiment(
                    symbol=symbol,
                    price_data={'close': close.tolist(), 'high': high.tolist(),
                                'low': low.tolist(), 'volume': df['volume'].values.astype(float).tolist()},
                    market_state=market_state,
                )
            except Exception as e:
                import traceback
                print(f'  [WARN] EventDetector 异常: {e}')
                traceback.print_exc()

        # 6. 框架融合（含 market_state + event_score — v2.2 三路融合）
        fusion_level, fusion_score, fusion_confidence, fusion_position = 'HOLD', 0.0, 0.5, 0.0
        fusion_risk, fusion_warnings, fusion_reasoning = 'MEDIUM', [], ''

        if self.fw_engine and fm_sig and xmm_sig:
            try:
                fusion = self.fw_engine.fuse(
                    fm_sig, xmm_sig, market, llm_factor_score, llm_factor_summary,
                    market_state=market_state,
                    event_sentiment_score=event_result.get('sentiment_score', 0.0),
                    event_summary=event_result.get('event_summary', ''),
                )
                fusion_level = fusion.level.value
                fusion_score = fusion.score
                fusion_confidence = fusion.confidence
                fusion_position = fusion.position_pct
                fusion_risk = fusion.risk_level
                fusion_warnings = [str(w) for w in fusion.warnings] if fusion.warnings else []
                fusion_reasoning = fusion.reasoning
            except Exception as e:
                import traceback
                print(f'!!! [CRITICAL] FusionEngine 熔断器异常崩溃: {e}')
                traceback.print_exc()
                raise  # 纸交易阶段熔断异常应立刻暴露，不允许静默降级

        # 6.5 v2.2: HardGate 硬门槛检查（决策层纯规则，零 LLM）
        gate_passed = True
        gate_reason = ''
        if self.hard_gate:
            try:
                signal_dict = {
                    'symbol': symbol, 'fusion_level': fusion_level,
                    'fusion_score': fusion_score, 'fusion_confidence': fusion_confidence,
                    'llm_factor_score': llm_factor_score,
                    'event_sentiment_score': event_result.get('sentiment_score', 0.0),
                    'event_confidence': event_result.get('confidence', 0.0),
                }
                gate_result = self.hard_gate.check(signal_dict, market_state)
                if hasattr(gate_result, 'passed'):
                    gate_passed = gate_result.passed
                    gate_reason = getattr(gate_result, 'reason', '')
                elif isinstance(gate_result, dict):
                    gate_passed = gate_result.get('passed', True)
                    gate_reason = gate_result.get('reason', '')
            except Exception as e:
                import traceback
                print(f'  [WARN] HardGate 异常: {e}')
                traceback.print_exc()

        # 位置上限（基于 market_state）
        if not gate_passed and fusion_level in ('BUY', 'STRONG_BUY'):
            fusion_level = 'HOLD'
            fusion_position = 0.0
            fusion_warnings.append(f'HardGate拦截: {gate_reason}')

        return {
            'symbol': symbol,
            'market': market,
            'date': date,
            'close': last_close,
            'data_source': source,
            'fusion_level': fusion_level,
            'fusion_score': round(fusion_score, 1),
            'fusion_confidence': round(fusion_confidence, 3),
            'target_position': round(fusion_position, 3),
            'risk': fusion_risk,
            'warnings': fusion_warnings,
            'reasoning': fusion_reasoning,
            'fm_signal': sig,
            'fm_score': round(score, 2),
            'xmm_signal': xmm_action,
            'xmm_confidence': round(xmm_conf, 3),
            'rsi_daily': round(float(rsi_d[-1]), 1),
            'rsi_weekly': round(float(w_rsi), 1),
            'rsi_monthly': round(float(m_rsi), 1),
            'resonance': bool(xmm_result.get('resonance', False)),
            'td9_count': xmm_result.get('td9_count', 0),
            'macd_desc': str(xmm_result.get('macd_desc', '')),
            'macd_structure_desc': str(
                xmm_result.get('macd_structure', {}).get('state', '')
            ),
            'macd_structure_level': xmm_result.get('macd_structure_level', 0),
            'fractal_type': ftype,
            'top_count': top_cnt,
            'bottom_count': bot_cnt,
            'trend_up': bool(trend_up),
            'stop_levels': {
                'fixed': round(last_close * 0.88, 2),
                'trailing_pct': -0.12,
                'dd_limit': -0.30,
            },
            'llm_factor_score': round(llm_factor_score, 1),
            'llm_factor_summary': llm_factor_summary,
            'llm_factor_details': llm_factor_scores,
            # v2.2 新增字段
            'market_state': market_state,
            'event_sentiment_score': event_result.get('sentiment_score', 0.0),
            'event_type': event_result.get('event_type', 'none'),
            'event_summary': event_result.get('event_summary', ''),
            'event_confidence': event_result.get('confidence', 0.0),
            'hard_gate_passed': gate_passed,
            'hard_gate_reason': gate_reason,
        }

    def scan_market(self, symbols, market='HK', market_state='CRAB'):
        """批量扫描一个市场的所有标的。
        
        Args:
            symbols: 标准符号列表
            market: 'HK' 或 'US'
            market_state: 市场状态 (BULL/BEAR/CRAB/RECOVERY/CORRECTION)
        
        Returns:
            list of dict: 有效信号结果列表
        """
        results = []
        for i, sym in enumerate(symbols):
            print(f'  Analyzing {sym}...', end=' ', flush=True)
            try:
                result = self.analyze(sym, market, market_state)
                if result:
                    results.append(result)
                    src = result.get('data_source', '?')
                    print(f'-> {result["fusion_level"]} ({result["fusion_score"]:+.1f}) [{src}]')
                else:
                    print('-> FAILED (数据不足)')
            except Exception as e:
                print(f'-> FAILED ({type(e).__name__}: {e})')
            # TickFlow 限流保护：港股每个标的间隔 5s，美股 2s
            if i < len(symbols) - 1:
                delay = 5 if market == 'HK' else 2
                time.sleep(delay)
        return results

    @staticmethod
    def _get_market_label(symbol):
        """从标准符号提取 LLM 因子打分用的市场标签。"""
        if symbol.endswith('.US'):
            base = symbol.replace('.US', '')
            return base if base == 'SPY' else base
        if symbol.endswith('.HK'):
            return symbol.replace('.HK', '')
        return symbol
