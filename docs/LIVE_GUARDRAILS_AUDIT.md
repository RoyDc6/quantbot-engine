# LIVE Guardrails 实盘执行审计

> 审计日期: 2026-06-03
> 审计范围: futu_trader/ | paper_trading/ | us_trader/ | hk_trader/ | core/ | scripts/ | _archive/misc/
> 状态: P0 只读审计 | 未修改代码 | 未触碰 Git

---

## 当前安全真相

**核心结论：所有下单路径当前使用 `TrdEnv.SIMULATE`，因此即使在 `--live` 模式下，订单也不会到达真实交易所。** 但这不代表 guardrails 完备——实盘安全依赖的是三层外部兜底，而非程序内部控制。

### 三层安全兜底（由外向内）

| 层 | 机制 | 当前状态 |
|----|------|---------|
| L3 — 外部 | Futu OpenD 交易解锁/交易密码 | 需手动解锁，不自动生效 |
| L2 — API | `TrdEnv.SIMULATE` 硬编码 | 全局生效，但无统一开关 |
| L1 — 程序 | 各执行入口的 `dry_run` 默认值 | 大部分默认 `dry_run=True`，但有例外 |

**风险在于：L2 是当前唯一自动生效的防护层，且分散在多个文件中各自定义。一旦有人将某处的 `SIMULATE` 改为 `REAL`，L1 的 `dry_run` 防线可以被 `--live` 绕过，L3 仅靠外部解锁阻挡。**

### 实盘工程标准对比

| 标准 | 当前状态 |
|------|---------|
| 🟢 无代码路径直通实盘 API | ✅ 所有路径使用 SIMULATE |
| 🔴 定时任务自动调用 `--live` | ❌ AutoTradeHK/AutoTradeUS 已启用 |
| 🔴 `--live` 需二次确认 | ❌ 无任何入口要求 pre-trade confirmation |
| 🔴 统一 live 开关 | ❌ 无全局 `LIVE_ENABLED` 标志 |
| 🔴 执行前输出完整交易计划 | ❌ 所有入口仅有事后日志，无事前摘要 |

---

## 执行入口清单（按风险排序）

### ⚠️ 1. `_archive/misc/auto_trade.py` — 最高风险

| 维度 | 内容 |
|------|------|
| **下单能力** | 调度 `futu_bridge --live` + `us_pipeline --live` |
| **dry-run 默认值** | **无** — 脚本本身没有 dry-run 模式，硬编码 `--live` |
| **live flag** | 硬编码 `args=['--live']`，调用时直接进入 Live 模式 |
| **预交易摘要** | 只有执行后的日志批量回显，无执行前确认 |
| **日志** | JSON 日志文件 `auto_trade_log.json`，保留最近 500 条 |
| **风险点** | 唯一一个硬编码 `--live` 的脚本；3 次重试；无执行前拦截 |
| **建议修复** | (1) 增加 `--dry-run` 覆盖开关；(2) 执行前输出完整交易计划摘要；(3) 增加交互式确认 Y/N |

### ⚠️ 2. `paper_trading/futu_bridge.py` — 高

| 维度 | 内容 |
|------|------|
| **下单能力** | 直接调用 `ctx.place_order()` (SELL/BUY)，港股+美股 |
| **dry-run 默认值** | `execute_on_futu(signals, dry_run=True)` — 默认安全 |
| **live flag** | `--live` → `dry_run=not args.live` |
| **预交易摘要** | 无执行前确认，仅有执行后 `[BUY]` / `[SELL]` / `[DRY-RUN]` 日志 |
| **日志** | `futu_bridge_log.json` 记录每次执行摘要 |
| **风险点** | 被 `auto_trade.py` 以 `--live` 调用；无交互确认；未检查市场状态 |
| **建议修复** | (1) 执行前打印完整交易计划摘要；(2) 增加 `--confirm` 标志要求 Y/N |

### ⚠️ 3. `us_trader/us_pipeline.py` — 高

| 维度 | 内容 |
|------|------|
| **下单能力** | 直接调用 `ctx.place_order()`，4 条路径：SIGNAL_EXIT / STOP / TP / BUY |
| **dry-run 默认值** | `run_us_pipeline(dry_run=True)` — 默认安全 |
| **live flag** | `--live` → `dry_run=not args.live` |
| **预交易摘要** | 仅有执行后 `[DRY]` / `[BUY]` / `[STOP]` / `[TP]` |
| **日志** | `us_report_*.json` 保存到 `us_trader/output/` |
| **风险点** | 被 `auto_trade.py` 以 `--live` 调用；STOP/TP 无前置确认；无市场状态预检 |
| **建议修复** | (1) 执行前汇总所有操作 (BUY/SELL/STOP/TP) 并输出摘要；(2) 增加 `--confirm` 标志 |

### ⚠️ 4. `futu_trader/trade_executor.py` — 高

| 维度 | 内容 |
|------|------|
| **下单能力** | `place_order()` 直接调用 `ctx.place_order()`，仅港股 |
| **dry-run 默认值** | `TradeExecutor(dry_run=True)` — 默认安全 |
| **live flag** | `--live` → `dry_run=not args.live` |
| **预交易摘要** | 仅有执行后 `[ORDER]` / `[DRY-RUN]` |
| **日志** | `trade_log` 内存列表，SIGINT 时保存到 `trade_log_*.json` |
| **风险点** | 被 `run_pipeline.py` 调用；无事前确认；无市场状态预检 |
| **建议修复** | (1) 执行前输出订单摘要；(2) 检查 VIX/市场状态后确认 |

### ⚠️ 5. `futu_trader/run_pipeline.py` — 中高

| 维度 | 内容 |
|------|------|
| **下单能力** | 编排 `trade_executor`，包含 Sonnet 验证层 |
| **dry-run 默认值** | `run_pipeline(dry_run=True)` — 默认安全 |
| **live flag** | `--live` → `dry_run=not args.live` |
| **预交易摘要** | 仅有信号摘要（BUY/SELL 数量），无交易计划明细 |
| **日志** | 无统一日志文件，仅 stdout |
| **风险点** | 被 `auto_trade.py` 未直接调用（`futu_bridge.py` 是独立入口）|
| **建议修复** | (1) Step 之间输出交易计划；(2) 增加事中确认点 |

### ⚠️ 6. `core/order_executor.py` — 中

| 维度 | 内容 |
|------|------|
| **下单能力** | 通过 `FutuAdapter.place_order()` 下单 |
| **dry-run 默认值** | `OrderExecutor(dry_run=True)` — 默认安全 |
| **live flag** | **无 CLI** — 仅通过 Python 构造参数控制 |
| **预交易摘要** | 仅有执行后 `[DRY-RUN]` / `[ORDER]` |
| **日志** | 可选 `log_path`；无统一日志目录 |
| **风险点** | 被 signal-only 管线调用（当前无 --live 通路），但只要有调用者传 `dry_run=False` 即可实盘 |
| **建议修复** | (1) 执行前输出完整订单计划；(2) 增加 `total_risk` 检查（订单总额是否超过账户阈值） |

### ⚠️ 7. `core/futu_adapter.py` — 中（垫底层）

| 维度 | 内容 |
|------|------|
| **下单能力** | `place_order()` 使用 `OpenSecTradeContext` |
| **dry-run 默认值** | **无** — 始终下单，无法模拟 |
| **live flag** | **无** |
| **预交易摘要** | 仅返回 `(success, order_id_or_reason)` |
| **日志** | 无自身日志 |
| **风险点** | 硬编码 `TrdEnv.SIMULATE`（安全但不可配置）；不含任何风控检查 |
| **建议修复** | (1) 增加 `trd_env` 参数，默认维持 SIMULATE；(2) 执行前输出日志 |

### ⚠️ 8. `unified_runner.py` — 高（当前主 live 编排入口）

| 维度 | 内容 |
|------|------|
| **下单能力** | 编排 `FusionController` → `OrderExecutor` → `FutuAdapter.place_order()` |
| **dry-run 默认值** | `args.live` 默认 `False` → `dry_run=True` |
| **live flag** | `--live` → `OrderExecutor(dry_run=False)` |
| **预交易摘要** | 仅有事后信号摘要，无执行前交易计划 |
| **日志** | 终端 stdout + 信号 JSON |
| **风险点** | AutoTradeHK/US 定时任务以 `--live` 调用；完全无执行前确认；无 live 二次确认开关 |
| **建议修复** | (1) 实现 `--confirm-live` 双开关；(2) 执行前输出完整交易计划摘要 |

### ✅ 9. `scripts/run_controller.py` — 低

| 维度 | 内容 |
|------|------|
| **下单能力** | **无** — 仅调用 `FusionController` 分析 |
| **live flag** | 定义 `--live` 参数但**为死代码**，未传递到任何下单模块 |
| **建议修复** | 移除 `--live` 参数避免误导，或实现为传递到 `OrderExecutor.dry_run=False` |

### ✅ 10–12. 无下单能力的文件（低风险）

| 文件 | 说明 |
|------|------|
| `core/fusion_controller.py` | 纯信号引擎，**未检测到** `place_order/trade/TrdEnv/SIMULATE` 等交易相关代码 |
| `paper_trading/daily_runner.py` | 仅信号生成，已归档（2026-05-21） |
| `paper_trading/lightweight_runner.py` | 模拟任务队列，仅数据库操作，无 Futu API 调用 |
| `paper_trading/_diagnose_llm.py` | LLM 诊断，无交易能力 |

### ✅ hk_trader/

**目录为空** — 无 Python 文件。

---

## Windows Task Scheduler 检查

实际发现 **两个已启用定时任务**：

| 任务名 | 状态 | 触发器 | 执行命令 |
|--------|------|--------|---------|
| `AutoTradeHK` | **Enabled** | 工作日 09:35 | `E:\quant\scheduled_hk.bat` → `python unified_runner.py --market HK --live` |
| `AutoTradeUS` | **Enabled** | 工作日 21:35 | `E:\quant\scheduled_us.bat` → `python unified_runner.py --market US --live` |

### `scheduled_hk.bat` 调用链

```
AutoTradeHK (09:35 定时触发)
  → scheduled_hk.bat
    → python E:\quant\unified_runner.py --market HK --live
      → OrderExecutor(dry_run=False)
        → core.futu_adapter.place_order()  [当前使用 TrdEnv.SIMULATE]
```

### `scheduled_us.bat` 调用链

```
AutoTradeUS (21:35 定时触发)
  → scheduled_us.bat
    → python E:\quant\unified_runner.py --market US --live
      → OrderExecutor(dry_run=False)
        → core.futu_adapter.place_order()  [当前使用 TrdEnv.SIMULATE]
```

### 定时任务风险评估

| 维度 | 评估 |
|------|------|
| 是否已启用？ | ✅ 是 |
| 是否自动调用 `--live`？ | ✅ 是 |
| 当前是否会导致实盘？ | ❌ 否（`TrdEnv.SIMULATE` 仍为 API 层兜底） |
| 若切换 REAL 是否立即实盘？ | ✅ 是（仍需 OpenD 交易解锁，但程序内无二次保护） |
| 是否有事前确认/拦截？ | ❌ 无 |

**架构性风险**：定时任务自动调用 `--live` 意味着系统在无人值守的凌晨/早间，已在语义上允许执行交易。当前不被执行是因为 Futu API 层的 `SIMULATE` 兜底，并非因为 guardrails 设计。

---

## 当前安全兜底层

当前有三层保护，但全部是外部或分散的：

### L3 — Futu OpenD 交易解锁（外部）
即使 `TrdEnv.REAL`，OpenD 客户端仍需输入交易密码/解锁后才能接受下单指令。**这是目前最强的外部防护层，但不是程序可控的 guardrail。**

### L2 — API 环境（分散定义）
所有下单路径各自定义：
```python
# futu_adapter.py
trd_env = ft.TrdEnv.SIMULATE

# trade_executor.py
trd_env = ft.TrdEnv.SIMULATE

# 其他文件类似
```
**没有统一开关。** 修改任何一处 `SIMULATE` → `REAL`，即可激活该路径的实盘能力。

### L1 — dry_run 默认值（大部分安全）
| 入口 | 默认 | 例外 |
|------|------|------|
| `futu_bridge.py` | `dry_run=True` | `--live` 绕过 |
| `us_pipeline.py` | `dry_run=True` | `--live` 绕过 |
| `trade_executor.py` | `dry_run=True` | `--live` 绕过 |
| `unified_runner.py` | `not args.live` | `--live` 绕过 |
| `auto_trade.py` | **无** | 硬编码 `--live` |
| `futu_adapter.py` | **无** | 无 dry-run 参数 |

---

## 核心风险总结

| # | 风险 | 严重度 | 来源 |
|---|------|--------|------|
| 1 | 定时任务自动调用 `--live`，语义上已允许交易执行 | P0 | AutoTradeHK (09:35) / AutoTradeUS (21:35) |
| 2 | 当前真实资金风险低，因 `TrdEnv.SIMULATE` 仍是 API 层兜底 | P0-2 | 所有下单路径 |
| 3 | 若未来切换 `TrdEnv.REAL`，仍需 OpenD 交易解锁/密码（外部保护） | P0-3 | Futu OpenD |
| 4 | 程序内部缺少 live 二次确认、pre-trade summary、统一 live enable 开关 | P0-4 | 所有执行入口 |
| 5 | `_archive/misc/auto_trade.py` 硬编码 `--live` 且无本地 dry-run | P0 | `auto_trade.py:195,219` |
| 6 | `_archive/misc/direct_order.py` / `direct_order_debug.py` 具备直连 `place_order` 能力，虽使用 SIMULATE 也须列入归档风险 | P0-5 | `_archive/misc/` |
| 7 | 无统一的执行前交易计划确认 | P1 | 所有执行入口 |
| 8 | 部分入口已有仓位/暴露检查，但缺少统一的执行前账户级风险摘要与硬性拦截标准 | P1 | `order_executor.py` / `futu_bridge.py` / `us_pipeline.py` / `core/stop_loss.py` |
| 9 | `core/futu_adapter.py` 无 dry-run 模式，下单即执行 | P1 | `futu_adapter.py:283-328` |
| 10 | `scripts/run_controller.py` 的 `--live` 参数是死代码，具有误导性 | P2 | `scripts/run_controller.py:51` |
| 11 | 日志分散不统一（JSON + stdout 混合，无统一日志目录） | P2 | 各执行器各自写日志 |

---

## 建议修复优先级

### P0 — 必须修复

1. **统一 `TrdEnv` 管理**
   - 所有文件当前各自定义 `trd_env = ft.TrdEnv.SIMULATE`
   - 建议统一从 `config.py` 读取 `LIVE_TRADE_ENABLED` 标志，默认 False
   - 任何脚本要切换到 REAL 都必须修改此标志

2. **`auto_trade.py` 增加本地 `--dry-run` 覆盖开关**
   - 当前硬编码 `--live`，无论外部如何配置都无法拦截
   - 可增加 `DRY_RUN_OVERRIDE = os.environ.get('QUANT_DRY_RUN', '1') == '1'` 环境变量兜底

3. **归档检查 `_archive/misc/direct_order.py` / `direct_order_debug.py`**
   - 确认当前是否使用 `TrdEnv.SIMULATE`
   - 若含 `--live` 或 `TrdEnv.REAL`，立即在归档中标注警告注释

### P1 — 强烈建议

4. **执行前交易计划摘要**
   - 每个能触发下单的 `main` 入口，在执行前输出完整交易计划
   - 格式：`标的、方向、数量、金额、策略根因`
   - 加粗警告行 `⚠️ 即将下单 N 笔，总额 XXXX`

5. **仓位总额与账户余额核验**
   - 执行前检查总持仓暴露是否超过 80%
   - 单笔建仓是否超过总资产 20%
   - 可用现金是否覆盖交易金额

6. **`core/futu_adapter.py` 增加 dry-run 参数**
   - `place_order()` 增加 `dry_run=True` 参数
   - 默认维持当前 SIMULATE 行为

### P2 — 建议改进

7. **统一交易日志** 到 `paper_trading/logs/` 目录
8. **移除 `scripts/run_controller.py` 的 `--live` 死代码**

---

## 实盘上线推荐方案

### 方案 A（保守）— 当前即可实施

**目标：消除定时任务 `--live` 带来的架构风险，回归纯 dry-run 自动任务。**

1. 修改 `scheduled_hk.bat` 和 `scheduled_us.bat`，去掉 `--live` 参数
   ```
   python E:\quant\unified_runner.py --market HK          # 去掉 --live
   python E:\quant\unified_runner.py --market US          # 去掉 --live
   ```
2. 定时任务 AutoTradeHK/AutoTradeUS 继续运行，但只跑 dry-run 信号扫描
3. 当前所有 live 交易依赖手动 `python unified_runner.py --market HK --live`

**优点**：改动极小，立即消除定时任务自动触发的架构风险。  
**代价**：阶段性放弃定时自动交易能力。

---

### 方案 B（实盘准备）— 保留 `--live` 前必须先实现的 guardrails

**目标：在保留定时 `--live` 能力的同时，确保程序级安全。**

必须在保留 `--live` 前，实现以下 **双开关机制**：

```
层级 1：--live                   # 外部 CLI 参数，允许执行交易（当前已有）
层级 2：--confirm-live           # 二次确认，或环境变量 QUANT_LIVE_CONFIRM=YES
```

具体实现要求：

| # | 要求 | 说明 |
|---|------|------|
| 1 | `--live` 保留 | 允许执行交易的 CLI flag |
| 2 | `--confirm-live` 新增 | 必须与 `--live` 同时存在才真正执行；否则仅输出计划摘要后退出 |
| 3 | 环境变量兜底 | 支持 `QUANT_LIVE_CONFIRM=YES` 作为非交互式确认（定时任务适用） |
| 4 | 执行前输出 | `--live` 模式下（即使用于定时任务），每次执行前必须输出完整交易计划 |
| 5 | 日志记录 | 每次 `--live` 调用，无论是否通过 confirm，都记录到不可篡改的日志文件 |

**建议实现时机**：在下一次切换 `TrdEnv.SIMULATE` → `REAL` 之前完成。在此之前，保守方案 A 已足够。

---

### 当前最终评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 定时任务风险 | 🔴 存在 | AutoTradeHK/AutoTradeUS 已启用，带 `--live` |
| 实盘资金风险 | 🟢 低 | `TrdEnv.SIMULATE` + OpenD 交易解锁双重外部保护 |
| 架构 guardrails | 🟡 不足 | 无 unified live 开关、无 pre-trade confirmation |
| 可修复成本 | 🟢 低 | 去掉 `--live` 一行即可关闭最大风险点 |
