# QuantBot 系统架构地图

> 最后一次更新: 2026-05-21
> 架构收敛: daily_runner.py → unified_runner.py + crypto_runner.py

---

## 一、三层架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                   Layer 1: 研究层 (Research)                 │
│                                                             │
│  因子计算 ──→  ML Alpha ──→  LLM因子工厂 ──→  MarketState   │
│  factors/     models/        llm_factor_factory/  market_   │
│  technical/   xgboost/      factor_scorer.py    state/      │
│  6模块        36因子        7因子               classifier  │
├─────────────────────────────────────────────────────────────┤
│                   Layer 2: 决策层 (Decision)                 │
│                                                             │
│  FusionEngine (9×9 融合矩阵) ──→ HardGate (纯规则硬门槛)    │
│  fusion_framework/               fusion_framework/          │
│  fusion_engine.py                hard_gate.py               │
│                                                             │
│  输入: FM信号 + TA信号 + LLM因子 + 市场状态                  │
│  输出: OrderDirective (决策→执行的分界线)                    │
├─────────────────────────────────────────────────────────────┤
│                   Layer 3: 执行层 (Execution)                │
│                                                             │
│  ┌──────────统一执行接口──────────┐                          │
│  │                               │                          │
│  ├─ unified_runner.py (港股+美股) │                          │
│  │  core/signal_engine.py        │                          │
│  │  core/data_fetcher.py (Futu)  │                          │
│  │  core/stop_loss.py (三重止损) │                          │
│  │  core/position_manager.py     │                          │
│  │  core/order_executor.py (Futu)│                          │
│  │                               │                          │
│  ├─ crypto_runner.py (加密)      │                          │
│  │  crypto/okx_adapter.py        │                          │
│  │  OKX auto_trader.py (独立)    │                          │
│  │                               │                          │
│  └────────────日报: reports/reporter.py─────────────────────┘
└─────────────────────────────────────────────────────────────┘
```

---

## 二、数据流路径

### 2.1 港股 + 美股 (Futu 管线)

```
Futu OpenD (127.0.0.1:11111)
       │
       ▼
data_fetcher.py ──→ VIX 数据 (vix_map)
       │
       ▼
signal_engine.py ──→ 技术指标 (EMA/RSI/MACD)
       │                 LLM 因子 (factor_scorer.py)
       │                 XMM 信号 (xmm_strategy)
       ▼
FusionEngine.fuse() ──→ FM+TA 9×9 矩阵融合
       │                  MarketState 调节 LLM 权重
       │                  VIX 风控降仓
       ▼
hard_gate.py ──→ 纯规则检查: 仓位上限 / 冷却期 / 市场状态
       │
       ▼
unified_runner.py ──→ 信号排序 → 风控(止损/反转) → 建仓(分批)
       │                  ↓
       │              OrderExecutor.execute_orders()
       │                  ↓
       │              Futu 模拟盘下单 (SIMULATE)
       │                  ↓
       ▼
reports/reporter.py ──→ 日报 .md → reports/{date}.md
```

### 2.2 加密 (OKX 管线)

```
OKX API v5 (永续合约)
       │
       ▼
crypto/okx_adapter.py ──→ 包装 market_scanner.generate_signal()
       │                     每个标的 → EMA/RSI/MACD/ATR/资金费率
       ▼
FusionEngine 兼容信号 ──→ 标准 fusion_level/fusion_score 格式
       │
       ▼
crypto_runner.py ──→ 信号摘要 → 日报生成
【可选】
       ▼
OKX auto_trader.py ──→ 独立 daemon → 2分钟/轮回测
       │                    趋势/震荡/高波动 regime 感知
       ▼
              实际下单 (demo trading, FLAG=1)
```

---

## 三、模块依赖关系

### 3.1 正向依赖（合法）

```
reports/reporter.py
  └── config.py

unified_runner.py
  ├── core/data_fetcher.py
  ├── core/signal_engine.py
  ├── core/stop_loss.py (RiskManager)
  ├── core/position_manager.py (StagedEntryManager, SignalReversalChecker, SignalFilter)
  ├── core/order_executor.py (OrderExecutor)
  ├── core/utils.py
  ├── config.py
  ├── market_state/classifier.py
  ├── fusion_framework/hard_gate.py
  └── reports/reporter.py

crypto/crypto_runner.py
  ├── crypto/okx_adapter.py
  │     └── market_scanner.py (D:\new_quant\...\okx_engine\)
  └── reports/reporter.py

fusion_framework/fusion_engine.py
  ├── fusion_framework/signal_types.py
  ├── llm_factor_factory/factor_scorer.py
  └── market_state/classifier.py
```

### 3.2 禁止反向引用

```
❌ reports/reporter.py → paper_trading/daily_runner.py
❌ unified_runner.py    → paper_trading/daily_runner.py
❌ crypto/*             → paper_trading/daily_runner.py

✅ daily_runner.py → 已归档，不再被任何模块引用
```

### 3.3 核心配置文件路径

| 文件 | 路径 | 用途 |
|------|------|------|
| `config.py` | `E:\quant\config.py` | 全局配置: Futu 连接, 股票池, 风控参数, 分批建仓 |
| `.env` | `D:\new_quant\2026-05-11-task-51\okx_engine\.env` | OKX API 凭证 (API_KEY, SECRET, PASSPHRASE) |
| `quant.db` | `E:\quant\quant.db` | SQLite: 信号 / 交易 / 持仓历史 |
| `risk_state.json` | `E:\quant\hk_trader\state\risk_state.json` | 港股止损追踪状态 |
| `risk_state.json` | `E:\quant\us_trader\state\risk_state.json` | 美股止损追踪状态 |
| `trade_state.json` | `D:\new_quant\2026-05-11-task-51\okx_engine\trade_state.json` | OKX 交易状态 |

---

## 四、Automation 调度

| 市场 | 调度时间 | 命令 | 运行模式 |
|------|---------|------|---------|
| 港股 | 交易日 09:35 | `unified_runner.py --market HK --live` | 实单 (Futu 模拟盘) |
| 美股 | 交易日 21:35 | `unified_runner.py --market US --live` | 实单 (Futu 模拟盘) |
| 加密 | 手动触发 | `crypto/crypto_runner.py` | 信号+日报 |
| 加密 daemon | 手动启动 | `start_daemon.bat` | 2分钟/轮回测 |

---

## 五、关键设计决策

### 5.1 LLM 定位: 见证者, 非决策者

```
研究层:   LLM 自由探索 (factor_scorer, nvidia consensus)
决策层:   LLM 权重 10-50% (regime-adaptive)
执行层:   零 LLM (纯规则引擎)
```

### 5.2 9×9 融合矩阵

| FM \\ TA | TA BUY | TA HOLD | TA SELL |
|----------|--------|---------|---------|
| **FM BUY** | STRONG_BUY | BUY | REDUCED |
| **FM HOLD** | BUY | HOLD | SELL |
| **FM SELL** | REDUCED | SELL | STRONG_SELL |

### 5.3 regime-adaptive LLM weight

| 市场状态 | LLM 权重 |
|---------|---------|
| 黑天鹅/政策市 (BEAR) | 45-50% |
| 高波动事件 (CORRECTION) | 35-40% |
| 趋势市场 (BULL/RECOVERY) | 25-30% |
| 横盘震荡 (CRAB) | 10-15% |

---

## 六、已归档模块

| 原模块 | 状态 | 替代 |
|--------|------|------|
| `paper_trading/daily_runner.py` | 🗄 已归档 | `unified_runner.py` + `reports/reporter.py` |
| `E:\quant\paper_trading\signals.json` | 🗄 已归档 | `core/quant_db.py` (SQLite) |
| `paper_trading/futu_bridge.py` | 🗄 已归档 | `core/order_executor.py` |

---

*本架构地图与三层架构哲学一致: Research → Decision → Execution, LLM 止步于执行层之前。*