"""Historical diagnostics using the same accounting function as forward paper."""
from .portfolio import empty_state, equity, execute_batch
from .strategy import VARIANTS


def compare(bars_by_symbol, instruments, strategy, config):
    symbols = list(config.symbols)
    dates = list(bars_by_symbol[symbols[0]]['ts'])
    if len(dates) <= config.warmup:
        raise ValueError('NO_BARS_AFTER_WARMUP')
    if any(list(bars_by_symbol[s]['ts']) != dates for s in symbols):
        raise ValueError('REPLAY_CALENDAR_MISMATCH')
    frames = {s: strategy.frames(bars_by_symbol[s]) for s in symbols}
    results = []
    for variant in VARIANTS:
        state = empty_state(config)
        curve, trace = [], []
        peak = config.initial_cash
        max_drawdown = 0.0
        for i in range(config.warmup - 1, len(dates) - 1):
            signals = [strategy.signal(bars_by_symbol[s], s, variant, frames[s], i) for s in symbols]
            # Close-confirmed signal at t; next bar open only. No t-close fill.
            quotes = {s: {'bid': float(bars_by_symbol[s].iloc[i + 1]['open']),
                          'ask': float(bars_by_symbol[s].iloc[i + 1]['open']),
                          'ts': int(dates[i + 1])} for s in symbols}
            state, result = execute_batch(state, signals, quotes, instruments, config)
            close_marks = {s: float(bars_by_symbol[s].iloc[i + 1]['close']) for s in symbols}
            nav = equity(state, close_marks)
            peak = max(peak, nav)
            max_drawdown = min(max_drawdown, nav / peak - 1)
            curve.append({'ts': int(dates[i + 1]), 'equity': nav})
            trace.extend(result['orders'])
        results.append({'variant': variant, 'status': 'HISTORICAL_DIAGNOSTIC_NOT_OOS',
                        'total_return': curve[-1]['equity'] / config.initial_cash - 1 if curve else 0,
                        'max_drawdown': max_drawdown, 'fees_paid': state['fees_paid'],
                        'simulated_fills': sum(x['status'] == 'SIMULATED_FILL' for x in trace),
                        'min_size_rejections': sum(x['status'] == 'REJECTED_MIN_SIZE' for x in trace),
                        'equity_curve': curve, 'orders': trace})
    start = config.warmup
    bh_returns = [float(bars_by_symbol[s].iloc[-1]['close']) / float(bars_by_symbol[s].iloc[start]['open']) - 1 for s in symbols]
    return {'results': results, 'start_ts': int(dates[start]), 'end_ts': int(dates[-1]),
            'completed_bars': len(dates), 'evaluation_days': len(dates) - config.warmup,
            'benchmarks': {'cash_return': 0.0, 'equal_weight_buy_hold_gross': sum(bh_returns) / len(bh_returns)},
            'limitations': ['Diagnostic only; no untouched out-of-sample claim.',
                            'Current instrument size rules applied historically; not point-in-time rules.',
                            'Next-open proxy uses configured fees/slippage; no historical order book or capacity proof.',
                            'Buy-and-hold benchmark is gross and not exposure/risk matched.',
                            'LLM is not included without timestamped historical evidence.'],
            'llm_comparison': 'NOT_EVALUATED'}
