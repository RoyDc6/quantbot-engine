# -*- coding: utf-8 -*-
import json
with open('E:/quant/output/xmm_backtest_result.json','r',encoding='utf-8') as f:
    r = json.load(f)
print('=== 回测结果 ===')
print(f"标的: {r['symbol']}")
print(f"周期: {r['period']} ({r['days']}天)")
print(f"最终权益: ${r['final_equity']:,.2f}")
print(f"策略收益: {r['total_return']:+.2f}%")
print(f"基准收益: {r['benchmark_return']:+.2f}%")
print(f"超额alpha: {r['alpha']:+.2f}%")
print(f"夏普比率: {r['sharpe']}")
print(f"最大回撤: {r['max_drawdown']:.2f}%")
print(f"总交易: {r['total_trades']}笔")
print(f"胜率: {r['win_rate']:.1f}%")
print(f"均收益: {r['avg_return']:+.2f}%")
print(f"均胜: {r['avg_win']:+.2f}%")
print(f"均亏: {r['avg_loss']:+.2f}%")
buys = [t for t in r['trades'] if t['action']=='BUY']
sells = [t for t in r['trades'] if t['action']=='SELL']
print(f"买入: {len(buys)}次  卖出: {len(sells)}次")
print(f"模型: {r['model']}")
