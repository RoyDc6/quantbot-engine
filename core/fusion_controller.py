# -*- coding: utf-8 -*-
"""
core/fusion_controller.py — QuantBot 战术中控台

缝合三驾马车（XMM + VP + LLM）的完整管线：
  data → XMM → VP → LLM → normalize → FusionEngine → HardGate → OrderDirective

架构定位：决策层上层，调度所有信号源，输出标准化的执行指令。
         研究层(LLM) → 战术中控台(本模块) → 执行层(纯规则)
"""

import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

import numpy as np
import pandas as pd

# ─── 路径注入 ──────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent.parent  # E:\quant
XMM_PATH = str(BASE / 'xmm-strategy')
for p in [str(BASE), XMM_PATH]:
    if p not in sys.path:
        sys.path.insert(0, p)

warnings.filterwarnings('ignore')

# ─── 三驾马车导入（各模块独立 try-catch，任一失败不影响其他） ──

# 1. XMM Strategy
try:
    from modules.engine import XMMStrategy
    XMM_AVAILABLE = True
except ImportError:
    XMMStrategy = None
    XMM_AVAILABLE = False

# 2. Volume Profile
try:
    from fusion_framework.volume_profile import VolumeProfileBoxStrategy
    VP_AVAILABLE = True
except ImportError:
    VolumeProfileBoxStrategy = None
    VP_AVAILABLE = False

# 3. LLM Bias Model
try:
    from core.models.llm_bias_model import LLMBiasModel
    LLM_AVAILABLE = True
except ImportError:
    LLMBiasModel = None
    LLM_AVAILABLE = False

# 4. FusionEngine
try:
    from fusion_framework.fusion_engine import FusionEngine
    ENGINE_AVAILABLE = True
except ImportError:
    FusionEngine = None
    ENGINE_AVAILABLE = False

# 5. HardGate
try:
    from fusion_framework.hard_gate import HardGate, GateConfig
    GATE_AVAILABLE = True
except ImportError:
    HardGate = GateConfig = None
    GATE_AVAILABLE = False

# 6. Signal types
try:
    from fusion_framework.signal_types import DecisionSignal, OrderDirective
    TYPES_AVAILABLE = True
except ImportError:
    DecisionSignal = OrderDirective = None
    TYPES_AVAILABLE = False

# 7. UniverseManager
from core.universe_manager import UniverseManager

# 8. AdapterFactory（统一数据源入口）
from core.adapter_factory import AdapterFactory


# ═══════════════════════════════════════════════════════════════
# 静态绝对权重（战术指挥部终极锚定）
# 主攻手 XMM 60% | 阵地盾 VP 25% | 侦察兵 LLM 15%
# 此权重为硬编码常量，不随市场状态变化。
# 断流时：异常源权重归零，剩余存活源按比例重新归一化。
# ═══════════════════════════════════════════════════════════════

BASE_WEIGHTS: Dict[str, float] = {
    'xmm': 0.60,
    'vp': 0.25,
    'llm': 0.15,
}


# ═══════════════════════════════════════════════════════════════
# 信号源状态标记
# ═══════════════════════════════════════════════════════════════

class SourceStatus:
    """信号源运行状态。"""
    OK = 'OK'
    SKIPPED = 'SKIPPED'   # 未配置/未启用
    ERROR = 'ERROR'       # 执行异常
    NO_DATA = 'NO_DATA'   # 数据不足
    NEUTRAL = 'NEUTRAL'   # 返回中性信号


# ═══════════════════════════════════════════════════════════════
# FusionController 主类
# ═══════════════════════════════════════════════════════════════

class FusionController:
    """
    战术中控台 — 三驾马车缝合管线。

    用法:
        fc = FusionController()
        result = fc.analyze_ticker('00700.HK', market_state='BULL')
        report = fc.scan_market('HK')
    """

    def __init__(self, config: dict = None):
        """
        Args:
            config: 可选的覆盖配置，支持:
                - 'futu_host' / 'futu_port': Futu 连接参数
                - 'llm_model': LLM 模型名
                - 'vp_lookback' / 'vp_bins': VP 参数
        """
        self.config = config or {}
        # 权重硬编码为常量 BASE_WEIGHTS，不由 config 覆盖

        # 初始化底层基建
        self.universe = UniverseManager()
        # 不再直接持有一个固定适配器 — 通过 AdapterFactory 按 ticker 分发
        # 见 _fetch_kline()

        # 三驾马车（懒加载，使用前才实例化）
        self._xmm = None
        self._vp = None
        self._llm = None

        # FusionEngine + HardGate
        self._engine = None
        self._gate = None

        # 状态缓存
        self._status = {}
        self._last_scan = None

    # ─── 懒加载 ────────────────────────────────────────────
    @property
    def xmm(self):
        if self._xmm is None and XMM_AVAILABLE and XMMStrategy:
            self._xmm = XMMStrategy()
        return self._xmm

    @property
    def vp(self):
        if self._vp is None and VP_AVAILABLE and VolumeProfileBoxStrategy:
            self._vp = VolumeProfileBoxStrategy()
        return self._vp

    @property
    def llm(self):
        if self._llm is None and LLM_AVAILABLE and LLMBiasModel:
            model_name = self.config.get('llm_model', 'mistralai/mixtral-8x7b-instruct-v0.1')
            self._llm = LLMBiasModel(model_name=model_name)
        return self._llm

    @property
    def engine(self):
        if self._engine is None and ENGINE_AVAILABLE and FusionEngine:
            self._engine = FusionEngine(self.config)
        return self._engine

    @property
    def gate(self):
        if self._gate is None and GATE_AVAILABLE and HardGate:
            gate_config = GateConfig() if GateConfig else None
            self._gate = HardGate(gate_config)
        return self._gate

    # ═══════════════════════════════════════════════════════════
    # 核心接口
    # ═══════════════════════════════════════════════════════════

    def analyze_ticker(self, ticker: str,
                       market_state: str = 'CRAB',
                       vix_regime: str = 'CANDIDATE',
                       vix_data: dict = None,
                       force_llm: bool = False,
                       run_xmm: bool = True,
                       run_vp: bool = True,
                       run_llm: bool = True
                       ) -> dict:
        """
        单标的完整管线分析。

        Args:
            ticker: 标准符号 '00700.HK' / 'US.AAPL'
            market_state: 市场状态 BULL/BEAR/CRAB/RECOVERY/CORRECTION
            vix_regime: VIX 状态 SUPPRESSED/CANDIDATE/RELEASED
            vix_data: 可选 VIX 数据 {date: value}
            force_llm: 强制重新运行 LLM（跳过缓存）
            run_xmm / run_vp / run_llm: 各信号源开关

        Returns:
            dict: 完整信号报告（含三驾马车原始输出 + 融合 + 硬门槛结果）
        """
        results = {
            'ticker': ticker,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'market_state': market_state,
            'vix_regime': vix_regime,
            'close': 0.0,
            'data_source': '',
            'sources': {},   # 三驾马车各自输出
            'status': {},    # 各源状态
            'fusion': {},    # 融合结果
            'gate': {},      # 硬门槛结果
            'directive': {}, # 最终指令
            'warnings': [],
            'errors': [],
        }

        # 1. 获取 K 线数据
        df = self._fetch_kline(ticker)
        if df is None or len(df) < 50:
            results['errors'].append(f'数据不足: {len(df) if df is not None else 0}条')
            results['directive'] = {
                'action': 'HOLD',
                'reason': '数据不足',
                'target_pct': 0.0,
            }
            return results

        results['close'] = float(df['close'].iloc[-1])
        results['data_source'] = df.attrs.get('source', 'Futu')

        # ─── 数据新鲜度检测 ────────────────────────────────────
        stale_days = df.attrs.get('stale_days', 0)
        if stale_days > 0:
            results['stale_days'] = stale_days
            msg = f'K线数据滞后{stale_days}天'
            if stale_days > 30:
                # 严重过期：全信号强制 HOLD，不做任何判断
                results['errors'].append(f'数据严重过期({stale_days}天)，信号暂停')
                results['directive'] = {
                    'action': 'HOLD',
                    'reason': f'数据严重过期({stale_days}天)，信号不可信',
                    'target_pct': 0.0,
                }
                return results
            elif stale_days > 3:
                results['warnings'].append(f'{msg}，XMM/VP信号置信度减半')

        close = df['close'].values.astype(float)
        date = results['date']

        # 预计算 RSI 供下游消费（daily_runner 等需要显示）
        results['_closes'] = close.tolist()
        results['_highs'] = df['high'].values.astype(float).tolist()
        results['_lows'] = df['low'].values.astype(float).tolist()
        results['rsi_daily'] = round(float(_rsi(close, 14)[-1]), 1)
        _wr = _weekly_rsi(close)
        results['rsi_weekly'] = round(float(_wr), 1) if _wr is not None else 50.0

        # 2. 运行三驾马车
        weights = self._get_weights()

        # 2a. XMM
        xmm_result = {}
        xmm_status = SourceStatus.SKIPPED
        if run_xmm and self.xmm:
            try:
                xmm_raw = self.xmm.analyze(df)
                if xmm_raw and isinstance(xmm_raw, dict):
                    xmm_result = {
                        'action': xmm_raw.get('signal', 'HOLD'),
                        'position_size': xmm_raw.get('position_size', 0.0),
                        'signal_type': xmm_raw.get('signal_type', ''),
                        'reason': xmm_raw.get('reason', ''),
                        'strength': xmm_raw.get('strength', 0),
                        'net_score': xmm_raw.get('net_score', 0),
                        'td_count': xmm_raw.get('td_count', 0),
                        'td_amplified': xmm_raw.get('td_amplified', False),
                        'trend': xmm_raw.get('trend_layer', {}).get('market', 'UNKNOWN'),
                    }
                    xmm_status = SourceStatus.OK
                else:
                    xmm_status = SourceStatus.NO_DATA
            except Exception as e:
                xmm_status = SourceStatus.ERROR
                results['errors'].append(f'XMM: {e}')
        results['sources']['xmm'] = xmm_result
        results['status']['xmm'] = xmm_status

        # 2b. Volume Profile
        vp_result = {}
        vp_status = SourceStatus.SKIPPED
        if run_vp and self.vp:
            try:
                vp_signal = self.vp.analyze(df, ticker=ticker)
                if vp_signal and isinstance(vp_signal, DecisionSignal if TYPES_AVAILABLE else object):
                    vp_result = {
                        'direction': vp_signal.direction,
                        'confidence': vp_signal.confidence,
                        'signal_level': vp_signal.signal_level,
                        'state': vp_signal.factor_details.get('state', 'inside_box'),
                        'vah': vp_signal.factor_details.get('VAH', 0),
                        'val': vp_signal.factor_details.get('VAL', 0),
                        'poc': vp_signal.factor_details.get('POC', 0),
                        'va_width': vp_signal.factor_details.get('va_width', 0),
                    }
                    vp_status = SourceStatus.OK
                else:
                    vp_status = SourceStatus.NO_DATA
            except Exception as e:
                vp_status = SourceStatus.ERROR
                results['errors'].append(f'VP: {e}')
        results['sources']['vp'] = vp_result
        results['status']['vp'] = vp_status

        # 2c. LLM Bias Model
        llm_result = {}
        llm_status = SourceStatus.SKIPPED
        if run_llm and self.llm:
            try:
                kline_dict = {
                    'close': close.tolist(),
                    'high': df['high'].values.astype(float).tolist(),
                    'low': df['low'].values.astype(float).tolist(),
                    'volume': df['volume'].values.astype(float).tolist(),
                }
                llm_signal = self.llm.generator(
                    ticker=ticker,
                    kline_data=kline_dict,
                    market_state=market_state,
                    force=force_llm,
                )
                if llm_signal and isinstance(llm_signal, DecisionSignal if TYPES_AVAILABLE else object):
                    llm_result = {
                        'sentiment_score': llm_signal.event_sentiment_score,
                        'confidence': llm_signal.event_confidence,
                        'signal_level': llm_signal.signal_level,
                        'event_type': llm_signal.event_type,
                        'event_summary': llm_signal.event_summary,
                        'warnings': llm_signal.warnings if hasattr(llm_signal, 'warnings') else [],
                    }
                    llm_status = SourceStatus.OK
                else:
                    llm_status = SourceStatus.NO_DATA
            except Exception as e:
                llm_status = SourceStatus.ERROR
                results['errors'].append(f'LLM: {e}')
        results['sources']['llm'] = llm_result
        results['status']['llm'] = llm_status

        # 3. 检查各源有效状态
        active_weights = self._compute_active_weights(weights, results['status'])

        # 4. 融合信号
        fusion = self._fuse_signals(xmm_result, vp_result, llm_result,
                                    active_weights, date, ticker)

        # ─── 数据滞后的置信度惩罚 ────────────────────────────
        stale_days = results.get('stale_days', 0)
        if 3 < stale_days <= 30:
            fusion['confidence'] *= 0.5  # 置信度减半
            fusion['warnings'] = fusion.get('warnings', []) + [f'数据滞后{stale_days}天，置信度减半']
            results['warnings'].append(f'数据滞后{stale_days}天，信号置信度减半')
            # 分数也按比例降低（滞后越长折扣越大）
            decay = max(0.5, 1.0 - stale_days * 0.02)
            fusion['score'] = round(fusion['score'] * decay, 1)
            fusion['level'] = self._score_to_level(fusion['score'])
            fusion['position_pct'] = self._level_to_position(fusion['level'])

        results['fusion'] = fusion

        # 5. HardGate 硬门槛检查
        gate_result = self._check_gate(ticker, fusion, market_state, vix_regime)
        results['gate'] = gate_result

        # 6. 生成最终指令
        directive = self._build_directive(ticker, fusion, gate_result, active_weights)
        results['directive'] = directive

        # 7. 最终警告
        if gate_result.get('reject_reasons'):
            results['warnings'].extend(gate_result['reject_reasons'])
        if fusion.get('warnings'):
            results['warnings'].extend(fusion['warnings'])
        if xmm_status == SourceStatus.ERROR:
            results['warnings'].append('XMM策略异常降级')
        if vp_status == SourceStatus.ERROR:
            results['warnings'].append('VP策略异常降级')
        if llm_status == SourceStatus.ERROR:
            results['warnings'].append('LLM模型异常降级')

        return results

    def scan_market(self, market: str = 'HK',
                    market_state: str = 'CRAB',
                    vix_regime: str = 'CANDIDATE',
                    vix_data: dict = None,
                    max_tickers: int = None,
                    force_llm: bool = False,
                    sort_by: str = 'fusion_score') -> List[dict]:
        """
        批量扫描一个市场所有标的。

        Args:
            market: 'HK' 或 'US'
            market_state: 市场状态
            vix_regime: VIX 状态
            vix_data: VIX 数据
            max_tickers: 最多扫描数量
            force_llm: 强制重跑 LLM
            sort_by: 排序字段 'fusion_score' / 'ticker' / 'confidence'

        Returns:
            List[dict]: 按 fusion_score 降序排列的信号报告列表
        """
        symbols = self.universe.get_symbols_by_market(market)
        if max_tickers:
            symbols = symbols[:max_tickers]

        print(f'🔄 FusionController 扫描 {market} 市场 ({len(symbols)} 标的)...')
        print(f'   市场状态: {market_state} | VIX: {vix_regime}')
        print()

        results = []
        for i, sym in enumerate(symbols):
            name = self.universe.get_name(sym)
            print(f'  [{i+1}/{len(symbols)}] {sym} ({name})...', end=' ', flush=True)
            try:
                result = self.analyze_ticker(
                    ticker=sym,
                    market_state=market_state,
                    vix_regime=vix_regime,
                    vix_data=vix_data,
                    force_llm=force_llm,
                )
                results.append(result)
                fusion = result.get('fusion', {})
                gate = result.get('gate', {})
                print(f'→ {fusion.get("level", "HOLD")} (score={fusion.get("score", 0):+.1f})'
                      f'  gate={gate.get("approved", False)}')
            except Exception as e:
                print(f'→ FAILED: {e}')
                results.append({
                    'ticker': sym,
                    'date': datetime.now().strftime('%Y-%m-%d'),
                    'directive': {'action': 'HOLD', 'reason': f'扫描异常: {e}'},
                })

            # 限流保护
            if i < len(symbols) - 1:
                delay = 5 if market == 'HK' else 2
                time.sleep(delay)

        # 排序
        if sort_by == 'fusion_score':
            results.sort(key=lambda r: r.get('fusion', {}).get('score', 0), reverse=True)
        elif sort_by == 'confidence':
            results.sort(key=lambda r: r.get('fusion', {}).get('confidence', 0), reverse=True)

        self._last_scan = {
            'market': market,
            'time': datetime.now().isoformat(),
            'count': len(results),
            'results': results,
        }
        return results

    def daily_report(self, market: str = 'all') -> dict:
        """
        生成日报摘要。

        Returns:
            dict: 含信号统计、三驾马车贡献、持仓建议等
        """
        report = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'markets': {},
            'summary': {},
        }

        markets_to_scan = ['HK', 'US'] if market == 'all' else [market]
        for mkt in markets_to_scan:
            state = self._detect_market_state(mkt) if mkt == 'US' else 'CRAB'
            vix_regime = self._detect_vix_regime()
            results = self.scan_market(mkt, market_state=state, vix_regime=vix_regime)

            # 统计
            buy_count = sum(1 for r in results
                          if r.get('directive', {}).get('action') == 'BUY')
            sell_count = sum(1 for r in results
                           if r.get('directive', {}).get('action') == 'SELL')
            hold_count = sum(1 for r in results
                           if r.get('directive', {}).get('action') == 'HOLD')
            blocked_count = sum(1 for r in results
                              if r.get('directive', {}).get('action') == 'BLOCKED')

            report['markets'][mkt] = {
                'market_state': state,
                'vix_regime': vix_regime,
                'total': len(results),
                'buy': buy_count,
                'sell': sell_count,
                'hold': hold_count,
                'blocked': blocked_count,
                'results': results,
            }

        report['summary'] = {
            'total_hk': report['markets'].get('HK', {}).get('total', 0),
            'total_us': report['markets'].get('US', {}).get('total', 0),
            'total_buy': sum(m.get('buy', 0) for m in report['markets'].values()),
            'total_sell': sum(m.get('sell', 0) for m in report['markets'].values()),
            'total_blocked': sum(m.get('blocked', 0) for m in report['markets'].values()),
        }

        return report

    # ═══════════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════════

    def _fetch_kline(self, ticker: str) -> Optional[pd.DataFrame]:
        """获取 K 线数据。

        通过 AdapterFactory 按 ticker 类型自动选择适配器：
          港股/美股 → FutuAdapter  |  Crypto → CryptoAdapter
        """
        try:
            adapter = AdapterFactory.get_adapter(ticker)
            df = adapter.fetch_kline(ticker, count=252)
            # ─── 数据新鲜度告警 ────────────────────────────────
            if df is not None and len(df) > 0:
                stale_days = df.attrs.get('stale_days', 0)
                if stale_days > 30:
                    print(f'  [EXPIRED] {ticker} K线滞后{stale_days}天，数据可能已停止同步')
                elif stale_days > 3:
                    print(f'  [STALE] {ticker} K线滞后{stale_days}天')
            return df
        except Exception:
            return None

    def _get_weights(self) -> Dict[str, float]:
        """返回静态 BASE_WEIGHTS（60/25/15，不随市场状态变化）。"""
        return dict(BASE_WEIGHTS)

    def _compute_active_weights(self, weights: Dict[str, float],
                                status: Dict[str, str]) -> Dict[str, float]:
        """
        根据各源实际运行状态调整权重。
        异常源 → 权重降为 0，其他源按比例重新分配。
        """
        active = {}
        total = 0.0

        for src in ('xmm', 'vp', 'llm'):
            s = status.get(src, SourceStatus.SKIPPED)
            if s in (SourceStatus.OK, SourceStatus.NEUTRAL):
                active[src] = weights.get(src, 0.0)
                total += active[src]
            else:
                active[src] = 0.0

        # 归一化
        if total > 0:
            for src in active:
                active[src] /= total
        else:
            # 全部失效 → 全 0
            for src in active:
                active[src] = 0.0

        return active

    def _fuse_signals(self, xmm: dict, vp: dict, llm: dict,
                      weights: Dict[str, float],
                      date: str, ticker: str) -> dict:
        """
        三路信号融合（不依赖外部 FusionEngine 的简化版本）。

        融合策略:
          score = xmm_factor × w_xmm + vp_factor × w_vp + llm_factor × w_llm
        """
        # 各源转信号分
        xmm_factor = self._xmm_to_score(xmm)
        vp_factor = self._vp_to_score(vp)
        llm_factor = self._llm_to_score(llm)

        wx = weights.get('xmm', 0.0)
        wv = weights.get('vp', 0.0)
        wl = weights.get('llm', 0.0)
        total_w = wx + wv + wl

        if total_w <= 0:
            return {
                'level': 'HOLD', 'score': 0.0, 'confidence': 0.0,
                'position_pct': 0.0, 'risk_level': 'MEDIUM',
                'warnings': ['所有信号源均不可用'],
                'reasoning': '无有效信号源',
                'weights_used': {'xmm': wx, 'vp': wv, 'llm': wl},
            }

        score = (xmm_factor * wx + vp_factor * wv + llm_factor * wl) / total_w

        # 置信度（各源置信度的加权平均）
        xmm_conf = xmm.get('position_size', 0.0) if xmm.get('action') != 'HOLD' else 0.3
        vp_conf = vp.get('confidence', 0.0)
        llm_conf = llm.get('confidence', 0.0)
        confidence = (xmm_conf * wx + vp_conf * wv + llm_conf * wl) / max(total_w, 0.01)

        # 信号等级映射
        level = self._score_to_level(score)

        # 仓位映射
        position_pct = self._level_to_position(level)

        # 风险等级
        risk_level = self._assess_risk(score, confidence, xmm, vp, llm)

        # 警告
        warnings = []
        if abs(score) < 15:
            warnings.append('融合得分低')
        if confidence < 0.4:
            warnings.append('综合置信度低')
        if abs(xmm_factor) > 50 and abs(vp_factor) > 50 and xmm_factor * vp_factor < 0:
            warnings.append('XMM与VP信号矛盾')
        if abs(llm_factor) > 60:
            warnings.append(f'LLM情绪极端 ({llm_factor:+.0f})')

        # 推理
        reasoning = self._build_reasoning(xmm, vp, llm, score, level)

        return {
            'level': level,
            'score': round(score, 1),
            'confidence': round(confidence, 3),
            'position_pct': round(position_pct, 3),
            'risk_level': risk_level,
            'warnings': warnings,
            'reasoning': reasoning,
            'weights_used': {'xmm': wx, 'vp': wv, 'llm': wl},
            'raw_scores': {
                'xmm': round(xmm_factor, 1),
                'vp': round(vp_factor, 1),
                'llm': round(llm_factor, 1),
            },
        }

    def _check_gate(self, ticker: str, fusion: dict,
                    market_state: str, vix_regime: str) -> dict:
        """HardGate 硬门槛检查。"""
        if not self.gate:
            return {
                'approved': True,
                'action': fusion.get('level', 'HOLD'),
                'reject_reasons': [],
                'adjusted_position': fusion.get('position_pct', 0.0),
            }

        # 构造 signal dict 供 HardGate 消费
        signal_dict = {
            'symbol': ticker,
            'fusion_level': fusion.get('level', 'HOLD'),
            'fusion_score': fusion.get('score', 0),
            'fusion_confidence': fusion.get('confidence', 0),
            'target_position': fusion.get('position_pct', 0),
            'llm_factor_score': fusion.get('raw_scores', {}).get('llm', 0),
            'event_sentiment_score': 0,
            'event_confidence': 0,
        }

        try:
            gate_result = self.gate.check(
                signal=signal_dict,
                market_state=market_state,
                vix_regime=vix_regime,
            )
            if hasattr(gate_result, 'approved'):
                return {
                    'approved': gate_result.approved,
                    'action': gate_result.action,
                    'reject_reasons': gate_result.reject_reasons,
                    'adjusted_position': gate_result.adjusted_position_pct,
                    'warnings': gate_result.warnings,
                }
            elif isinstance(gate_result, dict):
                return gate_result
        except Exception as e:
            pass

        return {
            'approved': True,
            'action': fusion.get('level', 'HOLD'),
            'reject_reasons': [],
            'adjusted_position': fusion.get('position_pct', 0.0),
        }

    def _build_directive(self, ticker: str, fusion: dict,
                         gate: dict, weights: dict) -> dict:
        """生成最终执行指令。"""
        if not gate.get('approved', True):
            return {
                'action': 'BLOCKED',
                'reason': '; '.join(gate.get('reject_reasons', ['HardGate拦截'])),
                'target_pct': 0.0,
            }

        level = fusion.get('level', 'HOLD')
        position = min(
            gate.get('adjusted_position', fusion.get('position_pct', 0)),
            fusion.get('position_pct', 0),
        )

        # BUY/SELL 映射
        action_map = {
            'STRONG_BUY': 'BUY',
            'BUY': 'BUY',
            'HOLD': 'HOLD',
            'SELL': 'SELL',
            'STRONG_SELL': 'SELL',
            'REDUCED': 'SELL',
        }

        return {
            'action': action_map.get(level, 'HOLD'),
            'level': level,
            'target_pct': round(position, 3),
            'reason': fusion.get('reasoning', ''),
            'confidence': fusion.get('confidence', 0),
            'weights_used': weights,
        }

    # ═══ 信号转换工具 ═══════════════════════════════════════

    @staticmethod
    def _xmm_to_score(xmm: dict) -> float:
        """XMM 信号 → 数值分 [-100, +100]"""
        action = xmm.get('action', 'HOLD')
        pos = xmm.get('position_size', 0.0)
        strength = xmm.get('strength', 0)

        if action == 'BUY':
            base = 50.0 + strength * 15
            return min(base + pos * 30, 100)
        elif action == 'SELL':
            base = -50.0 - strength * 15
            return max(base - pos * 30, -100)
        return 0.0

    @staticmethod
    def _vp_to_score(vp: dict) -> float:
        """VP 信号 → 数值分 [-100, +100]"""
        direction = vp.get('direction', 'HOLD')
        conf = vp.get('confidence', 0.0)
        if direction == 'BUY':
            return min(conf * 100, 80)
        elif direction == 'SELL':
            return max(-conf * 100, -80)
        return 0.0

    @staticmethod
    def _llm_to_score(llm: dict) -> float:
        """LLM 信号 → 数值分 [-100, +100]"""
        sent = llm.get('sentiment_score', 0.0)
        return float(sent)

    @staticmethod
    def _score_to_level(score: float) -> str:
        """数值分 → 信号等级。"""
        if score >= 60:
            return 'STRONG_BUY'
        elif score >= 30:
            return 'BUY'
        elif score <= -60:
            return 'STRONG_SELL'
        elif score <= -30:
            return 'SELL'
        elif abs(score) < 10:
            return 'HOLD'
        return 'REDUCED'

    @staticmethod
    def _level_to_position(level: str) -> float:
        """信号等级 → 仓位比例。"""
        pos_map = {
            'STRONG_BUY': 0.80,
            'BUY': 0.50,
            'HOLD': 0.00,
            'SELL': 0.00,
            'STRONG_SELL': 0.00,
            'REDUCED': 0.20,
        }
        return pos_map.get(level, 0.0)

    @staticmethod
    def _assess_risk(score: float, confidence: float,
                     xmm: dict, vp: dict, llm: dict) -> str:
        """风险评估。"""
        high_risk = False
        medium_risk = False

        if confidence < 0.3:
            high_risk = True
        elif confidence < 0.5:
            medium_risk = True

        if abs(score) > 80:
            medium_risk = True

        if llm.get('sentiment_score', 0) > 80 or llm.get('sentiment_score', 0) < -80:
            medium_risk = True

        if high_risk:
            return 'HIGH'
        elif medium_risk:
            return 'MEDIUM'
        return 'LOW'

    @staticmethod
    def _build_reasoning(xmm: dict, vp: dict, llm: dict,
                         score: float, level: str) -> str:
        """生成推理摘要。"""
        parts = []
        parts.append(f'融合={level}({score:+.1f})')

        if xmm.get('action') != 'HOLD':
            parts.append(f'XMM={xmm["action"]}({xmm.get("position_size", 0):.0%})')
            if xmm.get('reason'):
                parts.append(f'[{xmm["reason"][:20]}]')
        elif xmm.get('action') == 'HOLD':
            pass  # 中性信号不报

        if vp.get('direction') != 'HOLD':
            parts.append(f'VP={vp.get("state", "?")}→{vp["direction"]}')
        elif vp.get('state') == 'inside_box':
            parts.append('VP箱体内')

        if llm.get('sentiment_score', 0) != 0:
            sent = llm.get('sentiment_score', 0)
            parts.append(f'LLM偏向={sent:+.0f}')
            if llm.get('event_summary'):
                parts.append(f'[{llm["event_summary"][:30]}]')

        return ' | '.join(parts) if parts else '无有效信号'

    # ═══ 市场状态辅助 ═══════════════════════════════════════

    @staticmethod
    def _detect_market_state(market: str) -> str:
        """简易市场状态检测。"""
        try:
            from market_state.classifier import MarketStateClassifier
            clf = MarketStateClassifier()
            return clf.classify()
        except Exception:
            return 'CRAB'

    @staticmethod
    def _detect_vix_regime() -> str:
        """简易 VIX regime 检测。直接从缓存文件读取。"""
        try:
            from pathlib import Path
            from datetime import datetime
            import json
            p = Path(__file__).resolve().parent.parent / 'scanner' / 'cache' / 'VXX_US.json'
            if not p.exists():
                return 'CANDIDATE'
            with open(p, encoding='utf-8') as f:
                vxx = json.load(f)
            if not vxx:
                return 'CANDIDATE'
            dates = sorted([datetime.fromtimestamp(r['date'] / 1000).strftime('%Y-%m-%d')
                           for r in vxx])
            if len(dates) >= 2:
                vix_map = {datetime.fromtimestamp(r['date'] / 1000).strftime('%Y-%m-%d'): float(r['close'])
                          for r in vxx}
                latest = vix_map[dates[-1]]
                if latest >= 35:
                    return 'SUPPRESSED'
                elif latest <= 22:
                    return 'RELEASED'
            return 'CANDIDATE'
        except Exception:
            return 'CANDIDATE'

    # ─── 战术仪表板渲染 ───────────────────────────────────────
    def render_tactical_dashboard(self, signals: List[Dict], market_state: str = 'N/A',
                                  vix_regime: str = 'N/A', date: str = None) -> str:
        """
        将 DecisionSignal 列表渲染为 ASCII 战术仪表板。

        Args:
            signals: 每日信号的 dict 列表（每个 dict 必须含 symbol/fusion_level/fusion_score
                     fusion_confidence/_gate_reasons 等字段）
            market_state: 市场状态（BULL/BEAR/CRAB）
            vix_regime: VIX 环境
            date: 日期字符串

        标记规则:
            [---▲---]  — 得分 > 20（多头强度）
            [!!!X!!!]  — HardGate 熔断
            [▶··neutral·▶] — HOLD 无事件
            [---▼---]  — 强烈看空（得分 < -20）
        """
        if date is None:
            date = self._last_scan.get('date', 'N/A') if self._last_scan else 'N/A'

        lines = []
        # 各列宽度: 标记(13) + 标的(10) + 信号(10) + 得分(6) + 置信度(6) + VP(6) + LLM(6) + 原因(20) + 边框(10) = 89
        sep_thin = '─' * 89
        sep_thick = '═' * 89

        # 表头
        lines.append(f'┌{sep_thick}┐')
        lines.append(f'│  FusionController 战术台  │  {date}  │  {market_state}  │ VIX: {vix_regime:<12}│')
        lines.append(f'├{sep_thin}┬──────┬───────┬────────┬──────┬──────┬──────┬──────────────────┤')
        lines.append(f'│ {"标记":^12} │ {"标的":<10} │ {"信号":<10} │ {"得分":>5} │ 置信度 │ {"VP":>5} │ {"LLM":>5} │ {"熔断原因":<20} │')
        lines.append(f'├{sep_thin}┼──────┼───────┼────────┼──────┼──────┼──────┼──────────────────┤')

        for sig in signals:
            sym = sig.get('symbol', '?')
            level = sig.get('fusion_level', '?')
            score = sig.get('fusion_score', 0)
            conf = sig.get('fusion_confidence', 0)
            xmm_action = sig.get('xmm_signal', '?')
            vp_score = sig.get('event_sentiment_score', 0) or 0.0
            llm_score = sig.get('llm_factor_score', 0) or 0.0
            gate_reasons = sig.get('_gate_reasons', [])
            gate_blocked = not sig.get('_gate_approved', True) or bool(gate_reasons)

            # 标记选择
            if gate_blocked:
                marker = '[!!!X!!!]'
            elif score > 20:
                marker = '[---▲---]'
            elif score < -20:
                marker = '[---▼---]'
            else:
                marker = '[ ···    ]'

            reason_str = (gate_reasons[0][:18] + '..') if gate_reasons else ''
            if not reason_str and level in ('HOLD', 'REDUCED') and not gate_blocked:
                reason_str = '—'

            lines.append(
                f'│ {marker:<12} │ {sym:<10} │ {level:<10} │ {score:>+5.1f} │ {conf:.2f}  │ {vp_score:>+5.1f} │ {llm_score:>+5.1f} │ {reason_str:<20} │'
            )

        lines.append(f'├{sep_thin}┴──────┴───────┴────────┴──────┴──────┴──────┴──────────────────┤')

        # 统计
        n_blocked = sum(1 for s in signals if not s.get('_gate_approved', True))
        n_score_gt20 = sum(1 for s in signals if s.get('fusion_score', 0) > 20)
        n_buy = sum(1 for s in signals if s.get('fusion_level') in ('STRONG_BUY', 'BUY'))
        n_sell = sum(1 for s in signals if s.get('fusion_level') in ('STRONG_SELL', 'SELL'))
        n_resonance = sum(1 for s in signals if s.get('resonance', False))

        lines.append(f'│  权重: XMM 60%  |  VP 25%  |  LLM 15%  (静态硬编码 | 断流时存活源归一化)   │')
        lines.append(f'│  熔断: {n_blocked}  |  得分>20: {n_score_gt20}  |  BUY: {n_buy}  |  SELL: {n_sell}  |  共振: {n_resonance}            │')
        lines.append(f'└{sep_thick}┘')

        return '\n'.join(lines)


    # ─── 状态报告 ────────────────────────────────────────────
    def status_report(self) -> dict:
        """系统状态报告。"""
        # 从 AdapterFactory 获取适配器状态
        adapters = AdapterFactory.status()
        return {
            'components': {
                'universe': f'{self.universe.total_count} symbols',
                'futu_adapter': adapters.get('FutuAdapter', {}).get('available', False),
                'crypto_adapter': adapters.get('CryptoAdapter', {}).get('available', False),
                'xmm': XMM_AVAILABLE,
                'vp': VP_AVAILABLE,
                'llm': LLM_AVAILABLE,
                'fusion_engine': ENGINE_AVAILABLE,
                'hard_gate': GATE_AVAILABLE,
            },
            'adapters': adapters,
            'last_scan': self._last_scan,
            'weights': BASE_WEIGHTS.copy(),
        }


# ─── RSI 辅助函数 ────────────────────────────────
def _rsi(close, n=14):
    """NumPy RSI 计算"""
    d = np.diff(close); g = np.where(d > 0, d, 0.0); lo = np.where(d < 0, -d, 0.0)
    r = np.full(len(close), 50.0)
    if len(close) <= n:
        return r
    ag, al = np.mean(g[:n]), np.mean(lo[:n])
    r[n] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    for i in range(n + 1, len(close)):
        ag = (ag * (n - 1) + g[i - 1]) / n
        al = (al * (n - 1) + lo[i - 1]) / n
        r[i] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    return r


def _weekly_rsi(close, p=4):
    """周线 RSI（近似）"""
    n = len(close)
    if n < 30:
        return None
    w = [close[i] for i in range(4, n, 5)]
    if len(w) < p + 1:
        return None
    return float(_rsi(np.array(w, dtype=float), p)[-1])