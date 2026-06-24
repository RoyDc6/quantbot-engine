# 纸交易环境 - Paper Trading System

## 目录结构
```
paper_trading/
├── signals/           # 每日信号记录 (JSON，唯一结构化输出)
├── reports/           # 每周/月报告 (Markdown，JSON 的可读渲染)
├── daily_runner.py    # 每日信号生成脚本
├── portfolio.json     # 当前持仓状态
└── README.md          # 本文件
```

## 使用方式
1. 每日收盘后运行: `python daily_runner.py`
2. 信号记录到 signals/YYYY-MM-DD.json
3. 持仓更新到 portfolio.json
4. 每周一生成周报到 reports/

## 运行事实源

- Futu OpenD 是 HK/US 日常链路的唯一输入源。
- `paper_trading/signals/` 下的 JSON 是唯一结构化输出。
- 日报是同批 JSON 的可读渲染，便于人工检查。
- `quant.db` 仅作历史归档/研究查询，不参与当前交易状态判断。

## 信号来源
- 融合框架引擎 (fusion_engine.py)
- 真实VIX风控 (VXX.US)
- SPY + HK成分股

## 验证周期
- 纸交易运行3个月
- 对比策略信号 vs 实际走势
- 评估止损效果、信号质量
