# xmm_sonnet_model.py
# 徐小明三层策略模型 — 三周期嵌套决策引擎
# 月线定方向 → 周线定节奏 → 日线定时机

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
import datetime
import json

# ══════════════════════════════════════════════════════
# 一、参数配置
# ══════════════════════════════════════════════════════

TREND_SHORT = 25
TREND_LONG  = 90

MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIGNAL = 9
MACD_SHRINK = 1.01

TD_REF      = 4
TD_COMPLETE = 9

# ═══ 回调入场参数 ═══
PULLBACK_RSI_HIGH  = 65    # RSI 回落上限
PULLBACK_RSI_LOW   = 40    # RSI 回落下限（太低说明不是回调而是下跌）
EXIT_COOLDOWN_DAYS = 10    # EXIT 后冷却期（交易日）
RSI_PERIOD         = 14

# ═══ 底部布局仓位矩阵（原矩阵，TD序列定仓）═══
POSITION_MATRIX = {
    ('low_deep', 'low'):     0.70,
    ('low_deep', 'neutral'): 0.50,
    ('low',      'low'):     0.50,
    ('low',      'neutral'): 0.30,
    ('low',      'high_low'):0.20,
    ('low',      'high_high'):0.10,
    ('neutral',  'low'):     0.20,
    ('neutral',  'neutral'): 0.10,
    ('high',     'high'):    0.00,
}

# ═══ 趋势跟随仓位矩阵（月周趋势状态定仓）═══
# key: (月线趋势分类, 周线趋势分类) → 仓位上限
# 趋势分类: strong_bull/bull → '大势强多', sideways → '盘整', bear/strong_bear → '短期偏空'
# bull 映射为 '短期偏多'（月线 bull 但非 strong_bull = 短期偏多）
TREND_POSITION_MATRIX = {
    ('大势强多', '大势强多'): 0.60,   # 月周双强多 → 重仓跟随
    ('大势强多', '短期偏多'): 0.40,   # 月强多 + 周偏多
    ('大势强多', '盘整'):     0.25,   # 月强多 + 周盘整 → 轻仓持有
    ('大势强多', '短期偏空'): 0.10,   # 月强多 + 周回调 → 防御
    ('短期偏多', '大势强多'): 0.40,
    ('短期偏多', '短期偏多'): 0.25,
    ('短期偏多', '盘整'):     0.15,
    ('盘整',     '大势强多'): 0.25,
    ('盘整',     '短期偏多'): 0.15,
}

VXX_MULTIPLIER = {
    'friendly': 1.0,
    'neutral':  0.8,
    'caution':  0.5,
    'danger':   0.2,
}

WATCHLIST = {
    'HK.HSI':    'HSI',
    'HK.HSTECH': 'HSTECH',
    'SH.000001': 'SSE',
    'US.SPY':    'SPY',
    'US.QQQ':    'QQQ',
    'US.DIA':    'DIA',
    'US.BABA':   'BABA',
    'US.ORCL':   'ORCL',
    'US.VXX':    'VXX',
}

# ══════════════════════════════════════════════════════
# 二、基础指标计算
# ══════════════════════════════════════════════════════

def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def calc_trend_channel(df: pd.DataFrame,
                       sp: int = TREND_SHORT,
                       lp: int = TREND_LONG) -> pd.DataFrame:
    """
    双EMA通道：EMA(High/Low, sp/lp)
    返回列：短顶 短底 长顶 长底 trend_status
    trend_status: strong_bull / bull / sideways / bear / strong_bear
    """
    out = df[['open','high','low','close','volume']].copy()
    out['短顶'] = _ema(df['high'], sp)
    out['短底'] = _ema(df['low'],  sp)
    out['长顶'] = _ema(df['high'], lp)
    out['长底'] = _ema(df['low'],  lp)

    def _status(row):
        c = row['close']
        if   c > row['长顶']: return 'strong_bull'
        elif c < row['长底']: return 'strong_bear'
        elif c > row['短顶']: return 'bull'
        elif c < row['短底']: return 'bear'
        else:                 return 'sideways'

    out['trend_status'] = out.apply(_status, axis=1)

    # 穿越信号（每轮首根）
    prev_status = out['trend_status'].shift(1)
    out['cross_long_up']   = (out['trend_status'] == 'strong_bull') & (prev_status != 'strong_bull')
    out['cross_long_down'] = (out['trend_status'] == 'strong_bear') & (prev_status != 'strong_bear')
    out['cross_short_up']  = (out['trend_status'].isin(['bull','strong_bull'])) & \
                              (~prev_status.isin(['bull','strong_bull']))
    out['cross_short_down']= (out['trend_status'].isin(['bear','strong_bear'])) & \
                              (~prev_status.isin(['bear','strong_bear']))
    return out


def calc_macd_structure(df: pd.DataFrame,
                        fast: int = MACD_FAST,
                        slow: int = MACD_SLOW,
                        sig:  int = MACD_SIGNAL,
                        shrink: float = MACD_SHRINK) -> pd.DataFrame:
    """
    MACD跨段背离结构检测
    底部四状态机：钝化 → 结构形成 → 再钝化 → 消失
    顶部对称逻辑
    返回布尔列：底部钝化 底部结构 底再次钝化 底钝化消失 顶部钝化 顶部结构 顶再次钝化 顶钝化消失
    """
    close = df['close'].values
    n = len(close)

    diff = _ema(df['close'], fast) - _ema(df['close'], slow)
    dea  = _ema(diff, sig)
    hist = (diff - dea) * 2

    diff_v = diff.values
    dea_v  = dea.values
    hist_v = hist.values

    # 段极值追踪
    bot_div      = np.zeros(n, bool)
    bot_struct   = np.zeros(n, bool)
    bot_repeat   = np.zeros(n, bool)
    bot_gone     = np.zeros(n, bool)
    top_div      = np.zeros(n, bool)
    top_struct   = np.zeros(n, bool)
    top_repeat   = np.zeros(n, bool)
    top_gone     = np.zeros(n, bool)

    # 底部状态机
    b_state      = 'none'   # none / div / struct / repeat
    b_low_price  = np.inf
    b_low_hist   = 0.0
    b_prev_hist  = None

    # 顶部状态机
    t_state      = 'none'
    t_high_price = -np.inf
    t_high_hist  = 0.0
    t_prev_hist  = None

    for i in range(1, n):
        h = hist_v[i]
        d = diff_v[i]
        c = close[i]

        # ── 底部逻辑 ──────────────────────────────────
        if h < 0:
            # 绿柱区间
            if b_state == 'none':
                # 直接底钝化：当前绿柱 < 前段绿柱极值（零轴下背离）
                if b_prev_hist is not None and abs(h) < abs(b_prev_hist) * shrink:
                    b_state     = 'div'
                    b_low_price = c
                    b_low_hist  = h
                    bot_div[i]  = True
                else:
                    b_low_price = min(b_low_price, c)
                    b_low_hist  = min(b_low_hist, h) if b_low_hist != 0 else h
            elif b_state == 'div':
                if c < b_low_price:
                    # 价格创新低但绿柱收缩 → 底部结构形成
                    if abs(h) < abs(b_low_hist) * shrink:
                        b_state      = 'struct'
                        bot_struct[i]= True
                    else:
                        b_low_price = c
                        b_low_hist  = h
                        bot_div[i]  = True
                else:
                    bot_div[i] = True
            elif b_state == 'struct':
                # 再次钝化
                if abs(h) < abs(b_low_hist) * shrink:
                    b_state      = 'repeat'
                    bot_repeat[i]= True
                else:
                    bot_struct[i]= True
            elif b_state == 'repeat':
                bot_repeat[i] = True
        else:
            # 红柱区间 → 底部结构消失检测
            if b_state in ('struct', 'repeat', 'div'):
                bot_gone[i] = True
                b_state     = 'none'
            b_prev_hist = b_low_hist if b_low_hist != 0 else None
            b_low_price = np.inf
            b_low_hist  = 0.0

        # ── 顶部逻辑 ──────────────────────────────────
        if h > 0:
            if t_state == 'none':
                if t_prev_hist is not None and h < t_prev_hist * shrink:
                    t_state      = 'div'
                    t_high_price = c
                    t_high_hist  = h
                    top_div[i]   = True
                else:
                    t_high_price = max(t_high_price, c)
                    t_high_hist  = max(t_high_hist, h) if t_high_hist != 0 else h
            elif t_state == 'div':
                if c > t_high_price:
                    if h < t_high_hist * shrink:
                        t_state       = 'struct'
                        top_struct[i] = True
                    else:
                        t_high_price = c
                        t_high_hist  = h
                        top_div[i]   = True
                else:
                    top_div[i] = True
            elif t_state == 'struct':
                if h < t_high_hist * shrink:
                    t_state       = 'repeat'
                    top_repeat[i] = True
                else:
                    top_struct[i] = True
            elif t_state == 'repeat':
                top_repeat[i] = True
        else:
            if t_state in ('struct', 'repeat', 'div'):
                top_gone[i] = True
                t_state     = 'none'
            t_prev_hist  = t_high_hist if t_high_hist != 0 else None
            t_high_price = -np.inf
            t_high_hist  = 0.0

    result = pd.DataFrame({
        '底部钝化':   bot_div,
        '底部结构':   bot_struct,
        '底再次钝化': bot_repeat,
        '底钝化消失': bot_gone,
        '顶部钝化':   top_div,
        '顶部结构':   top_struct,
        '顶再次钝化': top_repeat,
        '顶钝化消失': top_gone,
        'diff':       diff_v,
        'dea':        dea_v,
        'hist':       hist_v,
    }, index=df.index)
    return result


def calc_td_both(close: pd.Series,
                 ref: int = TD_REF) -> tuple:
    """
    双向TD序列
    返回 (high_n, low_n) numpy arrays
    high_n[i]: 连续 close > close[i-ref] 的计数
    low_n[i]:  连续 close < close[i-ref] 的计数
    """
    n = len(close)
    high_n = np.zeros(n, np.int32)
    low_n  = np.zeros(n, np.int32)
    c = close.values
    hc, lc = 0, 0
    for i in range(n):
        if i >= ref:
            if   c[i] > c[i - ref]: hc += 1; lc = 0
            elif c[i] < c[i - ref]: lc += 1; hc = 0
            else:                   hc  = 0;  lc = 0
        high_n[i] = hc
        low_n[i]  = lc
    return high_n, low_n


# ══════════════════════════════════════════════════════
# 三、重采样工具
# ══════════════════════════════════════════════════════

def resample_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """日线 → 周线（周五收盘）"""
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.copy(); df.index = pd.to_datetime(df.index)
    w = df.resample('W-FRI').agg(
        open=('open','first'), high=('high','max'),
        low=('low','min'),   close=('close','last'),
        volume=('volume','sum')
    ).dropna()
    return w


def resample_to_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """日线 → 月线（月末收盘）"""
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.copy(); df.index = pd.to_datetime(df.index)
    m = df.resample('ME').agg(
        open=('open','first'), high=('high','max'),
        low=('low','min'),   close=('close','last'),
        volume=('volume','sum')
    ).dropna()
    return m


# ══════════════════════════════════════════════════════
# 四、仓位与风控辅助
# ══════════════════════════════════════════════════════

def _td_label(low_n: int, high_n: int) -> str:
    """将TD计数映射为底部布局仓位矩阵键"""
    if   low_n  >= TD_COMPLETE: return 'low_deep'
    elif low_n  >  0:           return 'low'
    elif high_n >= TD_COMPLETE: return 'high_high'
    elif high_n >= 2:           return 'high_low'
    else:                       return 'neutral'


def _trend_label(trend_status: str) -> str:
    """将 trend_status 映射为趋势跟随仓位矩阵键"""
    if trend_status == 'strong_bull': return '大势强多'
    elif trend_status == 'bull':      return '短期偏多'
    elif trend_status == 'sideways':  return '盘整'
    else:                             return '短期偏空'  # bear / strong_bear


def get_position_limit(m_low: int, m_high: int,
                       w_low: int, w_high: int) -> float:
    """查底部布局仓位矩阵，返回 0.0~0.70"""
    mk = _td_label(m_low, m_high)
    wk = _td_label(w_low, w_high)
    # 精确匹配 → 前缀匹配 → 默认0.10
    key = (mk, wk)
    if key in POSITION_MATRIX:
        return POSITION_MATRIX[key]
    # 月线 high 系列 → 0
    if mk in ('high_low', 'high_high', 'low_deep') and wk in ('high_low','high_high'):
        return 0.0
    return 0.10


def get_trend_position_limit(m_ts: str, w_ts: str) -> float:
    """查趋势跟随仓位矩阵，返回 0.10~0.60"""
    mk = _trend_label(m_ts)
    wk = _trend_label(w_ts)
    key = (mk, wk)
    return TREND_POSITION_MATRIX.get(key, 0.10)


def get_vxx_coeff(vxx_low_n: int, vxx_high_n: int) -> tuple:
    """返回 (系数, 状态标签)"""
    if   vxx_low_n  >= 3: return VXX_MULTIPLIER['friendly'], 'friendly'
    elif vxx_high_n >= 4: return VXX_MULTIPLIER['danger'],   'danger'
    elif vxx_high_n >= 2: return VXX_MULTIPLIER['caution'],  'caution'
    else:                 return VXX_MULTIPLIER['neutral'],   'neutral'


# ══════════════════════════════════════════════════════
# 五、三周期预计算器
# ══════════════════════════════════════════════════════

class XMMSonnetPrecomputed:
    """
    一次性预计算月/周/日三周期全量指标，
    get(i) 以 O(1) 返回第 i 根日线的三层决策信号。
    """

    def __init__(self, daily_df: pd.DataFrame,
                 vxx_df: Optional[pd.DataFrame] = None,
                 sp: int = TREND_SHORT, lp: int = TREND_LONG,
                 verbose: bool = True):

        if verbose: print("  [Sonnet] 预计算开始...", flush=True)

        # ── 重采样 ──────────────────────────────────
        self.daily_df   = daily_df
        self.weekly_df  = resample_to_weekly(daily_df)
        self.monthly_df = resample_to_monthly(daily_df)

        nd = len(daily_df)
        nw = len(self.weekly_df)
        nm = len(self.monthly_df)
        if verbose:
            print(f"  日线{nd}条  周线{nw}条  月线{nm}条", flush=True)

        # 不再自适应缩短EMA参数 — 数据不足直接报错
        # 降级参数比没有信号更危险
        m_sp, m_lp = sp, lp
        w_sp, w_lp = sp, lp

        # 数据充足性检查
        # EMA(n) 需要 ~n*1.2 条数据预热，最低要求 n+10（EMA指数衰减，n+10已收敛>99%）
        min_monthly  = lp + 10   # 月线最低要求 EMA(lp)+10
        min_weekly   = lp + 10   # 周线最低要求 EMA(lp)+10
        if nm < min_monthly:
            raise ValueError(
                f'月线数据不足: {nm}条 < 需{min_monthly}条(EMA{lp}需至少{min_monthly}条预热)。'
                f'请增加日线数据。当前日线{nd}条→月线约{nd//21}条。')
        if nw < min_weekly:
            raise ValueError(
                f'周线数据不足: {nw}条 < 需{min_weekly}条(EMA{lp}需至少{min_weekly}条预热)。'
                f'请增加日线数据。当前日线{nd}条→周线约{nd//5}条。')
        if verbose:
            print(f'  月线EMA({m_sp},{m_lp}) 周线EMA({w_sp},{w_lp}) — 参数正常 ✓', flush=True)

        # ── 月线指标 ────────────────────────────────
        self.m_trend  = calc_trend_channel(self.monthly_df, m_sp, m_lp)
        self.m_macd   = calc_macd_structure(self.monthly_df)
        self.m_high_n, self.m_low_n = calc_td_both(self.monthly_df['close'])

        # ── 周线指标 ────────────────────────────────
        self.w_trend  = calc_trend_channel(self.weekly_df, w_sp, w_lp)
        self.w_macd   = calc_macd_structure(self.weekly_df)
        self.w_high_n, self.w_low_n = calc_td_both(self.weekly_df['close'])

        # ── 日线指标 ────────────────────────────────
        self.d_trend  = calc_trend_channel(daily_df, sp, lp)
        self.d_macd   = calc_macd_structure(daily_df)
        self.d_high_n, self.d_low_n = calc_td_both(daily_df['close'])

        # ── 日线 RSI（用于回调入场判断）════════════
        self.d_rsi = self._calc_rsi(daily_df['close'], RSI_PERIOD)

        # ── EXIT 冷却追踪 ═════════════════════════
        self._last_exit_idx = -EXIT_COOLDOWN_DAYS - 1  # 上次EXIT的日线索引

        # ── VXX ─────────────────────────────────────
        self.vxx_high_n = np.zeros(nd, np.int32)
        self.vxx_low_n  = np.zeros(nd, np.int32)
        if vxx_df is not None and len(vxx_df) >= TD_REF + 1:
            vh, vl = calc_td_both(vxx_df['close'])
            # 按日期对齐
            vxx_dates = vxx_df.index
            daily_dates = daily_df.index
            for i, dt in enumerate(daily_dates):
                pos = vxx_dates.searchsorted(dt, side='right') - 1
                if 0 <= pos < len(vh):
                    self.vxx_high_n[i] = vh[pos]
                    self.vxx_low_n[i]  = vl[pos]

        # ── 日→周/月 索引映射 ────────────────────────
        self._build_mapping()
        if verbose: print("  [Sonnet] 预计算完成 ✓", flush=True)

    def _build_mapping(self):
        daily_dates   = self.daily_df.index
        weekly_dates  = self.weekly_df.index
        monthly_dates = self.monthly_df.index
        n = len(daily_dates)
        self._d2w = np.zeros(n, np.int32)
        self._d2m = np.zeros(n, np.int32)
        for i in range(n):
            d = daily_dates[i]
            wi = int(np.searchsorted(weekly_dates,  d, side='right')) - 1
            mi = int(np.searchsorted(monthly_dates, d, side='right')) - 1
            self._d2w[i] = max(0, wi)
            self._d2m[i] = max(0, mi)

    @staticmethod
    def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """RSI 计算"""
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        return (100 - 100 / (1 + rs)).fillna(50.0)

    # ── 核心决策 ────────────────────────────────────

    def get(self, i: int) -> dict:
        """三层决策引擎，返回信号字典"""
        wi = int(self._d2w[i])
        mi = int(self._d2m[i])

        # 月线
        m_ts  = self.m_trend['trend_status'].iloc[mi]
        m_ln  = int(self.m_low_n[mi])
        m_hn  = int(self.m_high_n[mi])
        m_ms  = self.m_macd.iloc[mi]

        # 周线
        w_ts  = self.w_trend['trend_status'].iloc[wi]
        w_ln  = int(self.w_low_n[wi])
        w_hn  = int(self.w_high_n[wi])
        w_ms  = self.w_macd.iloc[wi]

        # 日线
        d_ts  = self.d_trend['trend_status'].iloc[i]
        d_ln  = int(self.d_low_n[i])
        d_hn  = int(self.d_high_n[i])
        d_ms  = self.d_macd.iloc[i]
        d_cld = bool(self.d_trend['cross_long_down'].iloc[i])
        d_wld = bool(self.w_trend['cross_long_down'].iloc[wi])

        # VXX
        vxx_coeff, vxx_st = get_vxx_coeff(
            int(self.vxx_low_n[i]), int(self.vxx_high_n[i]))

        # ══ 第一层：月线趋势过滤 ══════════════════════
        if m_ts == 'strong_bear':
            return self._sig('EXIT', 0.0, vxx_coeff, vxx_st,
                             '月线弱空→强制空仓', '-', '-',
                             m_ts, w_ts, d_ts, m_ln, m_hn, w_ln, w_hn, d_ln, d_hn, d_ms, i)

        # 月线长空穿越 → 清仓
        if bool(self.m_trend['cross_long_down'].iloc[mi]):
            self._last_exit_idx = i
            return self._sig('EXIT', 0.0, vxx_coeff, vxx_st,
                             '月线长空穿越→清仓', '-', '-',
                             m_ts, w_ts, d_ts, m_ln, m_hn, w_ln, w_hn, d_ln, d_hn, d_ms, i)

        layer1 = f'月线{m_ts}→通过'

        # ── EXIT 冷却期检查 ═══════════════════════
        cooldown_active = (i - self._last_exit_idx) < EXIT_COOLDOWN_DAYS
        if cooldown_active:
            return self._sig('HOLD', 0.0, vxx_coeff, vxx_st,
                             layer1, f'冷却期(剩{EXIT_COOLDOWN_DAYS-(i-self._last_exit_idx)}天)', '-',
                             m_ts, w_ts, d_ts, m_ln, m_hn, w_ln, w_hn, d_ln, d_hn, d_ms, i)

        # 顶部结构标记（用于底部模式判断）
        has_top_struct = bool(d_ms['顶部结构'])

        # ══ 第二层：结构过滤 ════════════════════════════
        # 先检查底部加仓机会（优先级高于EXIT）
        # 当月线/周线仍多，日线超卖(低N≥6) → 底部买入机会
        bottom_buy = False
        if d_ts == 'strong_bear' and d_ln >= 6 and m_ts in ('strong_bull', 'bull', 'sideways'):
            # 日线深度回调但月线未走坏 → 底部机会
            bottom_buy = True
            layer2 = '底部超卖买入(日低N≥6)'
            # 直接返回买入信号，跳过EXIT检查
            base_limit = 0.50
            final_limit = round(base_limit * vxx_coeff, 3)
            layer3 = f'底部买入≤50%(日低N={d_ln})'
            return self._sig('BUY', final_limit, vxx_coeff, vxx_st,
                             layer1, layer2, layer3,
                             m_ts, w_ts, d_ts, m_ln, m_hn, w_ln, w_hn, d_ln, d_hn, d_ms, i)

        # 出场优先级（高→低）
        # P1: 底部结构消失（已持仓才清仓）
        if bool(d_ms['底钝化消失']) and not bool(d_ms['底部结构']):
            self._last_exit_idx = i
            return self._sig('EXIT', 0.0, vxx_coeff, vxx_st,
                             layer1, '底部结构消失→清仓', '-',
                             m_ts, w_ts, d_ts, m_ln, m_hn, w_ln, w_hn, d_ln, d_hn, d_ms, i)

        # P2: 日/周长空穿越
        if (d_cld or d_wld) and m_ts not in ('strong_bull', 'bull'):
            self._last_exit_idx = i
            return self._sig('EXIT', 0.0, vxx_coeff, vxx_st,
                             layer1, '日/周长空穿越(月线弱)→清仓', '-',
                             m_ts, w_ts, d_ts, m_ln, m_hn, w_ln, w_hn, d_ln, d_hn, d_ms, i)

        # P3: 顶部结构 → 减仓
        if bool(d_ms['顶部结构']):
            return self._sig('REDUCE', 0.5, vxx_coeff, vxx_st,
                             layer1, '日线顶部结构→减仓50%', '-',
                             m_ts, w_ts, d_ts, m_ln, m_hn, w_ln, w_hn, d_ln, d_hn, d_ms, i)

        # 入场条件标记
        has_bot_struct  = bool(d_ms['底部结构'])
        has_bot_repeat  = bool(d_ms['底再次钝化'])
        has_w_bot_struct= bool(w_ms['底部结构'])
        has_bot_div     = bool(d_ms['底部钝化'])

        # ══ 第三层：双模式仓位体系 ══════════════════════
        # 判断当前是"趋势跟随"还是"底部布局"模式
        m_clu = bool(self.m_trend['cross_long_up'].iloc[mi])
        w_clu = bool(self.w_trend['cross_long_up'].iloc[wi])
        d_clu = bool(self.d_trend['cross_long_up'].iloc[i])
        
        # ── 新增：回调入场条件 ═════════════════════
        # 当趋势已对齐（EMAs bullish）但无 crossover 时，
        # 用 RSI 回落 + 价格接近 EMA 短底 触发入场
        d_rsi_val = float(self.d_rsi.iloc[i]) if i < len(self.d_rsi) else 50.0
        d_short_bottom = float(self.d_trend['短底'].iloc[i])
        d_close_val = float(self.daily_df['close'].iloc[i])
        
        # 价格与EMA短底的相对距离
        proximity_ratio = (d_short_bottom - d_close_val) / d_short_bottom if d_short_bottom > 0 else 0
        
        pullback_entry = False
        if (m_ts in ('strong_bull', 'bull')
            and w_ts in ('strong_bull', 'bull', 'sideways')
            and not has_bot_struct and not has_bot_repeat
            and not has_top_struct  # 无顶部结构
            and not (d_clu or w_clu or m_clu)  # 无穿越（已有穿越走原逻辑）
            and PULLBACK_RSI_LOW <= d_rsi_val <= PULLBACK_RSI_HIGH  # RSI 温和回落
            and -0.02 <= proximity_ratio <= 0.01  # 价格在EMA短底附近（-2%到+1%）
            and d_ts in ('bear', 'sideways', 'bull')  # 日线不是强空（强空走底部逻辑）
        ):
            pullback_entry = True
        
        is_trend_mode = (
            m_ts in ('strong_bull', 'bull')
            and not has_bot_struct
            and not has_bot_repeat
            and (d_clu or w_clu or m_clu or pullback_entry)  # ← 加入回调入场
        )
        
        is_bottom_mode = (
            has_bot_struct or has_bot_repeat
            or (has_w_bot_struct and not has_top_struct)
        )
        
        if not is_trend_mode and not is_bottom_mode:
            reason = '底钝化等待结构' if has_bot_div else '无入场信号→持有'
            return self._sig('HOLD', 0.0, vxx_coeff, vxx_st,
                             layer1, reason, '-',
                             m_ts, w_ts, d_ts, m_ln, m_hn, w_ln, w_hn, d_ln, d_hn, d_ms, i)
        
        # 根据模式选择仓位矩阵
        if is_trend_mode:
            mode = 'trend'
            base_limit = get_trend_position_limit(m_ts, w_ts)
            if pullback_entry:
                layer2 = f'【趋势回调】{m_ts}/{w_ts} RSI={d_rsi_val:.0f}'
            else:
                layer2 = f'【趋势跟随】{m_ts}/{w_ts}'
        else:  # is_bottom_mode
            mode = 'bottom'
            base_limit = get_position_limit(m_ln, m_hn, w_ln, w_hn)
            layer2 = ('【底部布局】日线底部结构' if has_bot_struct else
                      '【底部布局】底再次钝化' if has_bot_repeat else
                      '【底部布局】周线底部结构')

        # 日线过热修正（两种模式通用）
        if d_hn >= TD_COMPLETE:  # 高9
            base_limit = min(base_limit, 0.05)
            layer3 = f'日高9→压至5%'
        elif d_hn >= 6:  # 高6~8
            base_limit *= 0.5
            layer3 = f'日高{d_hn}→×0.5不追'
        else:
            layer3 = f'{mode}模式 仓位上限{base_limit*100:.0f}%'

        # VXX 调整
        final_limit = round(base_limit * vxx_coeff, 3)
        if vxx_st != 'neutral':
            layer3 += f' VXX:{vxx_st}×{vxx_coeff}'

        # 信号强度
        if has_bot_struct and has_w_bot_struct:
            sig = 'STRONG_BUY'
        else:
            sig = 'BUY'

        return self._sig(sig, final_limit, vxx_coeff, vxx_st,
                         layer1, layer2, layer3,
                         m_ts, w_ts, d_ts, m_ln, m_hn, w_ln, w_hn, d_ln, d_hn, d_ms, i)

    def _sig(self, signal, pos_limit, vxx_coeff, vxx_st,
             l1, l2, l3,
             m_ts, w_ts, d_ts,
             m_ln, m_hn, w_ln, w_hn, d_ln, d_hn,
             d_ms, i):
        close_arr = self.daily_df['close'].values
        return {
            'signal':        signal,
            'position_limit':pos_limit,
            'vxx_coeff':     vxx_coeff,
            'vxx_status':    vxx_st,
            'layer1':        l1,
            'layer2':        l2,
            'layer3':        l3,
            'monthly_trend': m_ts,
            'weekly_trend':  w_ts,
            'daily_trend':   d_ts,
            'm_low_n': m_ln, 'm_high_n': m_hn,
            'w_low_n': w_ln, 'w_high_n': w_hn,
            'd_low_n': d_ln, 'd_high_n': d_hn,
            'close':         float(close_arr[i]),
            'index':         i,
            'bot_struct':    bool(d_ms['底部结构']),
            'bot_div':       bool(d_ms['底部钝化']),
            'bot_repeat':    bool(d_ms['底再次钝化']),
            'bot_gone':      bool(d_ms['底钝化消失']),
            'top_struct':    bool(d_ms['顶部结构']),
            'top_div':       bool(d_ms['顶部钝化']),
        }


# ══════════════════════════════════════════════════════
# 六、模型主类
# ══════════════════════════════════════════════════════

class XMMSonnetModel:
    """
    徐小明策略 — Sonnet三周期版 v1.0
    核心信条：月线定方向 > 结构修边 > 序列定仓位
    """

    def __init__(self,
                 name: str = '徐小明三周期_Sonnet_v1.0',
                 sp: int = TREND_SHORT,
                 lp: int = TREND_LONG):
        self.name = name
        self.sp   = sp
        self.lp   = lp

    def precompute(self, daily_df: pd.DataFrame,
                   vxx_df: Optional[pd.DataFrame] = None,
                   verbose: bool = True) -> XMMSonnetPrecomputed:
        return XMMSonnetPrecomputed(daily_df, vxx_df, self.sp, self.lp, verbose)

    def analyze(self, daily_df: pd.DataFrame,
                vxx_df: Optional[pd.DataFrame] = None) -> dict:
        """分析最新一根K线，返回信号字典"""
        min_bars = max(self.lp + 5, 60)
        if daily_df is None or len(daily_df) < min_bars:
            return {'signal': 'HOLD', 'position_limit': 0.0,
                    'layer1': f'数据不足(需{min_bars}条)',
                    'layer2': '-', 'layer3': '-'}
        pc = self.precompute(daily_df, vxx_df, verbose=False)
        return pc.get(len(daily_df) - 1)

    def to_json(self) -> str:
        return json.dumps({'name': self.name,
                           'params': {'sp': self.sp, 'lp': self.lp}},
                          ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════
# 七、快速自测
# ══════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print('=' * 60)
    print('XMMSonnetModel 自测')
    print('=' * 60)

    # 加载 SPY 数据
    import json as _json
    with open('E:/quant/scanner/cache/SPY_US_1y.json', 'r') as f:
        raw = _json.load(f)
    df = pd.DataFrame(raw)
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.sort_values('datetime').set_index('datetime')
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna()
    print(f'数据: {len(df)}条  {df.index[0].date()} ~ {df.index[-1].date()}')

    model = XMMSonnetModel()
    pc    = model.precompute(df)
    sig   = pc.get(len(df) - 1)

    print(f'\n最新信号 ({df.index[-1].date()}):')
    print(f'  信号:     {sig["signal"]}')
    print(f'  仓位上限: {sig["position_limit"]*100:.1f}%')
    print(f'  第一层:   {sig["layer1"]}')
    print(f'  第二层:   {sig["layer2"]}')
    print(f'  第三层:   {sig["layer3"]}')
    print(f'  月线趋势: {sig["monthly_trend"]}  月低N={sig["m_low_n"]}  月高N={sig["m_high_n"]}')
    print(f'  周线趋势: {sig["weekly_trend"]}  周低N={sig["w_low_n"]}  周高N={sig["w_high_n"]}')
    print(f'  日线趋势: {sig["daily_trend"]}  日低N={sig["d_low_n"]}  日高N={sig["d_high_n"]}')
    print(f'  底部结构: {sig["bot_struct"]}  底钝化: {sig["bot_div"]}')
    print(f'  顶部结构: {sig["top_struct"]}  顶钝化: {sig["top_div"]}')
    print(f'  VXX:      {sig["vxx_status"]} ×{sig["vxx_coeff"]}')
