# -*- coding: utf-8 -*-
"""
因子衰减监控 (M4)
每周运行，追踪 LLM 因子和传统因子的 IC 趋势，检测衰减。

触发条件:
- IC 连续 2 周下降 > 30% → 告警（建议因子轮换）
- IC 转负（从正变负）→ 告警

数据源: paper_trading/signals/*.json（信号日报）
存储: backtest/factor_ic_history.json（历史 IC 快照）
"""

import sys, io, os, json, time
from pathlib import Path
from datetime import datetime, timedelta
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

BASE = Path('E:/quant')
SIGNALS_DIR = BASE / 'paper_trading' / 'signals'
HISTORY_FILE = BASE / 'backtest' / 'factor_ic_history.json'

sys.path.insert(0, str(BASE))


# ─── 加载信号数据 ─────────────────────────────────────
def load_signals_since(days=14):
    """加载最近 N 天的信号日报"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    all_signals = []
    for f in sorted(SIGNALS_DIR.glob('*.json')):
        try:
            with open(f, encoding='utf-8') as fh:
                data = json.load(fh)
            file_date = data.get('date', f.stem.split('_')[0])
            if file_date < cutoff:
                continue
            signals = data.get('signals', [])
            if not signals and 'symbol' in data:
                signals = [data]
            for sig in signals:
                sig['_date'] = file_date
                sig['_file'] = f.name
            all_signals.extend(signals)
        except Exception as e:
            print(f'  [WARN] 跳过 {f.name}: {e}')
    return all_signals


# ─── 获取价格序列 ─────────────────────────────────────
def get_price_series(symbol, days=60):
    """获取标的价格序列（Futu 优先）"""
    try:
        import futu as ft
        parts = symbol.split('.')
        if len(parts) == 2:
            futu_code = f'{parts[1]}.{parts[0]}'
            ctx = ft.OpenQuoteContext(host='127.0.0.1', port=11111)
            try:
                ret, data = ctx.get_cur_kline(futu_code, days, ft.KLType.K_DAY, ft.AuType.QFQ)
                if ret == ft.RET_OK and data is not None and len(data) >= 10:
                    result = {}
                    for _, row in data.iterrows():
                        d = str(row['time_key'])[:10]
                        result[d] = float(row['close'])
                    return result
            finally:
                ctx.close()
    except:
        pass

    # TickFlow 回退
    try:
        import tickflow_env
        from tickflow import TickFlow
        tf = TickFlow() if os.environ.get('TICKFLOW_API_KEY') else TickFlow.free()
        data = tf.klines.get(symbol, period='1d', count=days)
        if data and 'close' in data:
            result = {}
            for i, t in enumerate(data['timestamp']):
                d = datetime.fromtimestamp(t/1000).strftime('%Y-%m-%d')
                result[d] = float(data['close'][i])
            return result
    except:
        pass
    return {}


# ─── Rank IC ──────────────────────────────────────────
def rank_ic(scores, returns):
    """Spearman Rank IC"""
    if len(scores) < 3:
        return np.nan
    from scipy.stats import spearmanr
    corr, _ = spearmanr(scores, returns)
    return corr


# ─── 计算当前 IC ──────────────────────────────────────
def compute_current_ic(lookback_days=14):
    """计算最近 lookback_days 天的因子 IC"""
    signals = load_signals_since(days=lookback_days)
    if not signals:
        print('  [ERROR] 无信号数据')
        return None

    symbols = sorted(set(s.get('symbol', '') for s in signals if s.get('symbol')))
    print(f'  信号: {len(signals)} 条, 标的: {len(symbols)} 个')

    # 加载价格
    print('  加载价格数据...')
    price_cache = {}
    for sym in symbols:
        ps = get_price_series(sym, days=60)
        if ps:
            price_cache[sym] = ps

    # 为每条信号计算前瞻收益
    enriched = []
    for sig in signals:
        sym = sig.get('symbol', '')
        date = sig.get('_date', '')
        if not sym or not date or sym not in price_cache:
            continue
        ps = price_cache[sym]
        sorted_dates = sorted(ps.keys())
        if date not in ps:
            closest = [d for d in sorted_dates if d >= date]
            if not closest:
                continue
            signal_date = closest[0]
        else:
            signal_date = date

        idx = sorted_dates.index(signal_date) if signal_date in sorted_dates else -1
        if idx < 0 or idx + 1 >= len(sorted_dates):
            continue

        entry_price = ps[signal_date]
        next_price = ps[sorted_dates[idx + 1]]
        fwd_return = (next_price / entry_price - 1) * 100

        enriched.append({
            'symbol': sym,
            'date': signal_date,
            'fwd_return': fwd_return,
            'fusion_score': sig.get('fusion_score', 0),
            'llm_factor_score': sig.get('llm_factor_score', 0),
            'fm_score': sig.get('fm_score', 0),
        })

    if len(enriched) < 5:
        print(f'  [WARN] 有效样本仅 {len(enriched)} 条，IC 不可靠')

    returns = [e['fwd_return'] for e in enriched]
    llm_scores = [e['llm_factor_score'] for e in enriched]
    fm_scores = [e['fm_score'] for e in enriched]
    fusion_scores = [e['fusion_score'] for e in enriched]

    # 非零 LLM 样本
    nonzero_enriched = [e for e in enriched if e['llm_factor_score'] != 0]
    n_nonzero = len(nonzero_enriched)
    n_total = len(enriched)

    ic_llm = rank_ic(llm_scores, returns)
    ic_fm = rank_ic(fm_scores, returns)
    ic_fusion = rank_ic(fusion_scores, returns)

    # 非零样本 IC
    ic_llm_nonzero = np.nan
    if n_nonzero >= 3:
        ic_llm_nonzero = rank_ic(
            [e['llm_factor_score'] for e in nonzero_enriched],
            [e['fwd_return'] for e in nonzero_enriched]
        )

    result = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'lookback_days': lookback_days,
        'total_samples': n_total,
        'llm_nonzero_samples': n_nonzero,
        'zero_pct': round((n_total - n_nonzero) / n_total * 100, 1) if n_total else 0,
        'ic_llm': round(float(ic_llm), 4) if not np.isnan(ic_llm) else None,
        'ic_llm_nonzero': round(float(ic_llm_nonzero), 4) if not np.isnan(ic_llm_nonzero) else None,
        'ic_fm': round(float(ic_fm), 4) if not np.isnan(ic_fm) else None,
        'ic_fusion': round(float(ic_fusion), 4) if not np.isnan(ic_fusion) else None,
        'llm_incremental': round(float(ic_llm - ic_fm), 4) if not np.isnan(ic_llm) and not np.isnan(ic_fm) else None,
    }
    return result


# ─── 历史管理 ─────────────────────────────────────────
def load_history():
    """加载历史 IC 记录"""
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {'snapshots': []}


def save_history(history):
    """保存历史 IC 记录"""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def append_snapshot(history, snapshot):
    """追加快照（同一天不重复）"""
    # 去重：同一天只保留最新
    history['snapshots'] = [s for s in history['snapshots'] if s['date'] != snapshot['date']]
    history['snapshots'].append(snapshot)
    # 只保留最近 90 天
    cutoff = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    history['snapshots'] = [s for s in history['snapshots'] if s['date'] >= cutoff]
    history['snapshots'].sort(key=lambda x: x['date'])


# ─── 衰减检测 ─────────────────────────────────────────
def detect_decay(history):
    """检测因子衰减，返回告警列表"""
    alerts = []
    snapshots = history.get('snapshots', [])
    if len(snapshots) < 3:
        return alerts  # 数据不足，无法判断趋势

    recent = snapshots[-3:]  # 最近 3 个快照
    ic_llm_series = [s.get('ic_llm') for s in recent if s.get('ic_llm') is not None]
    ic_fm_series = [s.get('ic_fm') for s in recent if s.get('ic_fm') is not None]

    # LLM 因子衰减检测
    if len(ic_llm_series) >= 3:
        ic_now = ic_llm_series[-1]
        ic_prev = ic_llm_series[-2]
        ic_prev2 = ic_llm_series[-3]

        # 连续 2 周下降 > 30%
        if ic_prev2 > 0 and ic_prev > 0:
            drop_1 = (ic_prev2 - ic_prev) / abs(ic_prev2)
            drop_2 = (ic_prev - ic_now) / abs(ic_prev)
            if drop_1 > 0.30 and drop_2 > 0.30:
                alerts.append({
                    'level': 'CRITICAL',
                    'factor': 'LLM',
                    'type': 'consecutive_decay',
                    'message': f'LLM 因子 IC 连续 2 周下降 >30%: {ic_prev2:+.4f} → {ic_prev:+.4f} → {ic_now:+.4f}',
                    'action': '建议因子轮换: 运行 llm_factor_factory/run.py 重新挖掘因子',
                })

        # IC 转负
        if ic_prev2 > 0 and ic_now < 0:
            alerts.append({
                'level': 'WARNING',
                'factor': 'LLM',
                'type': 'sign_flip',
                'message': f'LLM 因子 IC 转负: {ic_prev2:+.4f} → {ic_now:+.4f}',
                'action': '检查因子有效性，考虑降低 LLM 权重',
            })

    # 传统因子衰减检测
    if len(ic_fm_series) >= 3:
        ic_now = ic_fm_series[-1]
        ic_prev = ic_fm_series[-2]
        ic_prev2 = ic_fm_series[-3]

        if ic_prev2 > 0 and ic_prev > 0:
            drop_1 = (ic_prev2 - ic_prev) / abs(ic_prev2)
            drop_2 = (ic_prev - ic_now) / abs(ic_prev)
            if drop_1 > 0.30 and drop_2 > 0.30:
                alerts.append({
                    'level': 'WARNING',
                    'factor': '传统因子',
                    'type': 'consecutive_decay',
                    'message': f'传统因子 IC 连续 2 周下降 >30%: {ic_prev2:+.4f} → {ic_prev:+.4f} → {ic_now:+.4f}',
                    'action': '检查因子权重配置',
                })

    return alerts


# ─── 主流程 ───────────────────────────────────────────
def main():
    print('=' * 65)
    print(f'  因子衰减监控 (M4)')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 65)

    # 1. 计算当前 IC
    print('\n计算当前因子 IC...')
    snapshot = compute_current_ic(lookback_days=14)
    if not snapshot:
        print('  [ERROR] 无法计算 IC，退出')
        return

    # 2. 显示当前 IC
    print(f'\n--- 当前 IC 快照 ({snapshot["date"]}) ---')
    print(f'  样本: {snapshot["total_samples"]} 条 | LLM非零: {snapshot["llm_nonzero_samples"]} ({100 - snapshot["zero_pct"]:.0f}%)')
    print(f'  LLM IC:     {snapshot["ic_llm"] or "N/A":>8}')
    print(f'  LLM IC (非零): {snapshot["ic_llm_nonzero"] or "N/A":>8}')
    print(f'  传统 IC:    {snapshot["ic_fm"] or "N/A":>8}')
    print(f'  融合 IC:    {snapshot["ic_fusion"] or "N/A":>8}')
    print(f'  LLM 增量:   {snapshot["llm_incremental"] or "N/A":>8}')

    # 3. 保存快照
    history = load_history()
    append_snapshot(history, snapshot)
    save_history(history)
    print(f'\n  快照已保存: {HISTORY_FILE}')

    # 4. 显示历史趋势
    snapshots = history['snapshots']
    if len(snapshots) >= 2:
        print(f'\n--- IC 趋势 ({len(snapshots)} 个快照) ---')
        print(f'  {"日期":<12} {"LLM IC":>8} {"传统IC":>8} {"增量":>8} {"非零IC":>8} {"样本":>4}')
        print(f'  {"─"*12} {"─"*8} {"─"*8} {"─"*8} {"─"*8} {"─"*4}')
        for s in snapshots[-8:]:  # 最近 8 个快照
            ic_llm = f'{s["ic_llm"]:+.4f}' if s.get("ic_llm") is not None else 'N/A'
            ic_fm = f'{s["ic_fm"]:+.4f}' if s.get("ic_fm") is not None else 'N/A'
            incr = f'{s["llm_incremental"]:+.4f}' if s.get("llm_incremental") is not None else 'N/A'
            ic_nz = f'{s["ic_llm_nonzero"]:+.4f}' if s.get("ic_llm_nonzero") is not None else 'N/A'
            print(f'  {s["date"]:<12} {ic_llm:>8} {ic_fm:>8} {incr:>8} {ic_nz:>8} {s["total_samples"]:>4}')

    # 5. 衰减检测
    alerts = detect_decay(history)
    if alerts:
        print(f'\n{"="*65}')
        print(f'  ⚠️  因子衰减告警 ({len(alerts)} 条)')
        print(f'{"="*65}')
        for a in alerts:
            icon = '🔴' if a['level'] == 'CRITICAL' else '🟡'
            print(f'\n  {icon} [{a["level"]}] {a["factor"]}')
            print(f'     {a["message"]}')
            print(f'     建议: {a["action"]}')
    else:
        print(f'\n  ✅ 无衰减告警（数据量: {len(snapshots)} 个快照）')

    print(f'\n{"="*65}')
    print(f'  监控完成')


if __name__ == '__main__':
    main()
