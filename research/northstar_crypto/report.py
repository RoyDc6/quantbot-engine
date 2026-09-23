from datetime import datetime, timezone


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def render(artifact):
    if artifact['mode'] == 'OKX_DEMO_FORWARD':
        return render_demo(artifact)
    funding = artifact.get('funding', {})
    account = funding.get('account')
    capital = artifact['config']['initial_cash']
    capital_text = f'{capital:.8f} USDT' if capital is not None else '不可用；禁止回退到固定本金'
    lines = ['# Crypto 日报 · Northstar 独立候选', '',
             f"运行：{artifact['run_id']}；状态：{artifact['status']}；模式：{artifact['mode']}",
             f"数据源：OKX 公开现货行情；周期：1Dutc；采集：{artifact['snapshot_captured_at']}",
             '', '**资金来源为 OKX 模拟账户全部权益；成交仍使用本地研究账本，未向 OKX 发送订单。策略有效性尚未评估。**' if account else
             '**本轮账户资金未接入或属于历史回放；未向 OKX 发送订单。**', '',
             '## 数据与信号', '', '| 标的 | 数据状态 | 已完成日线数 | 信号截止 UTC | 动作 | 原始比例 | 原因 |',
             '|---|---|---:|---|---|---:|---|']
    for row in artifact['signals']:
        valid = row['data_status'] == 'VALID'
        lines.append('| ' + ' | '.join([
            row['symbol'], row['data_status'], str(row.get('bars', '—')),
            iso(row['signal_close_ms']) if valid else '—', row.get('action') or '—',
            f"{row['raw_fraction']:.0%}" if valid else '—',
            row.get('reason', row.get('error', '')).replace('|', '/').replace('\n', ' ')]) + ' |')
    counts = artifact['counts']
    lines += ['', '计数：' + '；'.join(f'{key}={value}' for key, value in counts.items()),
              '数据失败不计入正常 HOLD；原始比例不是最终仓位，置信分不是盈利概率。', '',
              '## 本地模拟账本', '', f"处理状态：{artifact['paper']['status']}。实际交易所订单：0。",
              f"资金基数：{capital_text}；单标的上限 {artifact['config']['single_cap']:.0%}；总暴露上限 {artifact['config']['gross_cap']:.0%}。",
              f"研究成本假设：手续费 {artifact['config']['fee_bps']} bps，额外滑点 {artifact['config']['slippage_bps']} bps；不是账户费率。"]
    if account:
        lines += ['', f"OKX 模拟账户当前全部权益：{account['total_equity_usdt']:.8f} USDT 等值；可用 USDT：{account['available_usdt']:.8f}。",
                  f"交易所原始总权益估值：{account['total_equity_usd']:.8f} USD；USDT/USD 换算率：{account['usdt_usd_rate']:.8f}。",
                  f"资金范围：全部模拟账户权益的 100%；读取时点：{account['captured_at']}。",
                  f"本地账本起始资金：{funding['initial_capital_usdt']:.8f} USDT，来自建账时账户全部权益。",
                  '资金范围 100% 不代表满仓；仓位仍服从策略的单标的与总暴露上限。',
                  '账户币种尾数计入权益估值，未在交易所兑换为 USDT。本地记账从等值现金开始，后续保留独立损益，不按每日账户余额重置。',
                  '', '| 模拟账户资产 | 币种权益 | 可用数量 | 冻结数量 | USD 估值 |', '|---|---:|---:|---:|---:|']
        for row in account['currencies']:
            lines.append(f"| {row['currency']} | {row['equity']:.12f} | {row['available']:.12f} | {row['frozen']:.12f} | {row['equity_usd']:.10f} |")
    elif funding.get('error'):
        lines += [f"账户资金状态：{funding['status']}；原因：{funding['error']}。"]
    if 'error' in artifact['paper']:
        lines += [f"处理说明：{artifact['paper']['error']}"]
    if artifact['paper'].get('risk_resolved') is False:
        lines += ['风险状态：最小数量等约束使实际持仓仍有超限；本轮标为 PARTIAL，风险未解决时拒绝进一步增加持仓。']
    if artifact.get('paper_state'):
        state = artifact['paper_state']
        lines += [f"模拟现金：{state['cash']:.6f} USDT；累计手续费：{state['fees_paid']:.6f} USDT；已实现模拟损益：{state['realized_pnl']:.6f} USDT。"]
    lines += ['', '| 标的 | 当前数量 | 受限目标权重 | 目标数量 | 数量差额 | 模拟结果 |', '|---|---:|---:|---:|---:|---|']
    for row in artifact['paper'].get('orders', []):
        lines.append(f"| {row['symbol']} | {row['current_qty']:.8f} | {row['target_weight']:.2%} | {row['target_qty']:.8f} | {row['delta_qty']:.8f} | {row['status']} |")
    if not artifact['paper'].get('orders'):
        lines += ['| — | — | — | — | — | 本轮无新增模拟处理 |']
    lines += ['', '## 候选比较', '', '**以下为历史诊断，不是样本外或前向收益。**']
    comparison = artifact.get('comparison', {})
    if comparison.get('results'):
        lines += ['', '| 版本 | 成本后收益 | 最大回撤 | 模拟成交次数 | 最小数量拒绝次数 |', '|---|---:|---:|---:|---:|']
        for row in comparison['results']:
            lines.append(f"| {row['variant']} | {row['total_return']:.2%} | {row['max_drawdown']:.2%} | {row['simulated_fills']} | {row['min_size_rejections']} |")
        lines += ['', '范围：' + str(comparison['evaluation_days']) + ' 个交易日；日线收盘确认信号，下一根开盘价加成本作为模拟成交。',
                  '使用当前最小下单数量规则，未还原历史规则；未检验真实流动性或成交能力。',
                  '完整曲线、逐笔模拟明细和基准限定见 artifact.json。']
    else:
        lines += ['未完成：' + comparison.get('error', '本次未请求历史比较。')]
    llm = artifact['llm']
    lines += ['', '## LLM 辅助观察', '', f"状态：{llm['status']}；对仓位影响：0。"]
    for row in llm.get('accepted', []):
        summary = row['summary'].replace('\n', ' ').replace('|', '/')[:1000]
        lines += [f"- {row['symbol']}：{summary}（来源：{row['source_url']}；模型：{row['model']}）"]
    if not llm.get('accepted'):
        lines += ['没有符合时间与来源要求的辅助记录；不能解释为中性判断。']
    lines += ['', '## 验证与下一步', '',
              'VP 未进入本候选；HK/US 继续使用原版本和原调度。',
              '当前阶段只验证输入、信号、模拟记账和报告一致性。样本外、参数稳定性及足够前向交易仍待完成。',
              f"输入指纹：{artifact['snapshot_hash']}；策略快照：{artifact['vendor_hash']}。", '']
    return '\n'.join(lines)


def render_demo(a):
    e, f = a['execution'], a['funding']
    account = f.get('account')
    lines = ['# Crypto 日报 · Northstar OKX 模拟盘', '',
             f"运行：{a['run_id']}；状态：{a['status']}；模式：OKX_DEMO_FORWARD [mode: demo]", '',
             '资金、持仓、订单和成交以 OKX 模拟账户为准。策略有效性：NOT_EVALUATED；实盘执行关闭。', '',
             '## 信号', '', '| 标的 | 数据状态 | 截止 UTC | 动作 | 原始比例 | 原因 |', '|---|---|---|---|---:|---|']
    for r in a['signals']:
        valid = r['data_status'] == 'VALID'
        fraction = f"{r['raw_fraction']:.0%}" if valid else '—'
        reason = r.get('reason',r.get('error','')).replace('|','/').replace('\n',' ')
        lines.append(f"| {r['symbol']} | {r['data_status']} | {iso(r['signal_close_ms']) if valid else '—'} | {r.get('action') or '—'} | {fraction} | {reason} |")
    lines += ['', '计数：' + '；'.join(f'{k}={v}' for k,v in a['counts'].items()), '', '## 模拟账户资金', '']
    if account:
        lines += [f"当前全部权益：{account['total_equity_usdt']:.8f} USDT 等值；可用 USDT：{account['available_usdt']:.8f}。",
                  f"原始权益：{account['total_equity_usd']:.8f} USD；USDT/USD：{account['usdt_usd_rate']:.8f}；读取：{account['captured_at']}。",
                  f"资金范围：全部账户权益的 100%；单标的上限 {a['config']['single_cap']:.0%}；总暴露上限 {a['config']['gross_cap']:.0%}。",
                  '全部资金作为动态仓位基数；不创建虚拟本金，不把其他币种自动兑换为 USDT。', '',
                  '| 资产 | 实际数量 | 可用数量 | 冻结数量 |', '|---|---:|---:|---:|']
        for r in account['currencies']:
            lines.append(f"| {r['currency']} | {r['cash_balance']:.12f} | {r['available']:.12f} | {r['frozen']:.12f} |")
    else:
        lines += ['账户读取失败：' + f.get('error','不可用')]
    lines += ['', '## OKX 模拟盘订单与对账', '',
              f"执行状态：{e['status']}；本轮新接受模拟订单：{e.get('new_orders',0)}；实盘订单：0。",
              f"重复抑制：{e.get('duplicate_suppressed',False)}；余额对账：{e.get('reconciliation',{}).get('status','未完成')}；风险达标：{e.get('risk_resolved','未完成')}。",
              '信号动作与执行侧风险调整分开记录：HOLD 表示没有新的 alpha 动作；若现有暴露超过单标的/总暴露上限或需预留执行成本，仍可能产生减仓单，该订单属于 RISK_CAP_REBALANCE，不是 SELL 信号。',
              '下表为持久化批次回报；重复运行展示历史回报，不代表本轮再次下单。订单接受不等于成交；撤销的 IOC 也可能部分成交。', '',
              '| 标的 | 方向 | 委托数量 | 累计成交 | 均价 | 手续费（原币种，有符号） | 状态 | OKX ordId |', '|---|---|---:|---:|---:|---|---|---|']
    for o in e.get('orders',[]):
        b,r = o['body'],o.get('receipt',{})
        lines.append(f"| {b['instId']} | {b['side']} | {b['sz']} | {r.get('accFillSz','—')} | {r.get('avgPx','—')} | {r.get('fee','—')} {r.get('feeCcy','')} | {o['status']} | {r.get('ordId',o.get('acknowledgment',{}).get('ordId','—'))} |")
    if not e.get('orders'):
        lines += ['| — | — | — | — | — | — | 本批次无订单 | — |']
    if e.get('error'):
        lines += ['', '执行说明：' + e['error']]
    if e.get('plans'):
        lines += ['', '| 标的 | 信号动作 | 当前数量 | 目标权重 | 目标数量 | 计划状态 | 调整性质 |', '|---|---|---:|---:|---:|---|---|']
        for p in e['plans']:
            adjustment = '—'
            if p.get('action') == 'HOLD' and abs(float(p.get('delta_qty') or 0)) > 1e-12:
                adjustment = 'RISK_CAP_REBALANCE（非 SELL 信号）'
            lines.append(f"| {p['symbol']} | {p.get('action','—')} | {p['current_qty']:.10f} | {p['target_weight']:.2%} | {p['target_qty']:.10f} | {p['status']} | {adjustment} |")
    lines += ['', '## 历史诊断', '', '以下为本地历史回放，费用使用研究假设；不是 OKX 模拟盘成交收益，也不是样本外结论。']
    comparison = a.get('comparison',{})
    if comparison.get('results'):
        lines += ['', '| 版本 | 成本后收益 | 最大回撤 | 本地模拟成交数 |', '|---|---:|---:|---:|']
        for r in comparison['results']:
            lines.append(f"| {r['variant']} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['simulated_fills']} |")
        benchmarks = comparison.get('benchmarks') or {}
        if benchmarks:
            lines += ['', '| 基准 | 收益 | 口径 |', '|---|---:|---|']
            if 'cash_return' in benchmarks:
                lines.append(f"| 现金 | {benchmarks['cash_return']:.2%} | 未投资基准 |")
            if 'equal_weight_buy_hold_gross' in benchmarks:
                lines.append(f"| 五标的等权买入持有 | {benchmarks['equal_weight_buy_hold_gross']:.2%} | 毛收益，未按策略暴露/风险匹配 |")
                full = next((r for r in comparison['results'] if r.get('variant') == 'northstar_full'), comparison['results'][-1])
                gap = full['total_return'] - benchmarks['equal_weight_buy_hold_gross']
                lines.append(f"\nNorthstar full 相对等权买入持有差额：{gap:+.2%}；该差额仅作诊断，不能解释为风险调整后超额收益。")
        returns = [round(float(r['total_return']), 12) for r in comparison['results']]
        if len(set(returns)) == 1 and len(returns) > 1:
            lines.append('本窗口内三个变体结果完全相同；当前样本没有显示 Structure/Sequence 改变最终交易结果。')
        lines += [f"评估 {comparison['evaluation_days']} 日；确认日线后使用下一根开盘加成本；详细曲线见 artifact.json。"]
        for limitation in comparison.get('limitations') or []:
            lines.append(f"- 限制：{limitation}")
    else:
        lines += [comparison.get('error','本轮未请求历史比较。')]
    lines += ['', '## LLM 辅助与验证', '', f"LLM 状态：{a['llm']['status']}；仓位影响：0。"]
    for r in a['llm'].get('accepted',[]):
        lines += [f"- {r['symbol']}：{r['summary']}（来源：{r['source_url']}；模型：{r['model']}）"]
    lines += ['VP 未进入本候选；HK/US 使用各自独立线路。',
              '成交能力验证与策略盈利能力验证分开记录。样本外、参数稳定性和足够前向记录仍待完成。', '',
              '| 检查 | 结果 |', '|---|---|']
    lines += [f"| {k} | {'PASS' if v else 'FAIL'} |" for k,v in a['quality_checks'].items()]
    lines += ['', f"输入指纹：{a['snapshot_hash']}；策略快照：{a['vendor_hash']}。", '']
    return '\n'.join(lines)
