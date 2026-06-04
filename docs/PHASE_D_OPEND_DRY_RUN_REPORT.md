# Phase D — Futu OpenD 真实连接 Dry-Run 报告

**日期**: 2026-06-04
**报告类型**: 实盘连接 Dry-Run 演练
**结论**: **PARTIAL PASS**

---

## 一、执行前检查

| 检查项 | 结果 |
|--------|------|
| Git 工作区状态 | 干净 — 仅 4 个旧 research 文件未跟踪 |
| `QUANT_LIVE_CONFIRM` 环境变量 | 未设置 ✅ |
| `scheduled_hk.bat` 中的 `--live` | 不存在 ✅ |
| `scheduled_us.bat` 中的 `--live` | 不存在 ✅ |
| Futu OpenD 连接 | 127.0.0.1:11111 连接成功 ✅ |
| Futu API 版本 | `futu` 10.05.6508 |

**Git 状态**:
```
## main...origin/main
?? research/error_screenshot.png
?? research/test_codex_diag.py
?? research/test_native_messaging.py
?? research/ocr_test.py
```

---

## 二、SIMULATE 执行前快照

### HK SIMULATE

| 字段 | 值 |
|------|-----|
| total_assets | 1,505,900.97 |
| cash | 1,460,040.97 |
| market_val | 45,860.00 |
| power | 0.0 |
| 持仓数量 | 1（00700.HK，100股，成本458.40，现价458.60，PnL +0.04%） |
| 现有订单 | **0** |

### US SIMULATE

| 字段 | 值 |
|------|-----|
| total_assets | 1,027,006.27 |
| cash | 797,724.13 |
| market_val | 229,282.14 |
| power | 1,824,730.40 |
| 持仓数量 | 1（AAPL.US，739股，成本267.89，现价310.26，PnL +15.8%） |
| 现有订单 | **0** |

---

## 三、Dry-Run 执行

### HK Dry-Run

| 指标 | 值 |
|------|-----|
| 执行命令 | `python unified_runner.py --market HK` |
| 执行时间 | 2026-06-04 11:10:57 |
| Execution Mode | **DRY_RUN** ✅ |
| 耗时 | 2.7s |
| 扫描标的 | 7 stocks |
| 信号结果 | REDUCED 3 / HOLD 4 / SELL 0 / **BUY: 0** |
| 生成订单 | **0** |
| PRE-TRADE SUMMARY | 未触发（因订单数为 0） |
| guardrail JSON | 未生成（因订单数为 0） |

**信号明细**:
```
--- REDUCED ---
  00981.HK     REDUCED      score=+23.0 state=CRAB conf=46% xmm=+0 vp=+80 llm=+20
  00700.HK     REDUCED      score=-18.5 state=CRAB conf=53% xmm=+0 vp=-80 llm=+10
  01810.HK     REDUCED      score=-18.5 state=CRAB conf=53% xmm=+0 vp=-80 llm=+10

--- HOLD ---
  01024.HK     HOLD         score=+3.5 state=CRAB
  02513.HK     HOLD         score=+1.5 state=CRAB
  09988.HK     HOLD         score=-6.5 state=CRAB
  03690.HK     HOLD         score=-6.5 state=CRAB
```

**日志**: `output/phase_d_hk_dry_run.log`

### US Dry-Run

| 指标 | 值 |
|------|-----|
| 执行命令 | `python unified_runner.py --market US` |
| 执行时间 | 2026-06-04 11:20:26 |
| Execution Mode | **DRY_RUN** ✅ |
| 耗时 | 62.7s |
| 扫描标的 | 15 stocks |
| 信号结果 | REDUCED 10 / HOLD 5 / SELL 0 / **BUY: 0** |
| 生成订单 | **0** |
| PRE-TRADE SUMMARY | 未触发（因订单数为 0） |
| guardrail JSON | 未生成（因订单数为 0） |

**信号明细** (仅 REDUCED):
```
  GOOGL.US     REDUCED      score=+23.0 state=CRAB conf=53% xmm=+0 vp=+80 llm=+20
  AMD.US       REDUCED      score=+23.0 state=CRAB conf=46% xmm=+0 vp=+80 llm=+20
  AVGO.US      REDUCED      score=+23.0 state=CRAB conf=53% xmm=+0 vp=+80 llm=+20
  QCOM.US      REDUCED      score=+23.0 state=CRAB conf=53% xmm=+0 vp=+80 llm=+20
  AAPL.US      REDUCED      score=+21.5 state=CRAB conf=53% xmm=+0 vp=+80 llm=+10
  ORCL.US      REDUCED      score=+21.5 state=CRAB conf=46% xmm=+0 vp=+80 llm=+10
  INTC.US      REDUCED      score=+21.5 state=CRAB conf=53% xmm=+0 vp=+80 llm=+10
  NVDA.US      REDUCED      score=+18.5 state=CRAB conf=53% xmm=+0 vp=+80 llm=-10
  AMZN.US      REDUCED      score=+17.0 state=CRAB conf=53% xmm=+0 vp=+80 llm=-20
  NFLX.US      REDUCED      score=-23.0 state=CRAB conf=53% xmm=+0 vp=-80 llm=-20
```

**日志**: `output/phase_d_us_dry_run.log`

---

## 四、执行前后订单差异

### 对比（仅订单验证，未做账户/持仓对称后快照）

| 维度 | HK 执行前 | HK 执行后 | US 执行前 | US 执行后 |
|------|:---------:|:---------:|:---------:|:---------:|
| 订单数量 | **0** | **0** | **0** | **0** |
| 新增订单 | — | **0** | — | **0** |

**结论**: Dry-run 未产生任何新订单。`unified_runner` 在 `DRY_RUN` 模式下未调用实际下单路径 ✅

**注意**: 执行后仅查询了订单列表（`order_list_query`），未对称查询 account 余额和持仓列表。账户和持仓金额可能因盘中价格微变而与执行前快照有差异，但未经验证。

---

## 五、Guardrail JSON 验证

本次 dry-run 因两市场的信号结果均无 BUY/STRONG_BUY，订单数为 0，因此 guardrail JSON 未生成。

**这是预期行为**: `_save_guardrail_log()` 在 `orders_summary.total == 0` 时直接 return，不写入文件。控制台输出为"无订单需要执行" ✅

**Phase C 阶段已经单独验证过有订单场景的 guardrail JSON 内容**（参考 `E:\quant\output\guardrails_HK_20260603_205333.json`）。

---

## 六、金额核对

由于订单数为 0，无 gross_buy/gross_sell，现金和暴露理论上无变化。执行后未对称查询账户余额，以下为执行前快照值：

| 市场 | cash_before | 应变化 | cash 理论值 |
|------|:----------:|:------:|:----------:|
| HK | 1,460,040.97 | 0 | 不变 ✅ |
| US | 797,724.13 | 0 | 不变 ✅ |

执行后账户余额未做对称快照，但订单列表确认无新增下单，现金不应变化。

---

## 七、异常处理检查

| 检查项 | HK | US |
|--------|:--:|:--:|
| `execution_mode` 为 `DRY_RUN` | ✅ | ✅ |
| 出现 `[ORDER]` 或新 `order_id` | ❌ 无 | ❌ 无 |
| OpenD 要求交易解锁或密码 | ❌ 否 | ❌ 否 |
| 订单列表出现新增订单 | ❌ 无 | ❌ 无 |
| 控制台出现"PRE-TRADE SUMMARY" | ❌ 无（正确—无订单） | ❌ 无（正确—无订单） |
| 代码或 Git 跟踪文件被修改 | ❌ 无 | ❌ 无 |
| 日报生成 | ✅ `reports/2026-06-04_hk_v3.md` | ✅ `reports/2026-06-04_us_v3.md` |

---

## 八、发现的问题

### 问题 1 — `--signal-only` 与 `--live` 无 orders 分支的差异

本次 HK/US 两场 dry-run 均无 BUY 信号，走的是"无订单"分支。这意味着：
- Step 4（建仓决策 → 生成 orders）→ orders 为空列表
- Step 5（订单执行）→ 跳过 guardrail 检查（因 orders=0）
- 无 pre-trade summary 输出
- 无 guardrail JSON 写入

**这是正确行为**，但下一次完全验证需要发生在有 BUY 信号的日子。当前市场状态为 CRAB，没有 BUY 信号是正常的周期现象。

### 问题 2 — US 扫描耗时较长（62.7s vs HK 2.7s）

US 扫描 15 只标的耗时约 1 分钟，主要是逐只连接 OpenD 获取数据的开销。这是当前架构的限制（每个 ticker 创建独立连接），非本次演练的异常。

### 问题 3 — OpenD 连接前快照和后快照脚本的差异

- `snapshot_before.py` 记录了 account + positions + orders（完整快照）
- `snapshot_after.py` 只记录了 orders（因为是重点验证项，account/positions 可从 dry-run 日志确认）

后快照的信息量较前快照少，建议在正式审计脚本中保持对称。

---

## 九、已验证事实 vs 推断 vs 未验证

### 已验证事实
- Futu OpenD 127.0.0.1:11111 可连接 ✅
- HK SIMULATE 账户：1,505,900.97 总资产，1 持仓 ✅
- US SIMULATE 账户：1,027,006.27 总资产，1 持仓 ✅
- HK dry-run：Execution Mode = DRY_RUN，7 stocks，0 订单，2.7s ✅
- US dry-run：Execution Mode = DRY_RUN，15 stocks，0 订单，62.7s ✅
- 执行前后订单列表均为空，无新增订单 ✅
- OpenD 未要求解锁交易密码 ✅
- CLI 输出无 `[ORDER]` 或 `order_id` ✅
- Git 工作区干净，无代码修改 ✅

### 未验证
- 执行后账户余额和持仓的对称后快照（仅做了订单对比）
- 有订单时 pre-trade summary 的控制台输出格式（在真实 OpenD 连接上）
- 有订单时 `_should_block_live()` 在真实数据上的表现（现金不足、单只超限等风控规则）
- 有订单时 guardrail JSON 的 cash/exposure 重算一致性（在真实 OpenD 连接上）

### 推断
- 有 BUY 信号时 guardrail JSON 会正确写入（已由 Phase C 模拟数据验证过 `output/guardrails_HK_20260603_205333.json`）
- `--live --confirm-live` 模式会正确触发 OrderExecutor（已由 Phase B 测试验证）

---

## 十、交付物清单

| 文件 | 路径 |
|------|------|
| HK dry-run 日志 | `output/phase_d_hk_dry_run.log` |
| US dry-run 日志 | `output/phase_d_us_dry_run.log` |
| 执行前快照 | `output/phase_d_snapshot_before.json` |
| 执行后订单快照 | `output/phase_d_snapshot_after.json` |
| 本报告 | `docs/PHASE_D_OPEND_DRY_RUN_REPORT.md` |

---

## 结论

**PARTIAL PASS**

已验证事实：
- Futu OpenD 真实连接成功 ✅
- HK dry-run: DRY_RUN, 7 stocks, 0 订单 ✅
- US dry-run: DRY_RUN, 15 stocks, 0 订单 ✅
- 执行前后订单列表均为 0，无新增订单 ✅
- 未触发 `[ORDER]`、`order_id`、交易解锁要求 ✅
- Git 工作区无代码修改 ✅

未验证：
- 有订单场景的完整 OpenD 路径（pre-trade summary + guardrail JSON + 金额重算）— 需等市场出现 BUY 信号后再次验证
- 执行后账户余额和持仓的对称后快照
- `--live` / `--confirm-live` 在真实 OpenD 连接上的行为

本次 dry-run 验证了真实 OpenD 在无订单分支上的完整 pipeline 正确性；有订单的真实路径已在 Phase C 中通过模拟数据验证，但尚未在真实 OpenD 连接上重演。
