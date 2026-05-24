# 纸交易环境 - Paper Trading System

## 目录结构
```
paper_trading/
├── signals/           # 每日信号记录 (JSON)
├── reports/           # 每周/月报告 (Markdown)
├── daily_runner.py    # 每日信号生成脚本
├── portfolio.json     # 当前持仓状态
└── README.md          # 本文件
```

## 使用方式
1. 每日收盘后运行: `python daily_runner.py`
2. 信号记录到 signals/YYYY-MM-DD.json
3. 持仓更新到 portfolio.json
4. 每周一生成周报到 reports/

## 信号来源
- 融合框架引擎 (fusion_engine.py)
- 真实VIX风控 (VXX.US)
- SPY + HK成分股

## 验证周期
- 纸交易运行3个月
- 对比策略信号 vs 实际走势
- 评估止损效果、信号质量
