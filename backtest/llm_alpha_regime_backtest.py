# -*- coding: utf-8 -*-
"""
LLM Alpha Regime Mapping 回测
目标：量化 LLM 因子在不同市场状态下的 Information Coefficient (IC)

核心问题：LLM 在什么市场环境下最有 Alpha？
回答：比较不同 regime 下 llm_factor_score vs 实际收益的 Rank IC

数据源：paper_trading/signals/*.json + Futu/TickFlow 实际价格
"""

import sys, io, os, json
from pathlib import Path
from datetime import datetime, timedelta
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

BASE = Path('E:/quant')
SIGNALS_DIR = BASE / 'paper_trading' / 'signals'
sys.path.insert(0, str(BASE))

# ─── 加载信号日报 ──────────────────────────────────────
def load_all_signals():
    """加载所有信号日报，返回 signal list（每条附带日期）"""
    all_signals = []
    for f in sorted(SIGNALS_DIR.glob('*.json')):
        try:
            with open(f, encoding='utf-8') as fh:
                data = json.load(fh)
            signals = data.get('signals', [])
            # 兼容早期格式（无 signals 字段，顶层即为单信号）
            if not signals and 'symbol' in data:
                signals = [data]
            for sig in signals:
                sig['_file'] = f.name
                sig['_date'] = data.get('date', f.stem)
            all_signals.extend(signals)
        except Exception as e:
            print(f'  [WARN] 跳过 {f.name}: {e}')
    return all_signals


# ─── 获取实际价格（用于计算前瞻收益）────────────────────
def get_price_series(symbol, days=60):
    """获取标的价格序列（Futu 优先 → TickFlow 回退）"""
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
            from datetime import datetime as dt
            for i, t in enumerate(data['timestamp']):
                d = dt.fromtimestamp(t/1000).strftime('%Y-%m-%d')
                result[d] = float(data['close'][i])
            return result
    except:
        pass

    return {}


# ─── 获取 VXX 价格序列（用于 regime 分类）────────────────
def get_vix_series():
    """加载 VXX 数据"""
    vxx_path = BASE / 'scanner' / 'cache' / 'VXX_US.json'
    if not vxx_path.exists():
        return {}
    with open(vxx_path) as f:
        data = json.load(f)
    result = {}
    for r in data:
        d = datetime.fromtimestamp(r['date']/1000).strftime('%Y-%m-%d')
        result[d] = float(r['close'])
    return result


# ─── 简化市场状态分类 ────────────────────────────────────
def classify_regime(vix_series, price_series, date):
    """
    简化版市场状态分类（回测用）
    基于 VXX 价格 + 价格趋势
    """
    # VIX regime
    dates = sorted(vix_series.keys())
    vals = [vix_series[d] for d in dates if d <= date]
    if len(vals) < 30:
        vix_regime = 'CANDIDATE'
    else:
        vxx = vals[-1]
        lookback = vals[-252:] if len(vals) >= 252 else vals
        pct_rank = sum(1 for v in lookback if v <= vxx) / len(lookback)
        if pct_rank >= 0.80:
            vix_regime = 'SUPPRESSED'
        elif pct_rank <= 0.20:
            vix_regime = 'RELEASED'
        else:
            vix_regime = 'CANDIDATE'

    # 趋势
    pdates = sorted(price_series.keys())
    pvals = [price_series[d] for d in pdates if d <= date]
    if len(pvals) < 50:
        trend = 'CRAB'
    else:
        price = pvals[-1]
        sma20 = np.mean(pvals[-20:])
        sma50 = np.mean(pvals[-50:])
        if price > sma20 and price > sma50:
            trend = 'BULL'
        elif price < sma20 and price < sma50:
            trend = 'BEAR'
        else:
            trend = 'CRAB'

    # 综合判断
    if vix_regime == 'SUPPRESSED':
        if trend == 'BEAR':
            return 'BEAR'
        return 'CORRECTION'
    elif vix_regime == 'RELEASED':
        if trend == 'BULL':
            return 'BULL'
        return 'RECOVERY'
    else:
        return trend


# ─── Rank IC 计算 ────────────────────────────────────────
def rank_ic(scores, returns):
    """计算 Rank IC（Spearman rank correlation）"""
    if len(scores) < 3:
        return np.nan
    from scipy.stats import spearmanr
    corr, pval = spearmanr(scores, returns)
    return corr


# ─── 主回测 ─────────────────────────────────────────────
def main():
    print('=' * 70)
    print('  LLM Alpha Regime Mapping 回测')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 70)

    # 1. 加载所有信号
    signals = load_all_signals()
    print(f'\n加载 {len(signals)} 条信号 ({len(set(s["_file"] for s in signals))} 个日报文件)')

    if not signals:
        print('  [ERROR] 无信号数据')
        return

    # 2. 提取所有唯一标的
    symbols = sorted(set(s.get('symbol', '') for s in signals if s.get('symbol')))
    print(f'覆盖标的: {len(symbols)} 个 — {", ".join(symbols[:10])}')

    # 3. 预加载价格数据
    print('\n加载价格数据...')
    price_cache = {}
    for sym in symbols:
        ps = get_price_series(sym, days=60)
        if ps:
            price_cache[sym] = ps
            print(f'  {sym}: {len(ps)} 天')
        else:
            print(f'  {sym}: 无数据')

    # 加载 VXX
    vix_series = get_vix_series()
    print(f'  VXX: {len(vix_series)} 天')

    # 4. 为每条信号计算前瞻收益 + 市场状态
    print('\n计算前瞻收益和市场状态...')
    enriched = []
    for sig in signals:
        sym = sig.get('symbol', '')
        date = sig.get('_date', sig.get('date', ''))
        if not sym or not date or sym not in price_cache:
            continue

        ps = price_cache[sym]
        sorted_dates = sorted(ps.keys())

        # 找到信号日的价格
        if date not in ps:
            # 找最近的交易日
            closest = [d for d in sorted_dates if d >= date]
            if not closest:
                continue
            signal_date = closest[0]
        else:
            signal_date = date

        # 找下一个交易日
        idx = sorted_dates.index(signal_date) if signal_date in sorted_dates else -1
        if idx < 0 or idx + 1 >= len(sorted_dates):
            continue

        entry_price = ps[signal_date]
        next_date = sorted_dates[idx + 1]
        next_price = ps[next_date]
        fwd_return = (next_price / entry_price - 1) * 100  # 百分比

        # 市场状态
        regime = classify_regime(vix_series, ps, signal_date)

        enriched.append({
            'symbol': sym,
            'date': signal_date,
            'next_date': next_date,
            'fwd_return': fwd_return,
            'regime': regime,
            'fusion_score': sig.get('fusion_score', 0),
            'fusion_level': sig.get('fusion_level', 'HOLD'),
            'llm_factor_score': sig.get('llm_factor_score', 0),
            'fm_score': sig.get('fm_score', 0),
            'fm_signal': sig.get('fm_signal', 'HOLD'),
            'xmm_signal': sig.get('xmm_signal', 'HOLD'),
            'rsi_daily': sig.get('rsi_daily', 50),
            'rsi_weekly': sig.get('rsi_weekly', 50),
            'confidence': sig.get('fusion_confidence', 0),
        })

    print(f'有效样本: {len(enriched)} 条')
    if len(enriched) < 5:
        print('  [WARN] 样本量过少，结果仅供参考')

    # 5. 按 regime 分组计算 IC
    print(f'\n{"="*70}')
    print('  LLM Alpha Regime Analysis')
    print(f'{"="*70}')

    regimes = sorted(set(e['regime'] for e in enriched))

    # 汇总表
    results = []

    for regime in regimes:
        subset = [e for e in enriched if e['regime'] == regime]
        n = len(subset)
        if n < 3:
            print(f'\n  [{regime}] 样本 {n} 条 — 不足，跳过')
            continue

        returns = [e['fwd_return'] for e in subset]
        fusion_scores = [e['fusion_score'] for e in subset]
        llm_scores = [e['llm_factor_score'] for e in subset]
        fm_scores = [e['fm_score'] for e in subset]
        confidences = [e['confidence'] for e in subset]

        # 各因子 IC
        ic_fusion = rank_ic(fusion_scores, returns)
        ic_llm = rank_ic(llm_scores, returns)
        ic_fm = rank_ic(fm_scores, returns)
        ic_conf = rank_ic(confidences, returns)

        # LLM 增量 = 融合IC - 因子IC
        llm_incremental = ic_fusion - ic_fm if not np.isnan(ic_fusion) and not np.isnan(ic_fm) else np.nan

        # 信号方向正确率（排除score==0的样本）
        valid_fusion = [e for e in subset if e['fusion_score'] != 0]
        if valid_fusion:
            correct = sum(1 for e in valid_fusion
                          if (e['fusion_score'] > 0 and e['fwd_return'] > 0) or
                             (e['fusion_score'] < 0 and e['fwd_return'] < 0))
            hit_rate = correct / len(valid_fusion)
        else:
            hit_rate = float('nan')

        # LLM 方向正确率（排除score==0的样本）
        valid_llm = [e for e in subset if e['llm_factor_score'] != 0]
        if valid_llm:
            llm_correct = sum(1 for e in valid_llm
                              if (e['llm_factor_score'] > 0 and e['fwd_return'] > 0) or
                                 (e['llm_factor_score'] < 0 and e['fwd_return'] < 0))
            llm_hit_rate = llm_correct / len(valid_llm)
        else:
            llm_hit_rate = float('nan')

        avg_return = np.mean(returns)

        result = {
            'regime': regime,
            'n': n,
            'avg_return': avg_return,
            'ic_fusion': ic_fusion,
            'ic_llm': ic_llm,
            'ic_fm': ic_fm,
            'ic_confidence': ic_conf,
            'llm_incremental': llm_incremental,
            'hit_rate_fusion': hit_rate,
            'hit_rate_llm': llm_hit_rate,
        }
        results.append(result)

        n_fusion_valid = len(valid_fusion)
        n_llm_valid = len(valid_llm)
        n_llm_zero = sum(1 for e in subset if e['llm_factor_score'] == 0)

        print(f'\n  [{regime}] 样本: {n} 条 | 均值收益: {avg_return:+.2f}%')
        print(f'  LLM因子: 非零 {n_llm_valid}/{n} ({n_llm_valid/n*100:.0f}%) | 零值 {n_llm_zero} ({n_llm_zero/n*100:.0f}%)')
        print(f'  ┌────────────────────┬──────────┬──────────────┬──────────┐')
        print(f'  │ 因子               │ Rank IC  │ 方向正确     │ 增量Alpha│')
        print(f'  ├────────────────────┼──────────┼──────────────┼──────────┤')
        hr_f = f'{hit_rate:.1%} ({n_fusion_valid}/{n})' if not np.isnan(hit_rate) else 'N/A'
        hr_l = f'{llm_hit_rate:.1%} ({n_llm_valid}/{n})' if not np.isnan(llm_hit_rate) else 'N/A'
        print(f'  │ FusionEngine(融合)  │ {ic_fusion:+.4f}  │ {hr_f:<12} │     —    │')
        print(f'  │ LLM因子(llm_alpha) │ {ic_llm:+.4f}  │ {hr_l:<12} │ {llm_incremental:+.4f}  │')
        print(f'  │ 传统因子(fm_score) │ {ic_fm:+.4f}  │     —        │     —    │')
        print(f'  │ 置信度(confidence) │ {ic_conf:+.4f}  │     —        │     —    │')
        print(f'  └────────────────────┴──────────┴──────────────┴──────────┘')

    # 6. 跨 Regime 对比
    if len(results) > 1:
        print(f'\n{"="*70}')
        print('  跨 Regime LLM Alpha 对比')
        print(f'{"="*70}')
        print(f'  {"Regime":<14} {"样本":>4} {"IC_llm":>8} {"IC_fm":>8} {"增量":>8} {"建议权重":>8}')
        print(f'  {"─"*14} {"─"*4} {"─"*8} {"─"*8} {"─"*8} {"─"*8}')

        # 根据 IC 结果给出建议权重
        for r in results:
            incr = r['llm_incremental']
            # 建议权重 = 基础15% + 增量IC * 系数
            suggested = max(0.10, min(0.50, 0.25 + incr * 100))
            r['suggested_weight'] = suggested
            print(f'  {r["regime"]:<14} {r["n"]:>4} {r["ic_llm"]:>+8.4f} {r["ic_fm"]:>+8.4f} {incr:>+8.4f} {suggested:>8.0%}')

        # 7. 对比当前 REGIME_LLM_WEIGHT
        print(f'\n  当前权重 vs 回测建议权重:')
        current_weights = {
            'BULL': 0.25, 'BEAR': 0.40, 'CRAB': 0.15,
            'RECOVERY': 0.35, 'CORRECTION': 0.45,
        }
        print(f'  {"Regime":<14} {"当前":>8} {"建议":>8} {"差异":>8}')
        print(f'  {"─"*14} {"─"*8} {"─"*8} {"─"*8}')
        for r in results:
            cur = current_weights.get(r['regime'], 0.30)
            sug = r['suggested_weight']
            diff = sug - cur
            print(f'  {r["regime"]:<14} {cur:>8.0%} {sug:>8.0%} {diff:>+8.0%}')

    # 8. 全样本分析
    print(f'\n{"="*70}')
    print('  全样本分析')
    print(f'{"="*70}')
    all_returns = [e['fwd_return'] for e in enriched]
    all_fusion = [e['fusion_score'] for e in enriched]
    all_llm = [e['llm_factor_score'] for e in enriched]
    all_fm = [e['fm_score'] for e in enriched]

    ic_total_fusion = rank_ic(all_fusion, all_returns)
    ic_total_llm = rank_ic(all_llm, all_returns)
    ic_total_fm = rank_ic(all_fm, all_returns)

    # LLM 因子零值诊断
    n_llm_total = len(all_llm)
    n_llm_zero = sum(1 for v in all_llm if v == 0)
    n_llm_nonzero = n_llm_total - n_llm_zero
    n_llm_none = sum(1 for e in enriched if e['llm_factor_score'] is None)

    # 按标的统计零值率
    sym_set = sorted(set(e['symbol'] for e in enriched))
    sym_zero_stats = {}
    for sym in sym_set:
        sym_llm = [e['llm_factor_score'] for e in enriched if e['symbol'] == sym]
        n_sym = len(sym_llm)
        n_sym_zero = sum(1 for v in sym_llm if v == 0)
        sym_zero_stats[sym] = {'total': n_sym, 'zero': n_sym_zero, 'zero_pct': n_sym_zero/n_sym*100 if n_sym else 0}

    print(f'\n  样本总量: {len(enriched)}')
    print(f'  融合 IC: {ic_total_fusion:+.4f}')
    print(f'  LLM  IC: {ic_total_llm:+.4f}')
    print(f'  因子 IC: {ic_total_fm:+.4f}')
    print(f'  LLM增量: {ic_total_llm - ic_total_fm:+.4f}')

    print(f'\n  --- LLM 因子零值诊断 ---')
    print(f'  总样本: {n_llm_total} | 非零: {n_llm_nonzero} ({n_llm_nonzero/n_llm_total*100:.1f}%) | 零值: {n_llm_zero} ({n_llm_zero/n_llm_total*100:.1f}%)')
    print(f'  {"标的":<15} {"总":>4} {"零值":>4} {"零值率":>8}')
    print(f'  {"─"*15} {"─"*4} {"─"*4} {"─"*8}')
    for sym in sym_set:
        st = sym_zero_stats[sym]
        print(f'  {sym:<15} {st["total"]:>4} {st["zero"]:>4} {st["zero_pct"]:>7.0f}%')

    print(f'\n  结论: {"⚠️  零值率过高，LLM因子IC和方向正确率不可靠" if n_llm_zero/n_llm_total > 0.5 else "✅  零值率可控"}')

    # 9. 保存报告
    report = {
        'generated_at': datetime.now().isoformat(),
        'total_samples': len(enriched),
        'regime_results': results,
        'overall': {
            'ic_fusion': float(ic_total_fusion) if not np.isnan(ic_total_fusion) else None,
            'ic_llm': float(ic_total_llm) if not np.isnan(ic_total_llm) else None,
            'ic_fm': float(ic_total_fm) if not np.isnan(ic_total_fm) else None,
        },
        'zero_inflation': {
            'total': n_llm_total,
            'zero': n_llm_zero,
            'nonzero': n_llm_nonzero,
            'zero_pct': round(n_llm_zero/n_llm_total*100, 1) if n_llm_total else 0,
            'per_symbol': sym_zero_stats,
        },
    }
    report_path = BASE / 'backtest' / 'llm_alpha_regime_report.json'
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f'\n报告已保存: {report_path}')

    print(f'\n{"="*70}')
    print('  回测完成')
    print(f'{"="*70}')


if __name__ == '__main__':
    main()
