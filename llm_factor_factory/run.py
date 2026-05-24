# -*- coding: utf-8 -*-
"""
LLM Factor Factory v1.0
闭环：LLM生成 ?IC验证 ?排名 ?加入因子??集成到信号系?"""
import sys, io, json, re, warnings, os, time
from datetime import datetime
import numpy as np, pandas as pd
import scipy.stats as st

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'C:\Users\RoyGoode\.workbuddy\skills\nvidia-api\scripts')
from nvidia_api import nvidia_llm
warnings.filterwarnings('ignore')

# ─── 目录 ─────────────────────────────────────────────────
BASE_DIR = r'E:\quant\llm_factor_factory'
os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(f'{BASE_DIR}\output', exist_ok=True)

# ─── 数据加载（TickFlow）──────────────────────────────────
def load_data():
    """加载多市场数据（带限流重试）"""
    import tickflow_env  # noqa: auto-inject TICKFLOW_API_KEY
from tickflow import TickFlow
    tf = TickFlow() if os.environ.get('TICKFLOW_API_KEY') else TickFlow.free()  #  API keyʹѲ
    datasets = {}
    order = ['SPY.US', '00700.HK', '01810.HK', '03690.HK', '09618.HK']

    for sym in order:
        for attempt in range(3):
            try:
                d = tf.klines.get(sym, period='1d', count=300)
                if not d or 'close' not in d or len(d['close']) < 50:
                    print(f'  {sym}: insufficient data'); break
                rows = [{'close': float(d['close'][i]),
                         'high': float(d.get('high', d['close'])[i]),
                         'low': float(d.get('low', d['close'])[i]),
                         'volume': float(d.get('volume', [1]*len(d['close']))[i])}
                        for i in range(len(d['timestamp']))]
                name = sym.split('.')[0]
                datasets[name] = pd.DataFrame(rows)
                print(f'  {name}: {len(datasets[name])} days')
                time.sleep(7)  # 6s/分钟限流安全间隔
                break
            except Exception as e:
                err_str = str(e)
                if '限流' in err_str and attempt < 2:
                    wait = int(re.search(r'(\d+)ms', err_str).group(1)) / 1000 + 1
                    print(f'  {sym}: rate limited, waiting {wait:.0f}s...')
                    time.sleep(wait)
                else:
                    print(f'  {sym}: {e}'); break
    return datasets

# ─── 指标?──────────────────────────────────────────────
def build_features(df):
    c = df['close'].values.astype(float)
    h = df['high'].values.astype(float)
    l = df['low'].values.astype(float)
    v = df['volume'].values.astype(float)
    n = len(c)

    def rsi(arr, n=14):
        d = np.diff(arr); g = np.where(d>0,d,0.0); lo = np.where(d<0,-d,0.0)
        r = np.full(len(arr), 50.0)
        if len(arr) <= n: return r
        ag, al = np.mean(g[:n]), np.mean(lo[:n])
        r[n] = 50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
        for i in range(n+1, len(arr)):
            ag=(ag*(n-1)+g[i-1])/n; al=(al*(n-1)+lo[i-1])/n
            r[i]=50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
        return r

    def ema_arr(arr, n):
        a=2/(n+1); s=np.full(len(arr),np.nan)
        if len(arr)<n: return s
        s[n-1]=float(np.mean(arr[:n]))
        for i in range(n,len(arr)): s[i]=a*float(arr[i])+(1-a)*s[i-1]
        return s

    def sma_arr(arr, n):
        s=np.full(len(arr),np.nan)
        for i in range(n-1,len(arr)): s[i]=float(np.mean(arr[i-n+1:i+1]))
        return s

    ret1 = np.diff(c, prepend=c[0])/(np.roll(c,1)+1e-10); ret1[0]=0
    ret5 = np.array([c[i]/c[max(0,i-5)]-1 for i in range(n)])
    ret20 = np.array([c[i]/c[max(0,i-20)]-1 for i in range(n)])

    atr = np.full(n, np.nan)
    tr = [max(h[0]-l[0], abs(h[0]-c[0]), abs(l[0]-c[0]))]
    for i in range(1,n):
        tr.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    for i in range(14,n): atr[i]=np.mean(tr[max(0,i-14):i+1])

    macd_e=ema_arr(c,12); macd_s=ema_arr(c,26); macd=macd_e-macd_s
    macd_sig=np.full(n, float(macd[-1]))
    a=2/10
    for i in range(n-2,-1,-1): macd_sig[i]=a*float(macd[i])+(1-a)*macd_sig[i+1]
    macd_hist = macd - macd_sig

    bb_mid=sma_arr(c,20); std20=np.full(n,np.nan)
    for i in range(19,n): std20[i]=float(np.std(c[i-19:i+1]))
    bb_up=bb_mid+2*std20; bb_dn=bb_mid-2*std20

    sma20=sma_arr(c,20); sma50=sma_arr(c,50); sma200=sma_arr(c,200)
    vol_ma20=sma_arr(v,20); vol_ratio=v/(vol_ma20+1e-10)
    rsi14=rsi(c,14)
    rsi7=rsi(c,7)
    e20=ema_arr(c,20); e50=ema_arr(c,50); e200=ema_arr(c,200)

    return {
        'close': c, 'high': h, 'low': l, 'volume': v,
        'rsi_7': rsi7, 'rsi_14': rsi14,
        'sma_20': sma20, 'sma_50': sma50, 'sma_200': sma200,
        'ema_20': e20, 'ema_50': e50, 'ema_200': e200,
        'atr_14': atr,
        'macd_hist': macd_hist, 'macd': macd,
        'bb_mid': bb_mid, 'bb_up': bb_up, 'bb_dn': bb_dn,
        'ret_1d': ret1, 'ret_5d': ret5, 'ret_20d': ret20,
        'volume_ma20': vol_ma20, 'volume_ratio': vol_ratio,
        'price_sma20_pct': (c-sma20)/(sma20+1e-10),
        'price_sma50_pct': (c-sma50)/(sma50+1e-10),
        'atr_pct': atr/(c+1e-10),
    }

def fwd_returns(close, horizons=[5]):
    """计算未来收益?""
    results = {}
    for h in horizons:
        fwd = np.full(len(close), np.nan)
        for i in range(len(close)-h):
            fwd[i] = close[i+h]/close[i] - 1
        results[f'h_fwd_{h}d'] = fwd
    return results

# ─── 因子验证 ─────────────────────────────────────────────
def parse_json_reply(text):
    """?LLM 输出中提?JSON"""
    m = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
    if m: text = m.group(1)
    m = re.search(r'(\[\s*\{.*\}\s*\])', text, re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    m = re.search(r'(\{.*\})', text, re.DOTALL)
    if m:
        try: return [json.loads(m.group(1))]
        except: pass
    return None

def safe_eval_formula(formula, feats, fwd_arr):
    """安全求?+ IC 计算"""
    try:
        f = formula.strip()
        # 预处理：sma_arr ?ema_arr 直接注入
        def _sma(arr, n=20):
            arr = np.array(arr, dtype=float)
            s = np.full(len(arr), np.nan)
            for i in range(n-1, len(arr)): s[i] = float(np.mean(arr[i-n+1:i+1]))
            return s
        def _ema(arr, n=20):
            arr = np.array(arr, dtype=float)
            a=2/(n+1); s=np.full(len(arr),np.nan)
            if len(arr)<n: return s
            s[n-1]=float(np.mean(arr[:n]))
            for i in range(n,len(arr)): s[i]=a*float(arr[i])+(1-a)*s[i-1]
            return s
        namespace = {"__builtins__":{}, "np":np, "sma_arr": _sma, "ema_arr": _ema}
        namespace.update({k: np.array(v, dtype=float) if not isinstance(v, str) else v
                          for k, v in feats.items()})
        vals = eval(f, namespace)
        vals = np.array(vals, dtype=float)
        vals = np.nan_to_num(vals, nan=np.nanmedian(vals),
                              posinf=np.nanpercentile(vals,99),
                              neginf=np.nanpercentile(vals,1))
        valid = ~np.isnan(vals) & ~np.isnan(fwd_arr)
        if valid.sum() < 30: return None, f'valid={valid.sum()}'
        ic = float(st.pearsonr(vals[valid], fwd_arr[valid])[0])
        ric = float(st.spearmanr(vals[valid], fwd_arr[valid])[0])
        idx = np.argsort(vals[valid])
        n = valid.sum(); hi = fwd_arr[valid][idx[-max(1,int(n*0.2)):]].mean()
        lo = fwd_arr[valid][idx[:max(1,int(n*0.2))]].mean()
        spread = (hi - lo) * 100
        return {'ic': round(ic,4), 'ric': round(ric,4), 'spread': round(spread,3),
                'valid_n': int(valid.sum())}, 'ok'
    except Exception as e:
        return None, str(e)[:80]

def validate_factor(formula, datasets, horizon=5):
    """多市?IC 验证"""
    results = {}
    all_rics = []; all_ics = []

    for name, df in datasets.items():
        feats = build_features(df)
        close = feats['close']
        fwd_arr = np.full(len(close), np.nan)
        for i in range(len(close)-horizon): fwd_arr[i] = close[i+horizon]/close[i] - 1
        vr, err = safe_eval_formula(formula, feats, fwd_arr)
        results[name] = {'err': err} if err else vr
        if vr:
            all_rics.append(vr['ric']); all_ics.append(vr['ic'])

    avg_ic = round(float(np.mean(all_ics)),4) if all_ics else 0
    avg_ric = round(float(np.mean(all_rics)),4) if all_rics else 0
    # 跨市场一致?    n_pos = sum(1 for r in all_rics if r > 0)
    consistency = n_pos / max(len(all_rics),1)  # 0~1，越高越一?    return {
        'ic_avg': avg_ic, 'ric_avg': avg_ric,
        'markets': results,
        'consistency': round(consistency, 2),
        'n_markets': len(all_rics),
    }

# ─── LLM 因子生成 ────────────────────────────────────────
FEATURE_LIST = [
    'close', 'high', 'low', 'volume',
    'rsi_7', 'rsi_14',
    'sma_20', 'sma_50', 'sma_200',
    'ema_20', 'ema_50', 'ema_200',
    'atr_14', 'atr_pct',
    'macd_hist', 'macd',
    'bb_mid', 'bb_up', 'bb_dn',
    'ret_1d', 'ret_5d', 'ret_20d',
    'volume_ma20', 'volume_ratio',
    'price_sma20_pct', 'price_sma50_pct',
]

def generate_factors(round_num, n_factors, topic_hint='', prev_best=None, prev_weak=None):
    """调用 LLM 生成候选因?""
    nvidia_llm.model = 'qwen/qwen2.5-coder-32b-instruct'
    nvidia_llm.temperature = 0.8 if round_num == 1 else 0.9

    base_prompt = f"""你是量化因子研究员。基于以下可用特征设计{n_factors}个Alpha因子?
【可用特征】（NumPy数组?
{', '.join(FEATURE_LIST)}

【核心约束?
- 因子值越??未来5日收益越?- 必须可直接用 NumPy 运算（不?.rolling() ?DataFrame 方法?- 可以?sma_arr(x, n) ?ema_arr(x, n) 辅助计算移动平均
- 每个因子要有清晰的经济学逻辑

【因子类型要求】（每个类型至少1个）:
- 动量类：短期动量 vs 中期动量 的非对称?- 均值回归类：价格相对均线的偏离 + 波动率调?- 成交量类：量价背离或量价同向强度
- 结构类：日内波动幅度与趋势的交互

"""

    if prev_best:
        best_str = '\n'.join([f"- {b['name']}: IC={b['ic_avg']:+.4f}, RIC={b['ric_avg']:+.4f} ?{b.get('formula','')}"
                               for b in prev_best[:3]])
        base_prompt += f"\n【上一轮优秀因子】（请借鉴其结构思路，但不要复制?\n{best_str}\n"

    if prev_weak:
        weak_str = '\n'.join([f"- {w['name']}: IC={w['ic_avg']:+.4f} ?失败原因: 方向错误或太简?
                               for w in prev_weak[:2]])
        base_prompt += f"\n【避免重复?\n{weak_str}\n"

    if topic_hint:
        base_prompt += f"\n【本次重点? {topic_hint}\n"

    base_prompt += f"\n输出JSON数组（严格JSON格式，无markdown?\n[{{\"name\":\"英文名\",\"formula\":\"numpy表达式\",\"description\":\"中文说明\",\"category\":\"类型\"}}]"

    print(f'  [LLM] Generating {n_factors} factors (round {round_num})...')
    t0 = time.time()
    reply = nvidia_llm.chat(base_prompt, max_tokens=800, temperature=nvidia_llm.temperature)
    elapsed = time.time() - t0
    print(f'  [LLM] Done in {elapsed:.1f}s ({len(reply)} chars)')

    factors = parse_json_reply(reply)
    if not factors:
        print(f'  [LLM] PARSE FAILED, raw reply:\n{reply[:300]}')
        return []
    print(f'  [LLM] Parsed {len(factors)} factors')
    return factors

# ─── 主循?─────────────────────────────────────────────
def run_factory(n_rounds=3, factors_per_round=5, min_ic=0.03, min_ric=0.04):
    print('='*60)
    print('LLM Factor Factory v1.0')
    print(datetime.now().strftime('%Y-%m-%d %H:%M'))
    print('='*60)

    # 加载数据
    print('\n[Load] Fetching data via TickFlow...')
    datasets = load_data()
    if not datasets:
        print('FATAL: No data loaded'); return
    print(f'  Loaded {len(datasets)} datasets')

    # 读取历史因子?    lib_path = f'{BASE_DIR}\\output\\factor_library.json'
    if os.path.exists(lib_path):
        with open(lib_path, encoding='utf-8') as f:
            library = json.load(f)
        print(f'\n[Library] Loaded {len(library.get("factors",[]))} existing factors')
    else:
        library = {'factors': [], 'timestamp': None}

    all_new = []

    for round_num in range(1, n_rounds+1):
        print(f'\n{"="*60}')
        print(f'  ROUND {round_num}')
        print(f'{"="*60}')

        # 生成主题引导
        topics = [
            '成交?价格非对称性：放量上涨 vs 缩量下跌的信号差?,
            '多周期动量叠加：日、周、月三个周期的动量方向一致?,
            '波动率异常：ATR相对于历史均值的变化?,
        ]
        topic = topics[(round_num-1) % len(topics)]

        # 找上一轮最好的和最差的
        prev_best = [f for f in library['factors'] if f.get('ric_avg',0) > min_ric]
        prev_weak = [f for f in library['factors'] if 0 < abs(f.get('ric_avg',0)) < 0.02]

        # 生成
        raw_factors = generate_factors(round_num, factors_per_round, topic, prev_best, prev_weak)

        # 验证
        print(f'\n[Validate] {len(raw_factors)} candidates...')
        round_results = []
        for fac in raw_factors:
            formula = fac.get('formula','')
            if not formula: continue
            print(f'\n  [{fac["name"]}] ', end='', flush=True)
            result = validate_factor(formula, datasets, horizon=5)
            ic = result['ic_avg']; ric = result['ric_avg']
            cons = result['consistency']
            status = 'STRONG' if abs(ric) >= 0.08 and cons >= 0.75 else \
                     'GOOD' if abs(ric) >= 0.05 else \
                     'OK' if abs(ric) >= min_ric else 'WEAK'
            print(f'IC={ic:+.4f} RIC={ric:+.4f}一?{cons:.0%} [{status}]')

            entry = {
                'round': round_num, 'name': fac['name'], 'formula': formula,
                'description': fac.get('description',''),
                'category': fac.get('category',''),
                'ic_avg': ic, 'ric_avg': ric,
                'consistency': cons,
                'status': status,
                'markets': {k: (v.get('ric','ERR') if isinstance(v,dict) else 'ERR')
                             for k,v in result['markets'].items()},
                'timestamp': datetime.now().isoformat(),
            }
            round_results.append(entry)
            all_new.append(entry)

        # 本轮摘要
        strong = [r for r in round_results if r['status'] in ('STRONG','GOOD')]
        print(f'\n  Round {round_num} summary: {len(strong)}/{len(round_results)} passed (RIC>={min_ric})')

    # 更新因子?    library['factors'] = library.get('factors', []) + all_new
    library['timestamp'] = datetime.now().isoformat()

    # 排序
    library['factors'].sort(key=lambda x: abs(x.get('ric_avg',0)) + x.get('consistency',0), reverse=True)

    # 保存
    with open(lib_path, 'w', encoding='utf-8') as f:
        json.dump(library, f, indent=2, ensure_ascii=False)
    print(f'\n[Saved] {len(library["factors"])} factors -> {lib_path}')

    # 最终排?    print('\n' + '='*60)
    print('  FACTOR RANKING')
    print('='*60)
    print(f'{"Rank":<4} {"Name":<38} {"RIC":>8} {"IC":>8} {"一?:>6} {"Status":>8}')
    print('-'*78)
    for i, fac in enumerate(library['factors'][:20]):
        print(f'{i+1:<4} {fac["name"]:<38} {fac["ric_avg"]:>+8.4f} {fac["ic_avg"]:>+8.4f} {fac["consistency"]:>6.0%} {fac["status"]:>8}')

    # 导出?signals.py 可用的格?    usable = [f for f in library['factors'] if f['status'] in ('STRONG','GOOD')]
    if usable:
        export_path = f'{BASE_DIR}\\output\\usable_factors.json'
        with open(export_path, 'w', encoding='utf-8') as f:
            json.dump({'factors': usable, 'count': len(usable)}, f, indent=2, ensure_ascii=False)
        print(f'\n[Export] {len(usable)} usable factors -> usable_factors.json')
        print('Sample formulas:')
        for f in usable[:3]:
            print(f'  {f["name"]}: {f["formula"]}')

    return library

if __name__ == '__main__':
    run_factory(n_rounds=3, factors_per_round=5)
