# -*- coding: utf-8 -*-
"""
LLM Factor Factory v2.0 - CSI300 专用�?基于 CSI300 原始数据生成因子
"""
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

# ─── 数据加载（CSI300 CSV）──────────────────────────────
def load_csi300_data():
    """�?CSV 加载 CSI300 数据"""
    csv_path = r'E:\quant\ml_alpha\csi300_features_raw.csv'
    if not os.path.exists(csv_path):
        print(f'  [ERROR] CSV not found: {csv_path}')
        return None

    df = pd.read_csv(csv_path)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values(['symbol', 'trade_date']).reset_index(drop=True)

    # 按股票分�?    datasets = {}
    for sym in df['symbol'].unique():
        sym_df = df[df['symbol'] == sym].copy()
        if len(sym_df) < 60:  # 至少60天数�?            continue
        datasets[sym] = sym_df

    print(f'  Loaded {len(datasets)} stocks, {len(df)} rows')
    print(f'  Date range: {df["trade_date"].min().date()} ~ {df["trade_date"].max().date()}')
    return datasets

# ─── 指标库（基于现有特征）───────────────────────────────
def build_features_from_csv(df):
    """�?CSV 数据构建特征字典"""
    c = df['close'].values.astype(float)
    h = df['high'].values if 'high' in df.columns else c
    l = df['low'].values if 'low' in df.columns else c
    v = df['volume'].values if 'volume' in df.columns else np.ones(len(c))
    n = len(c)

    # �?CSV 中提取现有特�?    ret_1d = df['ret_1d'].values.astype(float)
    rsi_14 = df['rsi_14'].values.astype(float)
    macd = df['macd'].values.astype(float) if 'macd' in df.columns else np.zeros(n)
    macd_hist = df['macd_hist'].values.astype(float) if 'macd_hist' in df.columns else np.zeros(n)
    atr_ratio = df['atr_ratio'].values.astype(float) if 'atr_ratio' in df.columns else np.zeros(n)
    vol_ratio = df['vol_ratio'].values.astype(float) if 'vol_ratio' in df.columns else np.ones(n)
    mom_5d = df['mom_5d'].values.astype(float) if 'mom_5d' in df.columns else np.zeros(n)
    mom_10d = df['mom_10d'].values.astype(float) if 'mom_10d' in df.columns else np.zeros(n)

    # 分形特征
    ft5 = df['fractal_top_5'].values.astype(float) if 'fractal_top_5' in df.columns else np.zeros(n)
    fb5 = df['fractal_bottom_5'].values.astype(float) if 'fractal_bottom_5' in df.columns else np.zeros(n)
    ft10 = df['fractal_top_10'].values.astype(float) if 'fractal_top_10' in df.columns else np.zeros(n)
    fb10 = df['fractal_bottom_10'].values.astype(float) if 'fractal_bottom_10' in df.columns else np.zeros(n)

    # 均线比率
    ma5_ratio = df['ma5_ratio'].values.astype(float) if 'ma5_ratio' in df.columns else np.zeros(n)
    ma10_ratio = df['ma10_ratio'].values.astype(float) if 'ma10_ratio' in df.columns else np.zeros(n)
    ma20_ratio = df['ma20_ratio'].values.astype(float) if 'ma20_ratio' in df.columns else np.zeros(n)
    ma60_ratio = df['ma60_ratio'].values.astype(float) if 'ma60_ratio' in df.columns else np.zeros(n)

    # 分形平衡
    fb10_val = df['frac_balance_10'].values.astype(float) if 'frac_balance_10' in df.columns else np.full(n, 0.5)
    fb20_val = df['frac_balance_20'].values.astype(float) if 'frac_balance_20' in df.columns else np.full(n, 0.5)

    # TD9
    td9 = df['td9_count'].values.astype(float) if 'td9_count' in df.columns else np.zeros(n)

    # 背离
    bull_div = df['bull_divergence'].values.astype(float) if 'bull_divergence' in df.columns else np.zeros(n)

    # 未来收益（用于验证）
    fwd_ret_5d = df['fwd_ret_5d'].values.astype(float) if 'fwd_ret_5d' in df.columns else np.zeros(n)

    return {
        'close': c, 'high': h, 'low': l, 'volume': v,
        'ret_1d': ret_1d, 'rsi_14': rsi_14,
        'macd': macd, 'macd_hist': macd_hist,
        'atr_ratio': atr_ratio, 'vol_ratio': vol_ratio,
        'mom_5d': mom_5d, 'mom_10d': mom_10d,
        'fractal_top_5': ft5, 'fractal_bottom_5': fb5,
        'fractal_top_10': ft10, 'fractal_bottom_10': fb10,
        'ma5_ratio': ma5_ratio, 'ma10_ratio': ma10_ratio,
        'ma20_ratio': ma20_ratio, 'ma60_ratio': ma60_ratio,
        'frac_balance_10': fb10_val, 'frac_balance_20': fb20_val,
        'td9_count': td9, 'bull_divergence': bull_div,
        'fwd_ret_5d': fwd_ret_5d,
    }

# ─── 因子验证（CSI300 特定）──────────────────────────────
def parse_json_reply(text):
    """�?LLM 输出中提�?JSON"""
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
    """安全求�?+ IC 计算"""
    try:
        f = formula.strip()
        namespace = {"__builtins__":{}, "np":np}
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

def validate_factor_csi300(formula, datasets, horizon=5):
    """CSI300 多股�?IC 验证"""
    results = {}
    all_rics = []; all_ics = []

    for name, df in datasets.items():
        feats = build_features_from_csv(df)
        fwd_arr = feats['fwd_ret_5d']  # 使用 CSV 中的未来收益
        vr, err = safe_eval_formula(formula, feats, fwd_arr)
        results[name] = {'err': err} if err else vr
        if vr:
            all_rics.append(vr['ric']); all_ics.append(vr['ic'])

    avg_ic = round(float(np.mean(all_ics)),4) if all_ics else 0
    avg_ric = round(float(np.mean(all_rics)),4) if all_rics else 0
    # 跨股票一致�?    n_pos = sum(1 for r in all_rics if r > 0)
    consistency = n_pos / max(len(all_rics),1)  # 0~1，越高越一�?    return {
        'ic_avg': avg_ic, 'ric_avg': avg_ric,
        'stocks': results,
        'consistency': round(consistency, 2),
        'n_stocks': len(all_rics),
    }

# ─── LLM 因子生成（CSI300 特定）────────────────────────
FEATURE_LIST_CSI300 = [
    'close', 'high', 'low', 'volume',
    'ret_1d', 'rsi_14',
    'macd', 'macd_hist',
    'atr_ratio', 'vol_ratio',
    'mom_5d', 'mom_10d',
    'fractal_top_5', 'fractal_bottom_5',
    'fractal_top_10', 'fractal_bottom_10',
    'ma5_ratio', 'ma10_ratio', 'ma20_ratio', 'ma60_ratio',
    'frac_balance_10', 'frac_balance_20',
    'td9_count', 'bull_divergence',
]

def generate_factors_csi300(round_num, n_factors, topic_hint='', prev_best=None, prev_weak=None):
    """调用 LLM 生成 CSI300 候选因�?""
    nvidia_llm.model = 'qwen/qwen2.5-coder-32b-instruct'
    nvidia_llm.temperature = 0.8 if round_num == 1 else 0.9

    base_prompt = f"""你是量化因子研究员。基于以下可用特征设计{n_factors}个Alpha因子（针对A股CSI300）�?
【可用特征】（NumPy数组�?
{', '.join(FEATURE_LIST_CSI300)}

【核心约束�?
- 因子值越�?�?未来5日收益越�?- 必须可直接用 NumPy 运算（不�?.rolling() �?DataFrame 方法�?- 每个因子要有清晰的经济学逻辑

【因子类型要求】（每个类型至少1个）:
- 动量类：短期动量 vs 中期动量 的非对称�?- 均值回归类：价格相对均线的偏离 + 波动率调�?- 成交量类：量价背离或量价同向强度
- 结构类：分形结构（顶/底）与趋势的交互
- 背离类：MACD/价格背离信号

"""

    if prev_best:
        best_str = '\n'.join([f"- {b['name']}: IC={b['ic_avg']:+.4f}, RIC={b['ric_avg']:+.4f} �?{b.get('formula','')}"
                               for b in prev_best[:3]])
        base_prompt += f"\n【上一轮优秀因子】（请借鉴其结构思路，但不要复制�?\n{best_str}\n"

    if prev_weak:
        weak_str = '\n'.join([f"- {w['name']}: IC={w['ic_avg']:+.4f} �?失败原因: 方向错误或太简�?
                               for w in prev_weak[:2]])
        base_prompt += f"\n【避免重复�?\n{weak_str}\n"

    if topic_hint:
        base_prompt += f"\n【本次重点�? {topic_hint}\n"

    base_prompt += f"\n输出JSON数组（严格JSON格式，无markdown�?\n[{{\"name\":\"英文名\",\"formula\":\"numpy表达式\",\"description\":\"中文说明\",\"category\":\"类型\"}}]"

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

# ─── 主循�?─────────────────────────────────────────────
def run_csi300_factory(n_rounds=3, factors_per_round=5, min_ic=0.03, min_ric=0.04):
    print('='*60)
    print('LLM Factor Factory v2.0 - CSI300')
    print(datetime.now().strftime('%Y-%m-%d %H:%M'))
    print('='*60)

    # 加载数据
    print('\n[Load] Loading CSI300 data from CSV...')
    datasets = load_csi300_data()
    if not datasets:
        print('FATAL: No data loaded'); return
    print(f'  Loaded {len(datasets)} stocks')

    # 读取历史因子�?    lib_path = f'{BASE_DIR}\\output\\csi300_factor_library.json'
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
            '分形结构：顶/底分形与趋势的交�?,
            '多周期动量：5日�?0日�?0日动量的方向一致�?,
            '量价背离：成交量与价格的非对称�?,
        ]
        topic = topics[(round_num-1) % len(topics)]

        # 找上一轮最好的和最差的
        prev_best = [f for f in library['factors'] if f.get('ric_avg',0) > min_ric]
        prev_weak = [f for f in library['factors'] if 0 < abs(f.get('ric_avg',0)) < 0.02]

        # 生成
        raw_factors = generate_factors_csi300(round_num, factors_per_round, topic, prev_best, prev_weak)

        # 验证
        print(f'\n[Validate] {len(raw_factors)} candidates...')
        round_results = []
        for fac in raw_factors:
            formula = fac.get('formula','')
            if not formula: continue
            print(f'\n  [{fac["name"]}] ', end='', flush=True)
            result = validate_factor_csi300(formula, datasets, horizon=5)
            ic = result['ic_avg']; ric = result['ric_avg']
            cons = result['consistency']
            status = 'STRONG' if abs(ric) >= 0.08 and cons >= 0.75 else \
                     'GOOD' if abs(ric) >= 0.05 else \
                     'OK' if abs(ric) >= min_ric else 'WEAK'
            print(f'IC={ic:+.4f} RIC={ric:+.4f}一�?{cons:.0%} [{status}]')

            entry = {
                'round': round_num, 'name': fac['name'], 'formula': formula,
                'description': fac.get('description',''),
                'category': fac.get('category',''),
                'ic_avg': ic, 'ric_avg': ric,
                'consistency': cons,
                'status': status,
                'stocks': {k: (v.get('ric','ERR') if isinstance(v,dict) else 'ERR')
                             for k,v in result['stocks'].items()},
                'timestamp': datetime.now().isoformat(),
            }
            round_results.append(entry)
            all_new.append(entry)

        # 本轮摘要
        strong = [r for r in round_results if r['status'] in ('STRONG','GOOD')]
        print(f'\n  Round {round_num} summary: {len(strong)}/{len(round_results)} passed (RIC>={min_ric})')

    # 更新因子�?    library['factors'] = library.get('factors', []) + all_new
    library['timestamp'] = datetime.now().isoformat()

    # 排序
    library['factors'].sort(key=lambda x: abs(x.get('ric_avg',0)) + x.get('consistency',0), reverse=True)

    # 保存
    with open(lib_path, 'w', encoding='utf-8') as f:
        json.dump(library, f, indent=2, ensure_ascii=False)
    print(f'\n[Saved] {len(library["factors"])} factors -> {lib_path}')

    # 最终排�?    print('\n' + '='*60)
    print('  FACTOR RANKING')
    print('='*60)
    print(f'{"Rank":<4} {"Name":<38} {"RIC":>8} {"IC":>8} {"一�?:>6} {"Status":>8}')
    print('-'*78)
    for i, fac in enumerate(library['factors'][:20]):
        print(f'{i+1:<4} {fac["name"]:<38} {fac["ric_avg"]:>+8.4f} {fac["ic_avg"]:>+8.4f} {fac["consistency"]:>6.0%} {fac["status"]:>8}')

    # 导出到可用格�?    usable = [f for f in library['factors'] if f['status'] in ('STRONG','GOOD')]
    if usable:
        export_path = f'{BASE_DIR}\\output\\csi300_usable_factors.json'
        with open(export_path, 'w', encoding='utf-8') as f:
            json.dump({'factors': usable, 'count': len(usable)}, f, indent=2, ensure_ascii=False)
        print(f'\n[Export] {len(usable)} usable factors -> csi300_usable_factors.json')
        print('Sample formulas:')
        for f in usable[:3]:
            print(f'  {f["name"]}: {f["formula"]}')

    return library

if __name__ == '__main__':
    run_csi300_factory(n_rounds=3, factors_per_round=5)
