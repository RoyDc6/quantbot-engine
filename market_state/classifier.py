# -*- coding: utf-8 -*-
"""
市场状态分类器
多维度市场状态自动识别：
- VIX 环境（恐惧/贪婪）
- 趋势方向（上升/下降/横盘）
- 动量状态（超买/超卖）
- 波动率水平
- 综合市场状态
"""

import sys, io, os, json
from pathlib import Path
from datetime import datetime, timedelta
import numpy as np

import pandas as pd


# ─── 路径配置 ─────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from core.paths import PROJECT_ROOT
BASE = PROJECT_ROOT
SCANNER_CACHE = BASE / 'scanner' / 'cache'
OUTPUT_DIR = BASE / 'market_state'
OUTPUT_DIR.mkdir(exist_ok=True)


# ─── VIX Regime 阈值 ────────────────────────────────
VIX_SUPPRESSED_PRICE = 35      # VXX 价格高于此值 = 高恐惧
VIX_RELEASED_PRICE = 22        # VXX 价格低于此值 = 低恐惧
VIX_RSI_W_HIGH = 70            # 周RSI 高于此值 = 超买恐惧
VIX_RSI_W_LOW = 40             # 周RSI 低于此值 = 低恐惧


# ─── 市场状态枚举 ────────────────────────────────────
class VixRegime:
    SUPPRESSED = 'SUPPRESSED'    # 高恐惧/恐慌
    CANDIDATE = 'CANDIDATE'      # 中性/过渡
    RELEASED = 'RELEASED'        # 低恐惧/平静

class TrendState:
    BULL = 'BULL'                # 上升趋势
    BEAR = 'BEAR'                # 下降趋势
    CRAB = 'CRAB'                # 横盘震荡

class MomentumState:
    OVERBOUGHT = 'OVERBOUGHT'    # RSI > 70 超买
    NEUTRAL = 'NEUTRAL'          # RSI 40-60 中性
    OVERSOLD = 'OVERSOLD'        # RSI < 30 超卖
    BULLISH = 'BULLISH'          # RSI 60-70 偏强
    BEARISH = 'BEARISH'          # RSI 30-40 偏弱

class VolLevel:
    HIGH = 'HIGH'                # 高波动
    MEDIUM = 'MEDIUM'            # 中波动
    LOW = 'LOW'                  # 低波动

class MarketState:
    BULL = 'BULL'                # 牛市：上升趋势 + 低VIX
    BEAR = 'BEAR'                # 熊市：下降趋势 + 高VIX
    RECOVERY = 'RECOVERY'        # 复苏：超卖 + VIX回落
    CORRECTION = 'CORRECTION'    # 回调：强势 + VIX上升
    CRAB = 'CRAB'                # 震荡：横盘 + 中VIX


# ─── 数据加载 ────────────────────────────────────────
def load_vix_data(days=252) -> pd.DataFrame:
    """加载 VXX 数据"""
    vxx_path = SCANNER_CACHE / 'VXX_US.json'
    if not vxx_path.exists():
        return None
    with open(vxx_path) as f:
        data = json.load(f)
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'], unit='ms')
    df = df.sort_values('date').tail(days)
    return df


def _parse_kline_result(data):
    """将 kline dict 统一转为 DataFrame（Futu / TickFlow 共用）"""
    if not data or 'close' not in data:
        return None
    df = pd.DataFrame({
        'date': [datetime.fromtimestamp(t/1000) for t in data['timestamp']],
        'open': [float(o) for o in data['open']],
        'high': [float(h) for h in data['high']],
        'low': [float(l) for l in data['low']],
        'close': [float(c) for c in data['close']],
        'volume': [float(v) for v in data.get('volume', [])]
    })
    df = df.sort_values('date')
    return df


def load_price_data(symbol: str, days=252) -> pd.DataFrame:
    """加载标的价格数据（FutuAdapter 优先 → TickFlow 回退）"""
    # 1. FutuAdapter（统一适配层）
    try:
        from core.futu_adapter import FutuAdapter
        adapter = FutuAdapter()
        df = adapter.fetch_kline(symbol, count=days)
        if df is not None and len(df) >= 50:
            print(f'  [Futu] {symbol}: {len(df)} days')
            return df
    except Exception as e:
        print(f'  [WARN] Futu 获取 {symbol} 失败: {e}')

    # 2. TickFlow 回退（有 API key 用付费版）
    try:
        import tickflow_env  # noqa: auto-inject TICKFLOW_API_KEY
        from tickflow import TickFlow
        tf = TickFlow() if os.environ.get('TICKFLOW_API_KEY') else TickFlow.free()
        data = tf.klines.get(symbol, period='1d', count=days)
        df = _parse_kline_result(data)
        if df is not None:
            print(f'  [TickFlow] {symbol}: {len(df)} days')
            return df
    except Exception as e:
        print(f'  [WARN] TickFlow 获取 {symbol} 失败: {e}')

    print(f'  [ERROR] {symbol}: Futu + TickFlow 均无数据')
    return None


# ─── 技术指标计算 ────────────────────────────────────
def compute_rsi(close: np.ndarray, n=14) -> np.ndarray:
    d = np.diff(close, prepend=close[0])
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    r = np.full(len(close), 50.0)
    if len(close) <= n: return r
    ag, al = float(np.mean(g[:n])), float(np.mean(l[:n]))
    r[n] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    for i in range(n+1, len(close)):
        ag = (ag * (n-1) + g[i-1]) / n
        al = (al * (n-1) + l[i-1]) / n
        r[i] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    return r


def compute_sma(close: np.ndarray, n: int) -> np.ndarray:
    s = np.full(len(close), np.nan)
    for i in range(n-1, len(close)):
        s[i] = float(np.mean(close[i-n+1:i+1]))
    return s


def compute_atr(high, low, close, n=14) -> np.ndarray:
    tr = [max(high[0]-low[0], abs(high[0]-close[0]), abs(low[0]-close[0]))]
    for i in range(1, len(close)):
        tr.append(max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1])))
    atr = np.full(len(close), np.nan)
    for i in range(n-1, len(close)):
        atr[i] = float(np.mean(tr[max(0,i-n+1):i+1]))
    return atr


def weekly_rsi(close: np.ndarray, n=4) -> float:
    """日历周RSI（每周最后一个数据点）"""
    if len(close) < 30: return 50.0
    w = [close[i] for i in range(4, len(close), 5)]
    if len(w) < n+1: return 50.0
    return float(compute_rsi(np.array(w, dtype=float), n)[-1])


# ─── VIX Regime 分类 ────────────────────────────────
def classify_vix_regime(vxx_df: pd.DataFrame) -> dict:
    """分析 VIX 环境"""
    if vxx_df is None or len(vxx_df) < 30:
        return {'regime': VixRegime.CANDIDATE, 'vxx_price': None,
                'rsi_week': None, 'trend': None, 'pct_rank': None}

    close = vxx_df['close'].values.astype(float)
    vxx_price = close[-1]

    # 计算 RSI
    rsi_d = compute_rsi(close, 14)
    rsi_w = weekly_rsi(close, 4)

    # 计算 VXX 价格分位数（近1年）
    recent = close[-252:] if len(close) > 252 else close
    pct_rank = float(np.sum(recent <= vxx_price) / len(recent)) * 100

    # 趋势判断
    sma20 = compute_sma(close, 20)
    trend = 'rising' if not np.isnan(sma20[-1]) and vxx_price > sma20[-1] else 'falling'

    # Regime 判断
    if vxx_price >= VIX_SUPPRESSED_PRICE or rsi_w >= VIX_RSI_W_HIGH:
        regime = VixRegime.SUPPRESSED
    elif vxx_price <= VIX_RELEASED_PRICE and rsi_w <= VIX_RSI_W_LOW:
        regime = VixRegime.RELEASED
    else:
        regime = VixRegime.CANDIDATE

    return {
        'regime': regime,
        'vxx_price': round(vxx_price, 2),
        'rsi_day': round(float(rsi_d[-1]), 1),
        'rsi_week': round(rsi_w, 1),
        'trend': trend,
        'pct_rank': round(pct_rank, 1),
    }


# ─── 趋势分类 ───────────────────────────────────────
def classify_trend(price_df: pd.DataFrame) -> dict:
    """分析趋势状态"""
    if price_df is None or len(price_df) < 60:
        return {'trend': TrendState.CRAB, 'sma20': None, 'sma50': None,
                'price_vs_sma20': None, 'price_vs_sma50': None,
                'price': None, 'momentum': MomentumState.NEUTRAL,
                'vol_level': VolLevel.MEDIUM, 'rsi_day': 50.0,
                'rsi_week': 50.0, 'atr_pct': 0.0, 'atr_pct_rank': 50.0}

    close = price_df['close'].values.astype(float)
    price = close[-1]

    sma20 = compute_sma(close, 20)
    sma50 = compute_sma(close, 50)

    price_vs_sma20 = (price / sma20[-1] - 1) * 100 if not np.isnan(sma20[-1]) else 0
    price_vs_sma50 = (price / sma50[-1] - 1) * 100 if not np.isnan(sma50[-1]) else 0

    # 趋势判断
    above_all = price_vs_sma20 > 0 and price_vs_sma50 > 0
    below_all = price_vs_sma20 < 0 and price_vs_sma50 < 0

    if above_all:
        trend = TrendState.BULL
    elif below_all:
        trend = TrendState.BEAR
    else:
        trend = TrendState.CRAB

    # 动量判断
    rsi_d = compute_rsi(close, 14)[-1]
    rsi_w = weekly_rsi(close, 4)

    if rsi_d > 70:
        momentum = MomentumState.OVERBOUGHT
    elif rsi_d < 30:
        momentum = MomentumState.OVERSOLD
    elif rsi_d > 60:
        momentum = MomentumState.BULLISH
    elif rsi_d < 40:
        momentum = MomentumState.BEARISH
    else:
        momentum = MomentumState.NEUTRAL

    # 波动率
    high = price_df['high'].values.astype(float)
    low = price_df['low'].values.astype(float)
    atr = compute_atr(high, low, close, 14)
    atr_pct = atr[-1] / price * 100 if not np.isnan(atr[-1]) and price > 0 else 0

    recent_atr = [a for a in atr[-60:] if not np.isnan(a)]
    if len(recent_atr) > 10:
        atr_pct_rank = float(np.sum(np.array(recent_atr) <= atr[-1]) / len(recent_atr)) * 100
    else:
        atr_pct_rank = 50

    if atr_pct_rank > 70:
        vol_level = VolLevel.HIGH
    elif atr_pct_rank < 30:
        vol_level = VolLevel.LOW
    else:
        vol_level = VolLevel.MEDIUM

    return {
        'trend': trend,
        'momentum': momentum,
        'vol_level': vol_level,
        'price': round(price, 2),
        'sma20': round(float(sma20[-1]), 2) if not np.isnan(sma20[-1]) else None,
        'sma50': round(float(sma50[-1]), 2) if not np.isnan(sma50[-1]) else None,
        'price_vs_sma20': round(price_vs_sma20, 2),
        'price_vs_sma50': round(price_vs_sma50, 2),
        'rsi_day': round(float(rsi_d), 1),
        'rsi_week': round(float(rsi_w), 1),
        'atr_pct': round(float(atr_pct), 2),
        'atr_pct_rank': round(atr_pct_rank, 1),
    }


def classify_crypto_state(price_df: pd.DataFrame) -> str:
    """使用基准币已收盘日线判定 Crypto 市场状态。

    Crypto 不复用 VIX regime。只有价格同时位于 SMA20/SMA50 同侧，且
    SMA20 的五日方向一致时才给出 BULL/BEAR；其余保持 CRAB，避免把
    不确定环境硬编码成单边市场。
    """
    if price_df is None or len(price_df) < 60:
        return MarketState.CRAB

    trend = classify_trend(price_df)
    close = price_df['close'].values.astype(float)
    sma20 = compute_sma(close, 20)
    current_sma20 = sma20[-1]
    prior_sma20 = sma20[-6]
    if np.isnan(current_sma20) or np.isnan(prior_sma20):
        return MarketState.CRAB

    sma20_rising = current_sma20 > prior_sma20
    sma20_falling = current_sma20 < prior_sma20
    if trend['trend'] == TrendState.BULL and sma20_rising:
        return MarketState.BULL
    if trend['trend'] == TrendState.BEAR and sma20_falling:
        return MarketState.BEAR
    return MarketState.CRAB


# ─── 综合市场状态 ───────────────────────────────────
def classify_market_state(vix: dict, trend: dict) -> str:
    """综合判断市场状态"""
    vix_regime = vix['regime']
    price_trend = trend['trend']
    momentum = trend['momentum']
    rsi_d = trend['rsi_day']

    # 规则判断
    if vix_regime == VixRegime.SUPPRESSED:
        if price_trend == TrendState.BEAR and momentum in (MomentumState.OVERSOLD, MomentumState.BEARISH):
            return MarketState.BEAR
        elif momentum == MomentumState.OVERBOUGHT:
            return MarketState.CORRECTION
        else:
            return MarketState.CRAB

    elif vix_regime == VixRegime.RELEASED:
        if price_trend == TrendState.BULL:
            return MarketState.BULL
        elif momentum in (MomentumState.OVERSOLD, MomentumState.BEARISH):
            return MarketState.RECOVERY
        else:
            return MarketState.BULL

    else:  # CANDIDATE
        if price_trend == TrendState.BULL and momentum in (MomentumState.BULLISH, MomentumState.NEUTRAL):
            return MarketState.BULL
        elif price_trend == TrendState.BEAR and momentum in (MomentumState.BEARISH, MomentumState.NEUTRAL):
            return MarketState.BEAR
        else:
            return MarketState.CRAB


# ─── 交易建议 ───────────────────────────────────────
def get_market_advice(market_state: str, vix_regime: str) -> dict:
    """根据市场状态生成交易建议"""
    advice_map = {
        MarketState.BULL: {
            'action': 'BUY',
            'position': '高仓位（60-80%）',
            'stop_loss': '保守（-10%）',
            'description': '低VIX + 上升趋势，适合持有或加仓'
        },
        MarketState.RECOVERY: {
            'action': 'BUY',
            'position': '中等仓位（40-60%）',
            'stop_loss': '中等（-12%）',
            'description': 'VIX回落 + 超卖，适合分批建仓'
        },
        MarketState.CRAB: {
            'action': 'HOLD',
            'position': '低仓位（20-40%）',
            'stop_loss': '严格（-8%）',
            'description': '震荡市场，高抛低吸'
        },
        MarketState.CORRECTION: {
            'action': 'REDUCE',
            'position': '低仓位（20-40%）',
            'stop_loss': '严格（-8%）',
            'description': 'VIX上升 + 超买，控制风险'
        },
        MarketState.BEAR: {
            'action': 'SELL',
            'position': '清仓或空仓',
            'stop_loss': '无条件止损',
            'description': '高VIX + 下降趋势，规避风险'
        },
    }
    return advice_map.get(market_state, advice_map[MarketState.CRAB])


# ─── 主分类器 ───────────────────────────────────────
class MarketStateClassifier:
    """市场状态分类器"""

    def __init__(self, symbol='SPY.US', vix_df=None, vix_meta=None):
        self.symbol = symbol
        self.vix_df = (
            vix_df.sort_values('date').tail(252).reset_index(drop=True)
            if isinstance(vix_df, pd.DataFrame) and not vix_df.empty
            else vix_df
        )
        self.vix_meta = dict(vix_meta) if isinstance(vix_meta, dict) else {}
        self._vix_injected = vix_df is not None
        self.price_df = None
        self.vix_state = None
        self.trend_state = None
        self.market_state = None

    def load_data(self):
        """加载数据"""
        print(f'\n加载 {self.symbol} 市场数据...')
        if not self._vix_injected:
            self.vix_df = load_vix_data()
        self.price_df = load_price_data(self.symbol)
        if self.vix_df is not None:
            print(f'  VXX 数据: {len(self.vix_df)} 天')
        if self.vix_meta:
            print(
                f'  VXX 来源: {self.vix_meta.get("source", "UNKNOWN")} | '
                f'as-of: {self.vix_meta.get("as_of", "N/A")} | '
                f'新鲜度: {self.vix_meta.get("freshness", "UNKNOWN")}'
            )
        if self.price_df is not None:
            print(f'  价格数据: {len(self.price_df)} 天')

    def analyze(self) -> dict:
        """执行完整分析"""
        # VIX 分析
        self.vix_state = classify_vix_regime(self.vix_df)
        print(f'\nVXX 环境: {self.vix_state["regime"]}')
        print(f'  VXX价格: {self.vix_state["vxx_price"]}')
        print(f'  RSI日: {self.vix_state["rsi_day"]} 周: {self.vix_state["rsi_week"]}')
        pct_rank = self.vix_state.get('pct_rank')
        pct_text = f'{pct_rank:.0f}%' if pct_rank is not None else 'N/A'
        print(f'  趋势: {self.vix_state["trend"]} 分位: {pct_text}')

        # 趋势分析
        self.trend_state = classify_trend(self.price_df)
        print(f'\n趋势状态: {self.trend_state["trend"]}')
        print(f'  价格: {self.trend_state["price"]} vs SMA20: {self.trend_state["price_vs_sma20"]:+.2f}%')
        print(f'  RSI日: {self.trend_state["rsi_day"]} 周: {self.trend_state["rsi_week"]}')
        print(f'  ATR: {self.trend_state["atr_pct"]:.2f}% (分位: {self.trend_state["atr_pct_rank"]:.0f}%)')
        print(f'  动量: {self.trend_state["momentum"]} 波动: {self.trend_state["vol_level"]}')

        # 综合状态
        self.market_state = classify_market_state(self.vix_state, self.trend_state)
        advice = get_market_advice(self.market_state, self.vix_state['regime'])
        print(f'\n综合市场状态: {self.market_state}')
        print(f'  建议: {advice["action"]} | 仓位: {advice["position"]}')
        print(f'  止损: {advice["stop_loss"]}')
        print(f'  说明: {advice["description"]}')

        return self.get_report()

    def get_report(self) -> dict:
        """获取完整报告"""
        advice = get_market_advice(self.market_state, self.vix_state['regime'])
        report = {
            'timestamp': datetime.now().isoformat(),
            'symbol': self.symbol,
            'market_state': self.market_state,
            'vix_regime': self.vix_state['regime'],
            'trend': self.trend_state['trend'],
            'momentum': self.trend_state['momentum'],
            'vol_level': self.trend_state['vol_level'],
            'vix_detail': self.vix_state,
            'trend_detail': self.trend_state,
            'advice': advice,
        }
        if self.vix_meta:
            report.update({
                'vxx_source': self.vix_meta.get('source'),
                'vxx_as_of': self.vix_meta.get('as_of'),
                'vxx_expected_as_of': self.vix_meta.get('expected_as_of'),
                'vxx_stale_sessions': self.vix_meta.get('stale_sessions'),
                'vxx_freshness': self.vix_meta.get('freshness'),
                'vxx_fetch_error': self.vix_meta.get('fetch_error'),
            })
            report['vix_detail'] = {
                **self.vix_state,
                **self.vix_meta,
            }
        return report

    def save_report(self) -> str:
        """保存报告到 JSON"""
        report = self.get_report()
        today = datetime.now().strftime('%Y-%m-%d')
        path = OUTPUT_DIR / f'market_state_{today}.json'
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return str(path)


# ─── 入口 ──────────────────────────────────────────
if __name__ == '__main__':
    # UTF-8 编码修复（仅主程序运行时）
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    
    import argparse
    parser = argparse.ArgumentParser(description='市场状态分类器')
    parser.add_argument('--symbol', '-s', default='SPY.US', help='标的代码（默认: SPY.US）')
    parser.add_argument('--save', action='store_true', help='保存报告')
    args = parser.parse_args()

    print('=' * 60)
    print(f'  市场状态分类器 {args.symbol}')
    print('=' * 60)

    clf = MarketStateClassifier(args.symbol)
    clf.load_data()
    report = clf.analyze()

    if args.save:
        path = clf.save_report()
        print(f'\n报告已保存: {path}')
