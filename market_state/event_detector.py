# -*- coding: utf-8 -*-
"""
MarketEventDetector — LLM 驱动的市场情绪/事件检测器
架构层: 研究层（LLM 自由探索，高自由度）

职责:
1. 基于 K 线特征 + 信号变化，调用 NVIDIA NIM 检测情绪/事件
2. 输出 sentiment_score [-100, +100] + event_type + event_summary
3. 失败安全: LLM 调用失败 → score=0，不影响现有逻辑

约束:
- 每个标的同一天最多 1 次 LLM 调用（带缓存）
- Token 控制: prompt < 500 tokens, max_tokens=200
- 模型: meta/llama-4-maverick-17b-128e-instruct (快速 2-3s)
"""

import sys, os, json, re, time
from datetime import datetime
from pathlib import Path
import numpy as np

# 所有 LLM 模型名必须从 config.py 读取，禁止硬编码
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

sys.path.insert(0, r'C:\Users\RoyGoode\.workbuddy\skills\nvidia-api\scripts')

# 安全导入 NVIDIA NIM
try:
    from nvidia_api import nvidia_llm
    NIM_AVAILABLE = True
except ImportError:
    NIM_AVAILABLE = False

from core.paths import PROJECT_ROOT
BASE = PROJECT_ROOT
CACHE_DIR = BASE / 'market_state' / 'event_cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class MarketEventDetector:
    """
    LLM 驱动的市场情绪/事件检测器
    架构层: 研究层（LLM 自由探索，高自由度）

    作用域矩阵位置:
    - 市场情绪/事件冲击/宏观叙事 → 高自由度
    - 输出经过 FusionEngine/HardGate 纯规则后才到执行层
    """

    def __init__(self, model=None):
        self.model = model or config.LLM_MODEL
        self._cache = {}  # {symbol+date: result}

    def _cache_key(self, symbol, date):
        return f'{symbol}_{date}'

    def _load_cache(self, symbol, date):
        """从磁盘缓存加载"""
        key = self._cache_key(symbol, date)
        if key in self._cache:
            return self._cache[key]
        cache_file = CACHE_DIR / f'{key}.json'
        if cache_file.exists():
            try:
                with open(cache_file, encoding='utf-8') as f:
                    result = json.load(f)
                self._cache[key] = result
                return result
            except:
                pass
        return None

    def _save_cache(self, symbol, date, result):
        """保存到磁盘缓存"""
        key = self._cache_key(symbol, date)
        self._cache[key] = result
        cache_file = CACHE_DIR / f'{key}.json'
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except:
            pass

    def _build_price_summary(self, price_data: dict, lookback=10) -> str:
        """从 K 线数据提取关键特征，压缩成 LLM 可消化的摘要"""
        close = price_data.get('close', [])
        high = price_data.get('high', [])
        low = price_data.get('low', [])
        volume = price_data.get('volume', [])

        if len(close) < lookback:
            return '数据不足'

        # ── 展示窗口: 最近 lookback 根 K 线（LLM 看到的价格特征）──
        c = [float(x) for x in close[-lookback:]]
        h = [float(x) for x in high[-lookback:]]
        l = [float(x) for x in low[-lookback:]]
        v = [float(x) for x in volume[-lookback:]]

        # 日涨跌幅（基于展示窗口）
        rets = [(c[i]/c[i-1]-1)*100 for i in range(1, len(c))]

        # 量比（基于展示窗口）
        vol_avg = np.mean(v) if v else 1
        vol_ratio = v[-1] / vol_avg if vol_avg > 0 else 1.0

        # 振幅（基于展示窗口）
        amplitude = [(h[i]-l[i])/c[i]*100 for i in range(len(c))]

        # ── RSI 14: 使用独立的、更长的数据窗口计算（至少 30 根 K 线）──
        # 与展示窗口的 lookback 分离，确保 RSI 计算精度
        rsi_window = min(30, len(close))       # 最多用 30 根（足够稳定），最少用 len(close)
        c_rsi = [float(x) for x in close[-rsi_window:]]
        rets_rsi = [(c_rsi[i]/c_rsi[i-1]-1)*100 for i in range(1, len(c_rsi))]
        gains = [max(0, r) for r in rets_rsi]
        losses = [max(0, -r) for r in rets_rsi]
        avg_gain = np.mean(gains[-14:]) if len(gains) >= 14 else np.mean(gains)
        avg_loss = np.mean(losses[-14:]) if len(losses) >= 14 else np.mean(losses)
        rsi = 100 - 100 / (1 + avg_gain / (avg_loss + 1e-10))

        # 组装摘要
        lines = []
        lines.append(f'最新价: {c[-1]:.2f}')
        lines.append(f'近5日涨跌: {[round(r,2) for r in rets[-5:]]}' if len(rets)>=5 else f'涨跌: {[round(r,2) for r in rets]}')
        lines.append(f'最新量比: {vol_ratio:.2f}x')
        lines.append(f'最新振幅: {amplitude[-1]:.2f}%')
        lines.append(f'RSI14: {rsi:.1f}')

        # 连涨/连跌天数（基于展示窗口）
        streak = 0
        direction = 0
        for r in reversed(rets):
            if streak == 0:
                direction = 1 if r > 0 else -1
                streak = 1
            elif (direction > 0 and r > 0) or (direction < 0 and r < 0):
                streak += 1
            else:
                break
        if streak >= 3:
            lines.append(f'连{"涨" if direction>0 else "跌"}: {streak}天')

        return '\n'.join(lines)

    def _build_signal_summary(self, recent_signals: list) -> str:
        """从近期信号变化构建摘要"""
        if not recent_signals:
            return '无近期信号数据'

        lines = []
        for sig in recent_signals[-5:]:
            level = sig.get('fusion_level', '?')
            score = sig.get('fusion_score', 0)
            date = sig.get('date', '?')
            sym = sig.get('symbol', '?')
            lines.append(f'{date} {sym}: {level} (score={score:+.1f})')

        # 检测信号翻转
        if len(recent_signals) >= 2:
            prev = recent_signals[-2].get('fusion_level', '')
            curr = recent_signals[-1].get('fusion_level', '')
            if prev != curr:
                lines.append(f'信号翻转: {prev} -> {curr}')

        return '\n'.join(lines)

    @staticmethod
    def _safe_text(value, max_len: int = 500) -> str:
        """将 LLM/API 返回值安全转换为审计文本，避免 None/dict/list 切片二次异常。"""
        if value is None:
            return ''
        if isinstance(value, str):
            return value[:max_len]
        try:
            return json.dumps(value, ensure_ascii=False)[:max_len]
        except (TypeError, ValueError):
            return str(value)[:max_len]

    @staticmethod
    def _api_error_detail(parsed, raw_text: str = '') -> str:
        """识别 NVIDIA NIM/API 网关异常并返回摘要；不是异常则返回空字符串。"""
        if isinstance(parsed, dict):
            if parsed.get('_api_error'):
                return str(parsed.get('error_detail', 'unknown'))
            if 'error' in parsed:
                err = parsed.get('error')
                if isinstance(err, dict):
                    return str(err.get('message') or err.get('detail') or err)
                return str(err)
            if parsed.get('status') in ('error', 'failed'):
                return str(parsed.get('message') or parsed.get('detail') or parsed)

        text = raw_text if isinstance(raw_text, str) else MarketEventDetector._safe_text(raw_text, 1000)
        if not text:
            return ''
        lower = text.lower()
        markers = (
            '[error', 'error 500', 'internal server error', 'bad gateway',
            '<html', 'enginecore encountered', 'rate_limit', 'rate limit',
            'timeout', 'timed out', 'service unavailable', 'gateway timeout'
        )
        if any(m in lower for m in markers):
            compact = re.sub(r'\s+', ' ', text).strip()
            return compact[:160] or 'api error'
        return ''

    @staticmethod
    def _normalize_reply(reply):
        """统一 NIM 返回格式: dict 直接作为 parsed，字符串继续走 JSON 解析。"""
        if isinstance(reply, dict):
            return reply, MarketEventDetector._safe_text(reply)
        raw_text = MarketEventDetector._safe_text(reply)
        if not raw_text:
            return None, raw_text
        return MarketEventDetector._parse_json(raw_text), raw_text

    def analyze_sentiment(self, symbol: str, price_data: dict,
                          recent_signals: list = None,
                          market_state: str = 'CRAB',
                          force: bool = False) -> dict:
        """
        分析单个标的的情绪状态

        Args:
            symbol: 标的代码
            price_data: K 线数据 dict (close/high/low/volume/timestamp)
            recent_signals: 近期该标的的信号列表
            market_state: 当前市场状态
            force: 是否强制重新调用（忽略缓存）

        Returns:
            dict with keys:
            - sentiment_score: [-100, +100]
            - event_type: str
            - event_summary: str
            - confidence: [0, 1]
            - llm_raw: str (审计用)
        """
        today = datetime.now().strftime('%Y-%m-%d')

        # 缓存检查
        if not force:
            cached = self._load_cache(symbol, today)
            if cached:
                return cached

        # 默认值（LLM 失败时返回）
        default_result = {
            'sentiment_score': 0.0,
            'event_type': 'none',
            'event_summary': 'LLM 检测未执行',
            'confidence': 0.0,
            'llm_raw': '',
            'timestamp': datetime.now().isoformat(),
        }

        if not NIM_AVAILABLE:
            default_result['event_summary'] = 'NVIDIA NIM API 不可用'
            return default_result

        # 构建 prompt
        price_summary = self._build_price_summary(price_data)
        signal_summary = self._build_signal_summary(recent_signals or [])

        prompt = f"""You are a strict, objective Quantitative Market Analyst. Evaluate the short-term sentiment and momentum of the following asset based ONLY on the provided data.

Asset: {symbol}
Current Market Regime: {market_state}

Regime Definition:
- If regime is 'CRAB', the market is range-bound and mean-reverting. Do NOT interpret 'CRAB' as a bearish or negative signal. It implies low volatility and neutral baseline sentiment.

Recent Price Action (Last 10 bars):
{price_summary}

Recent Signal History:
{signal_summary}

Instructions:
You must perform an evidence-based evaluation before scoring. 
1. Identify bullish evidence (e.g., oversold RSI, support held).
2. Identify bearish evidence (e.g., consecutive drops, moving average breakdown).
3. If the market is in 'CRAB' regime, your sentiment_score MUST be tightly bounded within [-30, +30] UNLESS there is overwhelming evidence of a volume anomaly or structural breakout.

Output format MUST be valid JSON only (no markdown blocks, no extra text):
{{
  "bullish_factors": "briefly list positive signals",
  "bearish_factors": "briefly list negative signals",
  "sentiment_score": integer between -100 and +100,
  "event_type": "none | momentum_shift | volume_anomaly | reversal_signal | breakout",
  "summary": "One sentence strictly summarizing the weight of evidence.",
  "confidence": integer between 0 and 100
}}"""

        try:
            t0 = time.time()
            reply = nvidia_llm.chat(
                prompt,
                model=self.model,
                max_tokens=200,
                temperature=0.5
            )
            elapsed = time.time() - t0

            # 解析 JSON / 识别 API 异常
            parsed, raw_text = self._normalize_reply(reply)
            api_error = self._api_error_detail(parsed, raw_text)
            if api_error:
                default_result['llm_raw'] = raw_text[:500]
                default_result['event_summary'] = f'API 错误: {api_error[:80]}'
                return default_result

            if parsed and isinstance(parsed, dict):
                result = {
                    'sentiment_score': float(parsed.get('sentiment_score', 0)),
                    'event_type': parsed.get('event_type', 'none'),
                    'event_summary': parsed.get('summary', ''),
                    'confidence': float(parsed.get('confidence', 0.5)),
                    'llm_raw': raw_text[:500],
                    'llm_model': self.model,
                    'llm_latency_ms': int(elapsed * 1000),
                    'timestamp': datetime.now().isoformat(),
                }
                # 限制范围
                result['sentiment_score'] = max(-100, min(100, result['sentiment_score']))
                result['confidence'] = max(0, min(1, result['confidence']))

                self._save_cache(symbol, today, result)
                return result
            else:
                default_result['llm_raw'] = raw_text[:500]
                default_result['event_summary'] = 'JSON 解析失败 (增强解析器 v3 无法提取)'
                return default_result

        except Exception as e:
            default_result['event_summary'] = f'LLM 调用失败: {str(e)[:80]}'
            default_result['llm_raw'] = str(e)[:500]
            return default_result

    def analyze_batch_sentiment(self, tickers_data: list,
                                 model: str = None,
                                 batch_size: int = 11) -> dict:
        """
        批量分析多个标的的情绪状态 — 1次API调用替代N次独立调用

        Args:
            tickers_data: list of dict, each with:
                - symbol: str (e.g. '00700.HK')
                - market_state: str (e.g. 'BULL')
                - price_summary: str (pre-built by _build_price_summary())
            model: LLM model (default: llama-4-maverick for speed)
            batch_size: max tickers per API call (default 11)

        Returns:
            dict mapping symbol -> sentiment result (same schema as analyze_sentiment)
        """
        if not NIM_AVAILABLE or not tickers_data:
            return {}

        model = model or config.LLM_MODEL  # ⛔ 锁定 L-001
        today = datetime.now().strftime('%Y-%m-%d')
        all_results = {}

        # 按 batch_size 切分
        for batch_start in range(0, len(tickers_data), batch_size):
            batch = tickers_data[batch_start:batch_start + batch_size]

            # 缓存检查：全部已缓存则跳过
            uncached = []
            for td in batch:
                sym = td['symbol']
                cached = self._load_cache(f'BATCH_{sym}', today)
                if cached:
                    all_results[sym] = cached
                else:
                    uncached.append(td)

            if not uncached:
                continue

            # 构建 CSV 格式 prompt（Token 效率最高）
            header = 'symbol,regime,close,ret_5d,rsi14,vol_ratio'
            rows = []
            for td in uncached:
                summary = td.get('price_summary', '')
                # 从 summary 提取关键数值
                close = '?'
                ret_5d = '?'
                rsi14 = '?'
                vol_ratio = '?'

                for line in summary.split('\n'):
                    line = line.strip()
                    if line.startswith('最新价:'):
                        close = line.split(':')[1].strip()
                    elif line.startswith('近5日涨跌:'):
                        raw = line.split(':')[1].strip()
                        raw = raw.replace('[', '').replace(']', '').replace(' ', '')
                        ret_5d = raw
                    elif line.startswith('RSI14:'):
                        rsi14 = line.split(':')[1].strip()
                    elif line.startswith('最新量比:'):
                        vol_ratio = line.split(':')[1].strip().replace('x', '')

                rows.append(f'{td["symbol"]},{td["market_state"]},{close},"{ret_5d}",{rsi14},{vol_ratio}')

            csv_data = '\n'.join(rows)
            symbols_str = ', '.join(td['symbol'] for td in uncached)

            prompt = f"""你是量化市场情绪分析师。分析以下所有标的的短期市场情绪和事件。

CSV输入格式: symbol,market_state,close,ret_5d(近5日涨跌%),rsi14,vol_ratio

数据:
{csv_data}

对以上每个标的，自由判断:
- sentiment_score: [-100,+100] (正=看多, 负=看空, 0=中性)
- event_type: 自定义 (如 none/momentum_shift/reversal/volume_anomaly 等)
- summary: 一句话描述
- confidence: [0,1]

请以JSON格式输出(无markdown)，key为各标的symbol，每个标的包含 sentiment_score, event_type, summary, confidence 即可。
确保每个标的都在返回JSON中。"""

            csv_symbols = [td['symbol'] for td in uncached]

            # API 调用
            try:
                t0 = time.time()
                reply = nvidia_llm.chat(
                    prompt,
                    model=model,
                    max_tokens=800,
                    temperature=0.5
                )
                elapsed = time.time() - t0

                # 批量响应可能超过 500 字符（5 tickers × ~150 chars = ~750）
                # 需要增大 max_len 避免 JSON 被截断解析失败
                raw_text_full = MarketEventDetector._safe_text(reply, max_len=2000)
                parsed = MarketEventDetector._parse_json(raw_text_full)
                raw_text = raw_text_full  # for llm_raw field (caller will slice)
                api_error = self._api_error_detail(parsed, raw_text)
                if api_error or not parsed or not isinstance(parsed, dict):
                    raise ValueError(f'Batch API 返回异常: {api_error or "解析失败"}')

                # ── 建立 parsed key → symbol 的模糊映射 ──
                # LLM 可能返回短 key（"00700" 而非 "00700.HK"），需模糊匹配
                key_to_symbol = {}
                for td in uncached:
                    sym = td['symbol']
                    if sym in parsed:
                        key_to_symbol[sym] = sym
                    else:
                        # 尝试去掉 .HK/.US 后缀
                        short = sym.split('.')[0]  # "00700.HK" -> "00700"
                        if short in parsed:
                            key_to_symbol[short] = sym
                            continue
                        # 尝试 market.symbol 格式 (HK.00700)
                        alt = sym.replace('.', '.')  # 已经是 "00700.HK", 再试 "HK.00700"
                        parts = sym.split('.')
                        if len(parts) == 2:
                            swapped = f'{parts[1]}.{parts[0]}'  # "HK.00700"
                            if swapped in parsed:
                                key_to_symbol[swapped] = sym
                                continue

                for td in uncached:
                    sym = td['symbol']
                    # 找到 mapped key
                    mapped_key = None
                    for pk, es in key_to_symbol.items():
                        if es == sym:
                            mapped_key = pk
                            break
                    if mapped_key:
                        item = parsed[mapped_key]
                        result = {
                            'sentiment_score': float(item.get('sentiment_score', 0)),
                            'event_type': item.get('event_type', 'none'),
                            'event_summary': item.get('summary', ''),
                            'confidence': float(item.get('confidence', 0.5)),
                            'llm_raw': raw_text[:500],
                            'llm_model': model,
                            'llm_latency_ms': int(elapsed * 1000),
                            'timestamp': datetime.now().isoformat(),
                        }
                        result['sentiment_score'] = max(-100, min(100, result['sentiment_score']))
                        result['confidence'] = max(0, min(1, result['confidence']))
                        self._save_cache(f'BATCH_{sym}', today, result)
                        all_results[sym] = result
                    else:
                        # 该标的不在返回中 → 单独调用回退
                        all_results[sym] = self.analyze_sentiment(
                            symbol=sym,
                            price_data={'close': [], 'high': [], 'low': [], 'volume': []},
                            market_state=td.get('market_state', 'CRAB'),
                            force=True
                        )

            except Exception as e:
                # 整批失败 → 逐只回退（带硬性限流）
                for td in uncached:
                    sym = td['symbol']
                    try:
                        all_results[sym] = self.analyze_sentiment(
                            symbol=sym,
                            price_data={'close': [], 'high': [], 'low': [], 'volume': []},
                            market_state=td.get('market_state', 'CRAB'),
                            force=True
                        )
                    except:
                        all_results[sym] = {
                            'sentiment_score': 0.0, 'event_type': 'none',
                            'event_summary': f'批量+单只兜底均失败: {str(e)[:50]}',
                            'confidence': 0.0, 'llm_raw': str(e)[:200],
                            'timestamp': datetime.now().isoformat(),
                        }
                    time.sleep(6.7)  # 9 req/min 硬限流

        return all_results

    def analyze_market_narrative(self, market_state: str,
                                  vix_series: dict = None) -> dict:
        """
        宏观叙事分析（每日一次，全市场）

        Args:
            market_state: 当前市场状态
            vix_series: VIX 时间序列 {date: value}

        Returns:
            dict with keys:
            - narrative_regime: str
            - key_events: list
            - narrative_score: [-100, +100]
        """
        today = datetime.now().strftime('%Y-%m-%d')

        # 缓存
        cached = self._load_cache('MARKET', today)
        if cached:
            return cached

        default_result = {
            'narrative_regime': 'neutral',
            'key_events': [],
            'narrative_score': 0.0,
            'confidence': 0.0,
            'llm_raw': '',
            'timestamp': datetime.now().isoformat(),
        }

        if not NIM_AVAILABLE:
            default_result['narrative_regime'] = 'unknown'
            return default_result

        # VIX 摘要
        vix_summary = '无 VIX 数据'
        if vix_series:
            dates = sorted(vix_series.keys())
            vals = [vix_series[d] for d in dates[-10:]]
            vxx = vals[-1] if vals else 0
            vxx_avg = np.mean(vals) if vals else 0
            vix_summary = f'VXX最新: {vxx:.2f}, 10日均值: {vxx_avg:.2f}, 趋势: {"上升" if vxx > vxx_avg else "下降"}'

        prompt = f"""你是宏观市场分析师。基于以下信息判断当前市场叙事状态。

市场状态: {market_state}
{vix_summary}

自由判断当前市场叙事:
1. narrative_regime: 自定义 (如 risk_on/risk_off/neutral/panic/euphoria 等)
2. narrative_score: [-100,+100] (正=风险偏好, 负=风险厌恶)
3. 当前主要市场叙事关键词(最多3个)

请以JSON格式输出(无markdown)，字段包含 narrative_regime, narrative_score, key_events, confidence 即可。"""

        try:
            t0 = time.time()
            reply = nvidia_llm.chat(
                prompt,
                model=self.model,
                max_tokens=200,
                temperature=0.5
            )
            elapsed = time.time() - t0

            parsed, raw_text = self._normalize_reply(reply)
            api_error = self._api_error_detail(parsed, raw_text)
            if api_error:
                default_result['llm_raw'] = raw_text[:500]
                default_result['narrative_regime'] = 'unknown'
                return default_result

            if parsed and isinstance(parsed, dict):
                result = {
                    'narrative_regime': parsed.get('narrative_regime', 'neutral'),
                    'key_events': parsed.get('key_events', []),
                    'narrative_score': float(parsed.get('narrative_score', 0)),
                    'confidence': float(parsed.get('confidence', 0.5)),
                    'llm_raw': raw_text[:500],
                    'llm_model': self.model,
                    'llm_latency_ms': int(elapsed * 1000),
                    'timestamp': datetime.now().isoformat(),
                }
                result['narrative_score'] = max(-100, min(100, result['narrative_score']))
                self._save_cache('MARKET', today, result)
                return result
            else:
                default_result['llm_raw'] = raw_text[:500]
                return default_result

        except Exception as e:
            default_result['llm_raw'] = str(e)[:500]
            return default_result

    @staticmethod
    def cleanup_cache(max_age_days: int = 7) -> int:
        """
        清理超过 max_age_days 天的缓存文件

        Args:
            max_age_days: 最大保留天数，默认 7 天

        Returns:
            删除的文件数量
        """
        if not CACHE_DIR.exists():
            return 0

        now = datetime.now()
        deleted = 0
        for f in CACHE_DIR.glob('*.json'):
            try:
                # 文件名格式: SYMBOL_DATE.json，从末尾提取日期
                stem = f.stem  # e.g. "00700.HK_2026-05-01"
                parts = stem.rsplit('_', 1)
                if len(parts) != 2:
                    continue
                date_str = parts[-1]  # "2026-05-01"
                file_date = datetime.strptime(date_str, '%Y-%m-%d')
                age_days = (now - file_date).days
                if age_days > max_age_days:
                    f.unlink()
                    deleted += 1
            except (ValueError, OSError):
                continue

        if deleted > 0:
            print(f'  [CACHE] event_cache 清理: 删除 {deleted} 个超过 {max_age_days} 天的缓存文件')
        return deleted

    @staticmethod
    def _parse_json(text):
        """
        从 LLM 输出中提取 JSON — 增强容错版 v3

        处理场景:
        1. 标准 JSON → 直接解析
        2. markdown 代码块包裹 → 剥离后解析
        3. JSON 后面跟了自然语言说明 → 提取第一个完整 JSON 对象
        4. 嵌套 JSON（含子对象/数组）→ 用括号匹配提取
        5. API/网关错误响应（ERROR 500/502 HTML 等）→ 返回 _api_error
        6. 字段名没有引号（非严格 JSON）→ 正则修复后重试
        7. 尾部多余逗号 → 修复后重试
        """
        if text is None:
            return None
        if not isinstance(text, str):
            if isinstance(text, dict):
                return text
            text = MarketEventDetector._safe_text(text)
        if not text or not text.strip():
            return None

        # ── 0. 修复模型常见转义问题 ──
        # 某些模型（如 mixtral-8x7b）会在 JSON 键名中加转义下划线: sentiment\_score
        # 标准 json.loads 会报 Invalid \escape
        text = text.replace('\\_', '_')

        # ── 0a. 快速检测 API/网关错误响应 ──
        error_markers = ['[ERROR', 'error 500', 'bad gateway', '<html', '"error":', '"Internal Server Error',
                         'EngineCore encountered', 'rate_limit', 'rate limit', 'timeout', 'service unavailable']
        text_lower = text.lower()
        if any(marker.lower() in text_lower for marker in error_markers):
            # 尝试从错误响应中提取嵌套 JSON（用括号匹配而非简单正则）
            brace_depth = 0
            start_idx = None
            for i, ch in enumerate(text):
                if ch == '{':
                    if brace_depth == 0:
                        start_idx = i
                    brace_depth += 1
                elif ch == '}':
                    brace_depth -= 1
                    if brace_depth == 0 and start_idx is not None:
                        candidate = text[start_idx:i+1]
                        try:
                            err = json.loads(candidate)
                            if 'error' in err:
                                return {'_api_error': True, 'error_detail': err.get('error', {}).get('message', str(err))}
                        except (json.JSONDecodeError, ValueError):
                            pass
                        start_idx = None
            # 无法提取错误 JSON，返回结构化错误（失败安全）
            compact = re.sub(r'\s+', ' ', text).strip()
            return {'_api_error': True, 'error_detail': compact[:160] or 'api error'}

        # ── 1. 去除 markdown 代码块 ──
        cleaned = text.strip()
        m = re.search(r'```(?:json)?\s*(.*?)\s*```', cleaned, re.DOTALL)
        if m:
            cleaned = m.group(1).strip()

        # ── 2. 尝试直接解析整个文本 ──
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            pass

        # ── 3. 括号匹配提取完整 JSON 对象（支持嵌套）──
        def extract_json_objects(s):
            """用括号匹配提取所有顶层 JSON 对象"""
            results = []
            i = 0
            while i < len(s):
                if s[i] == '{':
                    depth = 0
                    in_string = False
                    escape_next = False
                    start = i
                    for j in range(i, len(s)):
                        ch = s[j]
                        if escape_next:
                            escape_next = False
                            continue
                        if ch == '\\' and in_string:
                            escape_next = True
                            continue
                        if ch == '"' and not escape_next:
                            in_string = not in_string
                        if not in_string:
                            if ch == '{':
                                depth += 1
                            elif ch == '}':
                                depth -= 1
                                if depth == 0:
                                    results.append(s[start:j+1])
                                    i = j + 1
                                    break
                    else:
                        i += 1
                else:
                    i += 1
            return results

        candidates = extract_json_objects(cleaned)
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                # ── 4. 修复常见非严格 JSON 问题 ──
                fixed = candidate
                # 尾部多余逗号: ,} 或 ,]
                fixed = re.sub(r',\s*}', '}', fixed)
                fixed = re.sub(r',\s*]', ']', fixed)
                # 单引号 → 双引号（简单替换，适用于无嵌套情况）
                if "'" in fixed and '"' not in fixed:
                    fixed = fixed.replace("'", '"')
                try:
                    return json.loads(fixed)
                except (json.JSONDecodeError, ValueError):
                    continue

        # ── 5. 最后手段: 正则逐字段提取 ──
        field_patterns = {
            'sentiment_score': r'"?sentiment_score"?\s*[:=]\s*(-?[\d.]+)',
            'event_type':      r'"?event_type"?\s*[:=]\s*"([^"]*)"',
            'summary':         r'"?summary"?\s*[:=]\s*"([^"]*)"',
            'confidence':      r'"?confidence"?\s*[:=]\s*([\d.]+)',
            'narrative_regime': r'"?narrative_regime"?\s*[:=]\s*"([^"]*)"',
            'narrative_score': r'"?narrative_score"?\s*[:=]\s*(-?[\d.]+)',
            'key_events':      r'"?key_events"?\s*[:=]\s*\[(.*?)\]',
        }
        extracted = {}
        for key, pattern in field_patterns.items():
            m = re.search(pattern, cleaned, re.DOTALL)
            if m:
                val = m.group(1)
                if key in ('sentiment_score', 'confidence', 'narrative_score'):
                    try:
                        extracted[key] = float(val)
                    except ValueError:
                        continue
                elif key == 'key_events':
                    # 提取数组内容
                    items = re.findall(r'"([^"]*)"', val)
                    extracted[key] = items
                else:
                    extracted[key] = val

        if extracted and ('sentiment_score' in extracted or 'narrative_score' in extracted):
            return extracted

        return None


# ─── 全局实例 ────────────────────────────────────────────
event_detector = MarketEventDetector()
