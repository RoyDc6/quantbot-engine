# -*- coding: utf-8 -*-
"""插入趋势突破模式到 xmm_sonnet_model.py"""

with open(r'E:\quant\xmm-strategy\xmm_sonnet_model.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 找到插入点：layer1 行之后，第二层注释之前
old_text = """        layer1 = f'月线{m_ts}→通过'

        # ══ 第二层：结构过滤 ══════════════════════════
        # 先检查底部加仓机会（优先级高于EXIT）
        # 当月线/周线仍多，日线超卖(低N≥6) → 底部买入机会"""

new_text = """        layer1 = f'月线{m_ts}→通过'

        # ════ 新增：趋势突破模式 ════════════════════════════
        # 条件：月线强多 + EMA 突破信号 + 无顶部结构
        # 这是"趋势跟随"入场，区别于"底部布局"入场
        has_top_struct = bool(d_ms['顶部结构'])
        has_top_passive = bool(d_ms['顶部钝化'])
        
        if m_ts == 'strong_bull' and not has_top_struct and not has_top_passive:
            # 月/周长期通道突破 → 趋势跟随入场
            m_long_bull = bool(self.m_trend['cross_long_up'].iloc[mi])
            w_long_bull = bool(self.w_trend['cross_long_up'].iloc[wi])
            
            if m_long_bull or w_long_bull:
                risk = vxx_st
                m_state = _classify_seq(m_hn, m_ln)
                w_state = _classify_seq(w_hn, w_ln)
                base = POSITION_MATRIX.get((m_state, w_state), 0.10)
                # 趋势跟随模式：仓位上限压缩至50%（不满仓追）
                final_limit = round(min(base, 0.30) * vxx_coeff, 3)
                if d_hn < 6:  # 日线未过热
                    return self._sig('BUY', final_limit, vxx_coeff, vxx_st,
                                     layer1, 
                                     f'【趋势突破】月线强多+长期通道突破 | {m_state}/{w_state}',
                                     f'VXX:{risk} | 仓位:{final_limit:.0%}',
                                     m_ts, w_ts, d_ts, m_ln, m_hn, w_ln, w_hn, d_ln, d_hn, d_ms, i)
            
            # 日线短期通道突破 + 周线方向一致 → 轻仓跟随
            d_short_bull = bool(self.d_trend['cross_short_up'].iloc[i])
            if d_short_bull and w_ts in ('strong_bull', 'bull'):
                final_limit = round(min(0.15, POSITION_MATRIX.get((m_state, w_state), 0.10)) * vxx_coeff, 3)
                if d_hn < 6:
                    return self._sig('BUY', final_limit, vxx_coeff, vxx_st,
                                     layer1,
                                     f'【趋势跟随】日线短多+周月方向一致',
                                     f'VXX:{risk} | 仓位:{final_limit:.0%}（轻仓）',
                                     m_ts, w_ts, d_ts, m_ln, m_hn, w_ln, w_hn, d_ln, d_hn, d_ms, i)
        # ════ 趋势突破模式结束 ══════════════════════════════

        # ══ 第二层：结构过滤 ════════════════════════════
        # 先检查底部加仓机会（优先级高于EXIT）
        # 当月线/周线仍多，日线超卖(低N≥6) → 底部买入机会"""

if old_text in content:
    content = content.replace(old_text, new_text)
    with open(r'E:\quant\xmm-strategy\xmm_sonnet_model.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('[OK] Trend breakout mode inserted')
else:
    print('[FAIL] Target text not found, locating...')
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'layer1' in line and '月线' in line:
            print(f'找到layer1行: {i}: {repr(line[:60])}')
            # 打印上下文
            for j in range(max(0, i-2), min(len(lines), i+5)):
                print(f'{j}: {repr(lines[j][:80])}')
