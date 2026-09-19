# Northstar-D1

Northstar-D1 是共享模型核心、按市场独立部署的 D1 量化研究模型。
公开模型身份不描述内部决策组件；实现与技术诊断保留在本包内部。

两个部署互不混跑：

- `NORTHSTAR_D1_HK`：仅港股标的、香港交易日历与香港时区。
- `NORTHSTAR_D1_US`：仅美股标的、美国交易日历与纽约时区。

## 边界

- 每个标的、每次扫描都重新连接 Futu OpenD，并同时请求实时 snapshot、
  Futu 交易日历和 QFQ 日线；不读缓存，也没有其他数据源 fallback。
- OpenD 服务端时钟、报价登录状态、程序状态、盘中 snapshot 年龄和最新已完成
  交易日日线必须全部通过门禁；任何一项失败，该标的 fail closed，不输出信号。
- 复用 `FutuAdapter` 获取 QFQ 日线，复用 `UniverseManager` 获取相同 US/HK 标的。
- 不依赖任何外部策略中控。
- 模型包本身不访问账户，不调用下单 API；经用户授权的外部
  `research.northstar_d1_futu_sim` 层可读取不可变产物并仅调用 Futu `SIMULATE`。
- `paper_intents` 是研究信号，不是订单。
- 当前模型绩效状态为 `NOT_EVALUATED`；完成样本外验证前，
  `formal_publish_allowed` 固定为 `false`。
- 实时/最新价只用于数据新鲜度审计；模型信号只使用已完成 QFQ 日线。
- 普通交易日当日本地市场日线在 16:15 前一律排除，防止盘中日K冒充收盘信号；
  缩短交易日按 Futu `MORNING` 日历类型使用对应收盘缓冲。
- 既有策略、中控和执行链均不导入、不修改。
- 不提供 `ALL` 入口；HK/US 分别触发、分别失败关闭、分别写入输出分区。
- 正式 CLI 对每个市场调度槽执行 exactly-once；同一槽已有哈希验证成功产物时，
  返回 `DUPLICATE_SUPPRESSED`，不会再次请求 Futu 或重算信号。
- 每次运行先写入按 `run_id` 隔离的不可覆盖审计副本，再以原子替换更新日期主文件；
  JSON/Markdown 的 SHA256 记录在 manifest 中。
- JSON 为 schema `2.2`，每个标的保留趋势、结构、序列与最终决策的当次审计轨迹；
  报告质量检查失败时整次运行关闭，不把不完整报告记为 `RUN_PASS`。

模型比例表示研究层目标暴露或降低暴露的幅度；paper intent 另受单标的
20%研究风控上限约束。模型不读取实际持仓。

## 使用

```powershell
cd E:\quant
# 港股独立部署
python -m research.northstar_d1.hk
python -m research.northstar_d1.hk --symbol 09988.HK --no-write

# 美股独立部署
python -m research.northstar_d1.us
python -m research.northstar_d1.us --symbol AAPL.US --no-write

# 分市场回测
python -m research.northstar_d1.backtest_hk
python -m research.northstar_d1.backtest_us
```

港股报告写入 `research/northstar_d1/output/hk/`，美股报告写入
`research/northstar_d1/output/us/`。日期主文件包括 Markdown、JSON 和 manifest；
不可覆盖审计副本位于各市场的 `runs/<run_id>/`，运行状态和追加式事件账本位于
各市场的 `.runtime/`。
回测是无未来数据的 long-only 样本内诊断，信号在收盘计算、下一时段生效；
它不能替代样本外验证。

样本外验证必须按 [`VALIDATION_PLAN.md`](VALIDATION_PLAN.md) 的冻结、预登记和
forward evidence 流程单独进行。现有历史回测不得重新命名为 OOS。

Futu 模拟前向执行使用独立入口，不修改冻结模型文件：

```powershell
python -m research.northstar_d1_futu_sim.runner --market HK          # 只预览
python -m research.northstar_d1_futu_sim.runner --market US --execute-sim
```

执行层按模拟账户总资产换算 BUY 目标仓位，按当前可卖持仓换算 SELL 减仓；无差额、
不足一手、报价缺失或账户不可读均失败关闭或跳过。订单日志和回执保存在该执行层目录，
真实交易始终禁用。

正式运行可由受信任的自动化入口通过环境变量附加进程级 provenance；缺失时报告会
明确显示 `NOT_PROVIDED`，不会猜测调用来源：

- `NORTHSTAR_AUTOMATION_ID`
- `NORTHSTAR_AUTOMATION_RUN_ID`
- `NORTHSTAR_AUTOMATION_CONVERSATION_ID`
- `NORTHSTAR_AUTOMATION_RUN_KIND`
- `NORTHSTAR_INVOCATION_SOURCE`

自 2026-08-19 起，正式自然调度由 Windows Task Scheduler 承担，WorkBuddy 不再是
执行或调度权威，只在 Windows 产物生成后延时执行只读交付。两个运行入口分别为：

```text
python -m research.northstar_d1.windows_scheduler_hk
python -m research.northstar_d1.windows_scheduler_us
```

Windows 入口计算最近一个已到期的合法调度槽，并注入
`WINDOWS_TASK_SCHEDULER / SCHEDULED / MISSED_RECOVERY` 来源字段。错过时点可以补跑，
但必须保留原计划时间和实际开始时间；更新槽已经成功时由 exactly-once 返回
`DUPLICATE_SUPPRESSED`。已安装合同与回退证据见
[`WINDOWS_SCHEDULER_SYNC.md`](WINDOWS_SCHEDULER_SYNC.md)。

WorkBuddy 的 HK/US automation 分别在 16:40 和 10:20 调用
`research.northstar_d1.delivery_hk` / `research.northstar_d1.delivery_us`。helper 只接受最新应到
Windows 槽及 hash 验证通过的不可变 Markdown，并通过 `present_files` 恰好交付一份；它不调用
Futu、模型、账户、订单或 Windows runner。WorkBuddy 平台自动 memory 仅是内部记账，不能作为
报告证据。该方案不修改 `app.asar`，WorkBuddy 更新后无需重装补丁。

下列 WorkBuddy 专用入口只为历史产物核验和显式诊断保留，不再承担自然调度：

```text
python -m research.northstar_d1.workbuddy_hk
python -m research.northstar_d1.workbuddy_us
```

WorkBuddy 受控入口先只读核验工作目录、市场、automation ID 与唯一的 WorkBuddy 运行身份。它接受
三种身份模式：自然调度 runtime；`IN_PROGRESS manual_test` 与同批创建的活跃后台会话；
或由既有 automation run 唯一链接的活跃后台 follow-up 会话。`manual_test` 的
`runs_json` 尚未在运行中写入 conversation ID 时，只允许使用同工作区且创建时间相差不超过
2 秒的唯一 run/session 对；任何多匹配仍失败关闭。证据一致后，`scheduled`、同一调度本地
日期的 `missed`、`manual_test` 和 `on_demand_followup` 均可注入 provenance，并在该次用户
触发中调用对应市场一次。非运行窗口、跨市场身份或不唯一证据均在 Futu/模型前以
`FAILED_CLOSED` 结束。运行后仍可执行一次只读调度器回查：

WorkBuddy Desktop 5.3.8 原始的 Run now 路径以 `persistRuntimeState=false` 启动，且在会话
启动前不写 `IN_PROGRESS manual_test` 标记，因此严格入口无法证明当前运行。当前机器已安装
版本锁定的兼容补丁：只把 `persistInProgressRun` 提到会话分派之前，仍保持
`persistRuntimeState=false`，不会把手动运行写成自然调度或推进调度时间。补丁工具位于
`tools/patch_workbuddy_manual_test_provenance.mjs`；它仅接受已审核的 5.3.8 原始 SHA256，
重算 ASAR 完整性，并保留可恢复的原包。WorkBuddy 升级后必须重新审核，不能对未知版本强套。

```text
python -m research.northstar_d1.workbuddy_provenance --automation-id <automation-id>
```

回查器只接受 allowlist 中的任务；任一证据不唯一即返回 `NOT_PROVIDED`。它不调用
Futu、不运行模型、不写 WorkBuddy。报告内 provenance 与调度器回读应相互吻合；
`WorkBuddy Automation Run ID` 也不得与模型产物的 `Northstar Archive Run ID` 混用。

## 三层决策审计

Markdown 报告新增私有“结构信号”和“序列信号”表：

- 趋势：市场状态与当次趋势事件；
- 结构：底部/顶部阶段、持续天数、修边作用及比例/置信度变化；
- 序列：计数、阶段、方向、与动作关系及置信度变化；
- 决策：始终显示 `候选动作→实际输出动作`，数据门控关闭时可见
  `BUY/SELL→HOLD`，避免把门控误认为模型未触发。

这些字段由模型直接生成，WorkBuddy 只核验、不重算。报告只披露状态与实际影响，
不披露公式、参数名、周期、阈值、原始内部指标值或内部原因码；原因码仅保留在内部
JSON 审计证据中。结构只做趋势修边，序列只调整已有动作的置信度，不会单独制造
BUY/SELL。

正式 CLI 退出码：

- `0`：本次 `RUN_PASS`，或同调度槽的已验证成功运行被安全抑制；
- `1`：数据、产物或运行门禁失败并关闭；
- `2`：拒绝使用合并市场入口；
- `3`：已有运行进行中、重试等待期、重试耗尽或既有成功产物损坏。

`--no-write` 是诊断入口，不写运行状态或报告；它不用于 WorkBuddy 正式任务。

`python -m research.northstar_d1` 和
`python -m research.northstar_d1.backtest` 会拒绝执行，并提示选择 HK 或 US，
防止一次调用混合两个市场。

## 验证

```powershell
python -m pytest research/northstar_d1/tests -q
```
