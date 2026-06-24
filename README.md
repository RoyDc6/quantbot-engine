# QuantEngine - 量化交易引擎

> 数据至上，风控第一。从市场噪音中提取 alpha。

## 项目概述

QuantEngine 是一套完整的港股+美股量化交易系统，集成了传统技术分析、缠论分型、LLM 因子挖掘和多因子融合引擎。当前运行在 Futu 模拟账户（$1M+）上进行纸交易验证。

### 核心能力

| 维度 | 能力 |
|------|------|
| 信号系统 | 三周期（月EMA定方向 / 周RSI定仓位 / 日MACD触发）+ VIX全局风控 |
| ML Alpha | XGBoost 36因子（港股/美股/A股），170+MB 特征数据 |
| 融合引擎 | FusionEngine（30% LLM + 70% 传统因子加权融合） |
| 缠论分析 | 分型识别 / 笔划分 / 中枢 / 买卖点（港股Alpha最强 IC=0.34） |
| 市场状态 | 多维度分类（VIX环境 / 趋势 / 动量 / 波动率 → BULL/BEAR/CRAB） |
| LLM因子 | 因子工厂（RIC翻转修正），fractal_structure 最强因子 RIC=-0.45 |
| 数据源 | Futu OpenD（HK/US 日常运行链路唯一输入） |

## 目录结构

```
quant/
├── paper_trading/              # 纸交易系统
│   ├── daily_runner.py         # 每日信号生成主脚本（港股+美股）
│   ├── auto_trade.py           # 自动交易流程 v3.0（统一入口）
│   ├── portfolio.json          # 当前持仓状态
│   ├── signals/                # 每日信号记录 (JSON，唯一结构化输出)
│   └── reports/                # 周/月报告 (Markdown，JSON 的可读渲染)
├── fusion_framework/           # 融合引擎框架
│   ├── fusion_engine.py        # 双系统融合引擎
│   ├── signal_types.py         # 信号类型定义
│   └── market_state/           # 市场状态分类器
├── llm_factor_factory/         # LLM 因子挖掘与打分
│   └── factor_scorer.py        # 因子评分器
├── chan/                       # 缠论分析模块
│   └── fractal.py              # 分型识别
├── scanner/                    # 市场扫描器
│   └── cache/                  # 行情缓存（VXX等）
├── xmm_strategy/               # 徐小明策略模块
├── requirements.txt            # Python 依赖
└── README.md                   # 本文件
```

## 安装步骤

### 环境要求

- **Python**: 3.12+
- **Futu OpenD**: 已安装并运行在 `127.0.0.1:11111`
- **操作系统**: Windows（计划任务自动交易依赖 Windows Task Scheduler）

### 1. 克隆项目

```bash
git clone <repository-url>
cd quant
```

### 2. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

核心依赖：
- `futu-api>=10.0` — Futu OpenD 行情接口
- `pandas` — 数据处理
- `numpy` — 数值计算
- `matplotlib` — 图表绘制

### 3. 配置数据源

#### Futu OpenD（唯一输入源）

1. 下载并安装 [Futu OpenD](https://openapi.futunn.com/)
2. 启动 OpenD，确保监听 `127.0.0.1:11111`
3. 登录 Futu 账户（模拟账户即可）

#### 运行事实源

HK/US 日常运行链路采用固定事实源边界：

1. `Futu OpenD` 是唯一输入源，行情、账户和持仓状态均以 Futu 查询为准。
2. `paper_trading/signals/YYYY-MM-DD_MARKET.json` 是唯一结构化输出。
3. `reports/YYYY-MM-DD_market_v3.md` 是同批 JSON 结果的可读渲染。
4. `quant.db` 仅作历史归档/研究查询，不参与当前交易状态判断。

### 4. 验证安装

```bash
cd paper_trading
python daily_runner.py
```

首次运行应输出信号摘要和持仓状态，无报错即安装成功。

## 使用示例

### 每日信号生成

```bash
# 港股+美股信号生成
python paper_trading/daily_runner.py
```

输出示例：
```
============================================================
  纸交易信号生成 v2.0 2026-05-06
============================================================
--- HK (7 stocks) ---
  Analyzing 00700.HK... -> HOLD (+5.2) [Futu]
  Analyzing 09988.HK... -> BUY (+55.0) [Futu]
  ...

  ** TOP BUY **
    09988.HK   BUY          score=+55.0 conf=72% pos=10% rsi_d=51.2
```

### 信号输出格式

每日信号保存为 `paper_trading/signals/YYYY-MM-DD_MARKET.json`，这是当前运行链路的唯一结构化输出，包含：
- 每只标的的融合信号（fusion_level / fusion_score / confidence）
- 技术指标（RSI日/周/月、MACD、缠论分型）
- LLM 因子打分详情
- 止损水平和风控参数

### 自动交易（计划任务）

系统通过 Windows 计划任务 `AutoTradeHKUS` 自动执行：
- **港股**: 9:30-16:00（北京时间）
- **美股**: 21:30-次日 4:00（北京时间）

手动触发：
```bash
python paper_trading/auto_trade.py
```

## 信号系统详解

### 融合规则矩阵

```
                    TA BUY     TA HOLD    TA SELL
    FM BUY      STRONG_BUY  BUY        REDUCED
    FM HOLD     BUY         HOLD       SELL
    FM SELL     REDUCED     SELL       STRONG_SELL
```

### 风控参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 固定止损 | -12% | 基于入场价 |
| 移动止损 | -12% | 基于最高价 |
| 组合回撤止损 | -30% | 基于组合峰值 |
| 单只股票仓位上限 | 20% | 最大个股暴露 |
| 总仓位上限 | 80% | 保留20%现金 |
| 信号反转清仓 | 自动 | BUY->SELL 且浮盈 <5% 时触发 |
| 分批建仓 | 3批 | 首批 1/3，间隔 >=1 交易日 |

### 市场状态分类

| 状态 | 仓位上限 | 触发条件 |
|------|----------|----------|
| BULL | 80% | 趋势向上 + 低VIX |
| RECOVERY | 60% | 触底回升 |
| CRAB | 40% | 震荡市 |
| CORRECTION | 20% | 回调市 |
| BEAR | 10% | 趋势向下 + 高VIX |

## 交易标的

### 港股

| 代码 | 名称 | 备注 |
|------|------|------|
| 00700.HK | 腾讯控股 | 核心标的 |
| 09988.HK | 阿里巴巴 | |
| 03690.HK | 美团 | |
| 09618.HK | 京东 | |
| 00388.HK | 香港交易所 | |
| 01024.HK | 快手 | |
| 06055.HK | 中烟国际 | |

### 美股

| 代码 | 名称 | 备注 |
|------|------|------|
| SPY.US | 标普500 ETF | 美股信号基准 |

## 贡献指南

### 开发规范

1. **代码风格**: PEP 8，UTF-8 编码
2. **类型标注**: 关键函数需添加类型提示
3. **错误处理**: 外部 API 调用必须有 try-except 和降级逻辑
4. **日志**: 使用 print 输出结构化日志，避免 logging 模块

### 提交规范

```
feat: 新增功能
fix: 修复 bug
refactor: 重构代码
docs: 文档更新
perf: 性能优化
test: 测试相关
```

### 分支策略

- `main`: 生产分支，对应 Futu 模拟账户运行版本
- `dev`: 开发分支，新功能在此分支开发和测试
- `feature/*`: 功能分支，从 dev 分出

### 测试

```bash
# 运行信号生成（dry-run，不执行交易）
python paper_trading/daily_runner.py

# 检查信号输出
python -m json.tool paper_trading/signals/$(date +%Y-%m-%d).json
```

### 重要原则

- **止损不可删除** — 宁可跑输基准，不可错杀生存
- **Futu 输入唯一** — HK/US 当前行情、账户、持仓只以 Futu 查询为准
- **JSON 输出唯一** — `paper_trading/signals/` 是当前结构化输出；日报只是可读渲染
- **quant.db 不判当前** — `quant.db` 仅作历史归档/研究查询
- **纸交易优先** — 任何策略变更先在纸交易验证 3 个月
- **信号 > 噪音** — 不追逐市场热点，只响应量化信号

## 交易纪律

- **纸交易模式**: 当前所有交易信号仅供验证，不触碰实盘
- **止损铁律**: 固定-12% + 移动-12% + 组合回撤-30%
- **仓位控制**: 单只20% / 总仓位80%
- **每个信号必须有数据依据**，拒绝无支撑的主观判断

## License

Private. 仅供个人量化研究使用。
