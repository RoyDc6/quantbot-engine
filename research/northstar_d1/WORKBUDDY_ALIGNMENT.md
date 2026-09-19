# Northstar-D1 与 WorkBuddy 对齐契约

> **历史文档**：自 2026-08-19 起，自然调度与补跑合同由
> [`WINDOWS_SCHEDULER_SYNC.md`](WINDOWS_SCHEDULER_SYNC.md) 接管。本文中的 WorkBuddy
> 调度补偿、RRULE 和补丁说明不再是当前执行依据。

**版本**：1.9.2 + WorkBuddy 5.3.8 manual-test persistence patch
**日期**：2026-08-07
**契约状态**：`HK_FIRST_SCHEDULED_RUN_PASS / US_ON_DEMAND_ACCEPTED / PROVENANCE_CLOSED / PRIVATE_DECISION_AUDIT_ENABLED / FINAL_PRESENTATION_CONTRACT_ENABLED / WORKBUDDY_PROCESS_PROVENANCE_INJECTION_CONFIGURED`
**模型状态**：`RESEARCH_ONLY / NOT_EVALUATED / formal_publish_allowed=false`

## 一、对齐结论

Northstar-D1 是独立的 D1 量化研究模型，不属于任何既有策略中控或执行链。
它使用内部已核对的私有决策核心，但对外只暴露
Northstar-D1 模型身份、数据证据和研究动作，不披露因子名称、公式、周期、阈值或
融合细节。

模型核心可共享，但运行面必须拆成两个相互独立的部署：

| 部署 | 唯一 ID | 市场 | 市场日历时区 | 命令 | 输出分区 |
|---|---|---|---|---|---|
| Northstar-D1 HK | `NORTHSTAR_D1_HK` | 仅港股 | `Asia/Hong_Kong` | `python -m research.northstar_d1.workbuddy_hk` | `output/hk/` |
| Northstar-D1 US | `NORTHSTAR_D1_US` | 仅美股 | `America/New_York` | `python -m research.northstar_d1.workbuddy_us` | `output/us/` |

禁止创建合并的 `ALL` 任务，禁止在一个任务中依次运行两个市场，禁止使用
`--market` 动态切换市场。`python -m research.northstar_d1` 是拒绝入口，正常应以
退出码 2 结束。

## 二、标的与路由

标的由现有 `UniverseManager` 分市场提供。2026-08-03 的对齐快照为：

- HK（7）：`00700.HK`、`09988.HK`、`03690.HK`、`01024.HK`、
  `01810.HK`、`00981.HK`、`02513.HK`。
- US（15）：`AAPL.US`、`AMZN.US`、`MSFT.US`、`GOOGL.US`、`META.US`、
  `NVDA.US`、`TSLA.US`、`AMD.US`、`AVGO.US`、`ORCL.US`、`NFLX.US`、
  `CRM.US`、`ADBE.US`、`INTC.US`、`QCOM.US`。

自动化不得复制维护另一份标的清单；正式运行不传 `--symbol`，由相应市场入口在
运行时读取该市场标的。HK 入口收到 `.US` 标的、US 入口收到 `.HK` 标的时必须
失败，不得纠正后继续混跑。

## 三、唯一数据源与新鲜度契约

Northstar-D1 每次调用的唯一市场数据源是本机 Futu OpenD
（`127.0.0.1:11111`）。每个标的、每次扫描都必须重新执行：

1. OpenD 实时或最新 snapshot 查询；
2. Futu 交易日历查询；
3. Futu QFQ 已完成日线查询。

硬性规则：

- `source=FUTU_OPEND_LIVE`；
- `cache_used=false`、`fallback_used=false`；
- OpenD 报价已登录、程序状态为 `READY`；
- OpenD 服务端时钟偏差不超过 120 秒；
- 市场处于实时交易阶段时，snapshot 年龄不超过 300 秒；
- snapshot 到日线完成的整条取数链不超过 300 秒；
- 最新日线必须与本次 Futu 交易日历计算出的最后已完成交易日严格一致。

任何一项无法证明时，对相应标的执行 `FAIL_CLOSED_NO_SIGNAL`。禁止改用 Yahoo、
yfinance、缓存、旧报告或其他行情源补齐。

实时或最新 snapshot 只用于证明本次调用的数据新鲜度；模型信号只使用已完成的
QFQ 日线。普通交易日 16:15 本地市场时间前，当日 K 线不得被视为已完成；缩短
交易日以 Futu 日历类型及模型内置缓冲为准。

## 四、运行与成功判定

两个自动化都必须以 `E:\quant` 为工作目录：

```powershell
# HK 任务唯一正式命令
python -m research.northstar_d1.workbuddy_hk

# US 任务唯一正式命令
python -m research.northstar_d1.workbuddy_us
```

进程退出码不是唯一成功依据。WorkBuddy 必须回读本次生成的 JSON，并逐项验证：

```text
deployment_id == 目标任务的部署 ID
market == 目标市场
market_isolation.single_market_only == true
market_isolation.combined_run_allowed == false
data_ready == true
freshness_ready == true
summary.errors == 0
errors == []
execution.enabled == false
execution.account_access == false
execution.order_api_called == false
orders == []
fills == []
run_verdict == RUN_PASS
run_gate_checks 的 13 个布尔值全部为 true
report_quality_ready == true
report_quality_checks 的全部布尔值为 true
```

`report_quality_checks` 是独立于原 13 项运行门禁的报告完整性门禁，不改变原 13 项
数据与零执行检查。JSON 顶层 schema 为 `2.2`，每个 `signals[]` 必须包含
`decision_audit`，并完整提供 `trend`、`structure`、`sequence`、`decision` 四段。
其中必须分别记录 `candidate_action` 与 `emitted_action`；若数据门禁把候选动作关闭为
HOLD，也必须保留该覆盖路径，不能把它误报为模型从未触发。

只有以上全部成立，自动化运行才可记为 `SUCCESS`。以下情况必须显式记为
`FAILED_CLOSED`，不得写成“成功但无信号”：

- 命令退出码非 0；
- 找不到对应市场、本次信号日的 JSON；
- `data_ready=false` 或 `freshness_ready=false`；
- `summary.errors>0` 或 `errors` 非空；
- 部署 ID、市场或输出分区不匹配；
- 发现任何账户访问、订单或成交记录。
- `run_verdict` 不是 `RUN_PASS`，或 `run_gate_checks` 任一项不是 `true`；
- `report_quality_ready=false`、报告完整性检查失败，或任一标的缺少三层决策审计；
- manifest 缺失，或 archive JSON/Markdown 的 SHA256 与 manifest 不一致。

`BUY/SELL/HOLD` 都是合法研究结果；`HOLD` 不代表任务失败。`paper_intents` 只是
研究层意向，不是订单，也不能被转送至任何执行器。

## 五、产物契约

HK 任务只读取和交付：

```text
E:\quant\research\northstar_d1\output\hk\YYYY-MM-DD_northstar_d1_hk.json
E:\quant\research\northstar_d1\output\hk\YYYY-MM-DD_northstar_d1_hk.md
E:\quant\research\northstar_d1\output\hk\YYYY-MM-DD_northstar_d1_hk.manifest.json
E:\quant\research\northstar_d1\output\hk\runs\<run_id>\...
```

US 任务只读取和交付：

```text
E:\quant\research\northstar_d1\output\us\YYYY-MM-DD_northstar_d1_us.json
E:\quant\research\northstar_d1\output\us\YYYY-MM-DD_northstar_d1_us.md
E:\quant\research\northstar_d1\output\us\YYYY-MM-DD_northstar_d1_us.manifest.json
E:\quant\research\northstar_d1\output\us\runs\<run_id>\...
```

对外只交付 Markdown；JSON 作为内部可复核证据。Markdown 必须包含“结构信号”和
“序列信号”两张私有审计表，展示当次状态、方向、阶段、持续天数、作用及对比例/
置信度的实际影响；只展示模型已经生成的审计结果，WorkBuddy 不得重新计算因子。
WorkBuddy 的最终回复也必须逐标的完整复现这两张表；仅写“审计存在”“检查通过”或
信号汇总不算完成。手工诊断或显式要求复核时，即使信号日未变化，也必须标记
`UNCHANGED_SIGNAL_DATE` 后完整展示两表，不得把旧信号伪装成新交易日。最终回复缺少
任一完整表时，交付状态必须为 `REPORT_PRESENTATION_INCOMPLETE`，不得写成成功。
公开内容不得出现旧模型名称、私有因子名称、参数名、公式、周期、阈值、原始内部
指标值、内部原因码或内部融合路径；内部原因码只保留在 JSON 审计证据中，不复制到
交付 Markdown。每次运行需保留 WorkBuddy 自身的
运行记录，包括任务 ID、计划时间、实际开始/结束时间、退出码、信号日、输出路径、
数据门禁结论、`run_id`、attempt、manifest SHA256 和错误摘要。日期主文件允许原子
刷新，但 `runs/<run_id>/` 审计副本禁止覆盖。

## 六、自动化调度契约

创建两个独立、启用状态的 WorkBuddy 自动化。HK 保持港股收盘后运行，只有 US
改为北京时间上午运行：

| 任务名 | WorkBuddy 平台计划 | 有效执行硬闸 | 信号交易日 | 目标空间 |
|---|---|---|---|---|
| `Northstar-D1 HK Daily` | 周一至周五 16:25 `Asia/Hong_Kong` | 实际 HKT 早于 16:20 时立即 `SKIPPED_EARLY` | Futu 确认的最后已完成 HK 交易日；普通交易日可为当日 | `quant` |
| `Northstar-D1 US Daily` | 周二至周六 10:10 `Asia/Shanghai` | 实际北京时间早于 10:00 时立即 `SKIPPED_EARLY` | Futu 确认的上一已完成 US 交易日 | `quant` |

平台计划均已按现场数据库中的提前唤醒行为闭环校准。HK 界面显示 16:25，对应真实
`next_run_at` 为 HKT 16:20；US 界面显示 10:10，对应真实 `next_run_at` 为北京时间
10:00。两个任务使用不同 RRULE：

```text
HK: FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=16;BYMINUTE=25
US: FREQ=WEEKLY;BYDAY=TU,WE,TH,FR,SA;BYHOUR=10;BYMINUTE=10
```

US 固定使用北京时间后不再按纽约夏令时调整 RRULE。北京时间 10:00 对应前一自然日
22:00 EDT 或 21:00 EST，均已晚于美股常规收盘。HK 则在 HKT 16:20 收盘后运行；
普通交易日可使用 Futu 已确认完成的当日日线，缩短交易日和休市日仍以本次 Futu
日历为准，绝不能把盘中 K 线视为收盘信号。

计划星期只表示唤醒窗口；是否交易日、最后已完成交易日及缩短交易日必须由本次 Futu
日历确认。休市日不得伪造当日信号；若模型返回的信号日没有晚于该市场上次
已成功交付的信号日，可记录为 `NO_NEW_COMPLETED_SESSION`，不得重复对外发送旧报告。

若 HK 实际开始早于 HKT 16:20，或 US 实际开始早于北京时间 10:00：立即结束，
不等待、不睡眠到硬闸时间、不调用 Futu、不在同一次运行中重试。若正式运行失败关闭，可在 5 分钟后最多重试 1 次；
重试仍必须重新调用 Futu，禁止复用第一次的 snapshot、日线或 JSON。重试不得把
失败证据删除或覆盖成“从未失败”。

每次 WorkBuddy 唤醒只能发起一个进程调用。不得先用 Windows 路径执行后又改用
`/e/quant` 路径重跑；首次命令成功后绝对禁止第二次调用。Northstar-D1 CLI 自身以
市场、计划日期和计划时刻组成调度槽，并实施 exactly-once：

- 首次运行获得锁并生成 `run_id`；
- 同一调度槽已有哈希验证成功产物时返回 `DUPLICATE_SUPPRESSED`，不调用 Futu；
- 首次失败后 5 分钟内返回 `RETRY_DELAY_ACTIVE`；5 分钟后最多允许 attempt 2；
- attempt 2 再失败则返回 `RETRY_EXHAUSTED`；
- 成功产物缺失或哈希不匹配时返回 `BLOCKED_CORRUPT_PRIOR_SUCCESS`，不得静默重跑。

WorkBuddy 不得读取、创建或编辑 `.workbuddy/memory/` 或 automation 私有 `memory.md`；
自动化数据库运行记录、Northstar-D1 manifest 和不可覆盖 run archive 才是权威审计
证据，避免共享记忆串入其他 automation ID 或过期调度信息。

## 七、执行边界

WorkBuddy 获得的是研究自动化权限，不是交易权限：

- 不访问账户、持仓、现金、购买力或交易密码；
- 不下单、不撤单、不改单，不连接任何订单执行器；
- 不运行任何既有策略中控、统一运行器、订单执行器、模拟交易器或旧模型自动化；
- 不修改 Northstar-D1 源码、参数、标的池、Futu 适配器或其他生产核心；
- 不自动发送到邮箱、聊天群、券商或外部系统；
- 不因失败、无 BUY 或正式发布被禁止而放宽门禁；
- 不创建 HK/US 合并任务，不改变任何既有自动化状态。

当前 `validation_status=NOT_EVALUATED`，因此
`formal_publish_allowed=false` 是预期状态，不是数据失败。报告可作为内部研究日报，
不得描述为已验证策略或交易建议。

## 八、WorkBuddy 创建后的回执要求

创建完成后，WorkBuddy 必须返回可核验的现场证据，而不是只回复“已设置”：

- 两个实际 automation ID；
- 两个任务的 `ENABLED` 状态；
- 各自时区、计划时间、下一次运行时间；
- 各自绑定的唯一命令和工作目录；
- HK/US 未合并、未设置 `--market` 的回读证据；
- 其他自动化状态未改变的说明；
- 首次正式运行后，对应 JSON/Markdown 路径及全部数据门禁结论。

在收到以上回执前，状态只能记为 `AUTOMATION_REQUESTED`，不能宣称自动化已创建。

## 九、2026-08-03 现场运行与优化核验

HK 首次真实计划运行已形成数据库证据：

- automation ID：`automation-1785736457372`；
- `automation_runs=1`，`thread_id=run-1785745216061-4`；
- 北京时间 16:20:16 开始、16:21:31 完成，`result_success=1`；
- 信号日 `2026-08-03`，7 个标的均为 `FUTU_OPEND_LIVE`，零缓存、零 fallback；
- 13 项旧版数据与零执行门禁全部通过，BUY=3、SELL=1、HOLD=3、errors=0。

该次 HK 运行发生在 v1.5 运行保护代码部署之前，因此只保留了日期 JSON/Markdown，
没有事后伪造 manifest 或不可覆盖 run archive，也没有为补证而再次调用 Futu。新的
exactly-once、manifest 和 run archive 契约从后续正式调度槽开始生效。US 首次真实
计划运行仍等待 2026-08-04 北京时间 10:00。

以下为 v1.5 运行保护阶段的提示词快照，已由下一节 v1.6 决策审计版本取代；当时
automation ID、RRULE、工作目录与推送开关均未改变：

| 任务 | 提示词更新时间（北京时间） | 提示词 SHA256 | 唯一命令 | 跨市场模块 |
|---|---|---|---:|---:|
| HK | 18:18:06 | `43315a9a6ef5c15694b89536599ddb0cc21ec2104e32f2a161b4418b518e6105` | 1 | 0 |
| US | 18:19:56 | `8e2232177f2e2d9b8e11fee84ae4418c6b77daa3cf2f145314a3d502de06a435` | 1 | 0 |

两条唯一命令均不含动态市场参数。提示词明确禁止同一次 WorkBuddy 唤醒发起第二个
进程、Windows/POSIX 路径兜底重跑、越过 duplicate/corrupt/retry 门禁，以及读取或
编辑 WorkBuddy 共享/私有记忆。数据库中其他自动化的最近更新时间不晚于 16:38:32，
早于本次 18:18 至 18:19 的两次定向更新。

本地回归为 `26 passed`，Python 编译检查通过，合并市场入口实测退出码为 2。验证
只使用临时输出目录，没有调用生产 Futu，也没有提前占用 HK/US 的下一正式调度槽。

## 十、2026-08-03 v1.6 决策审计升级

本轮只增强信号可解释性和报告完整性，不修改因子算法、动作条件、BUY/SELL/HOLD、
目标比例、置信度规则、标的池、Futu 数据源或零交易权限。模型信号 schema 升为
`2.1`，运行报告 schema 升为 `2.2`：

- 趋势层展示市场状态与当次趋势事件；
- 结构层展示底部/顶部阶段、持续天数、修边方向、比例与置信度变化；
- 序列层展示计数、形成阶段、方向、与主动作关系及置信度变化；
- 决策层同时展示候选动作和数据门控后的实际输出动作；
- 仅输出状态与影响，明确禁止输出公式、参数名、周期、阈值或原始内部指标值。

WorkBuddy HK/US 提示词均追加第 7 节报告质量核验。数据库只读回查结果：

| 任务 | 更新时间（北京时间） | 提示词 SHA256 | 唯一命令 | 跨市场模块 | RRULE |
|---|---|---|---:|---:|---|
| HK | 19:01:53 | `1B91E875AC2F3113DD546E9E060BACE0CF4B88C9C2A890CB23587433BEEF8FE1` | 1 | 0 | 未改变 |
| US | 19:02:27 | `B99A422CB8B33E3BD085B3473D947BECE7DC11D9B76CFC544773CC8C767FD3A2` | 1 | 0 | 未改变 |

两任务名称、`ACTIVE` 状态、`cwd=["E:\\quant"]`、automation ID 与调度均保持原值；
其他 12 个自动化的最近更新时间为 16:38:32，早于本轮两个定向更新。最终模型测试
为 `28 passed`。验证只使用合成数据和临时目录，
没有调用生产 Futu、没有重跑正式报告，也没有占用下一调度槽。

## 十一、2026-08-03 v1.7 最终交付表格修正

19:14 的 HK 手工诊断证明模型产物本身已经包含完整“结构信号”和“序列信号”两表，
但 WorkBuddy 最终回复只报告审计通过并给出普通信号汇总，遗漏了用户要求的逐标的
结构与序列结果。该缺失发生在 WorkBuddy 二次摘要层，不是模型计算层或 Markdown
生成层。

HK/US 两个原 automation 已定向追加第 8 节最终交付契约：

- 最终回复必须从模型生成的 Markdown/JSON 原样复制全部标的的结构审计与序列审计；
- 禁止重算、聚合、省略、改写为笼统说明或替换成普通信号表；
- 信号日未变化时仍须以 `UNCHANGED_SIGNAL_DATE` 展示完整两表；
- 任一完整表缺失时必须返回 `REPORT_PRESENTATION_INCOMPLETE`；
- 禁止读取、创建或更新 automation memory，权威证据仅限自动化数据库运行记录、
  immutable archive、manifest、JSON 和 Markdown。

数据库只读回查结果：

| 任务 | 更新时间（北京时间） | 提示词 SHA256 | 第 7 节 | 第 8 节 | 唯一命令 | 跨市场模块 | RRULE |
|---|---|---|---:|---:|---:|---:|---|
| HK | 19:23:28 | `C9419C75E6F643E5160D1D3FBF77C380258AD3C9CB715BF22D3511CDC2C0CFBC` | 1 | 1 | 1 | 0 | 未改变 |
| US | 19:22:26 | `71D9D7B610244B9EF2A30E617E989B890386ACCBCDEF191B1B4468147A383264` | 1 | 1 | 1 | 0 | 未改变 |

两任务仍为 `ACTIVE`，automation ID、名称、`cwd=["E:\\quant"]`、推送开关和调度均
保持原值。此次只修正交付层，没有修改 Northstar-D1 因子、动作、比例、置信度、
标的池、Futu 数据源、市场隔离或零交易权限，也没有再次调用 Futu。

## 十二、2026-08-04 v1.8 WorkBuddy 运行归因

平台只读核对确认：当前 WorkBuddy automation 配置没有按任务注入进程环境变量的
字段。因此不能把配置中的 automation ID 冒充为本次调用证据，也不能为了补 provenance
包装、重试或再次执行 Northstar 市场命令。

本轮加入 `workbuddy_provenance.py`，并通过 WorkBuddy 正式 `automation_update` 接口定向
更新 HK/US 两个原任务。每条任务在唯一市场命令完成后只允许执行一次只读回查；回查
同时满足以下条件时才返回 `VERIFIED`：

- automation ID 位于固定 allowlist，任务为 `ACTIVE`；
- `cwd`、市场命令与 HK/US 部署契约一致；
- 当前 runtime state 明确处于 running，且启动记录足够新；
- 唯一 automation run 的启动时间、工作目录和 conversation ID 与 runtime state 一致。

任一条件不满足均返回 `NOT_PROVIDED`，不会猜测。该回查不调用 Futu、不运行模型、
不写 WorkBuddy，也不会触发补跑。模型报告内的进程级 provenance 与 WorkBuddy 调度器
外部证据必须分栏：`Northstar Archive Run ID` 是模型不可变产物 ID，
`WorkBuddy Automation Run ID` 是平台 `automation_runs.thread_id`，两者不得互换。

配置回读结果：

| 任务 | Automation ID | 提示词 SHA256 | 市场命令 | 只读回查 | 状态/目录/调度 | 即时运行 |
|---|---|---|---:|---:|---|---|
| HK | `automation-1785736457372` | `C5B8F8208EA05C5D522DAD85D0A082CAF5CC60D96AC26F6BAF3D10D5A7FB43E7` | 1 | 1 | 未改变 | 未触发 |
| US | `automation-1785736457815` | `AA733E9173D5DAE10F9405B5FD5076C2C17DC67CECEDFB70B92BDAA2382DC40F` | 1 | 1 | 未改变 | 未触发 |

其他自动化未发生变化。合成数据库测试与相关报告回归为 `52 passed`；非运行窗口对两个
真实任务的回查均正确返回 `AUTOMATION_NOT_CURRENTLY_RUNNING / NOT_PROVIDED`。只有下一次
自然调度产生现场 `VERIFIED` 证据并与平台记录回读一致后，才可把外部 provenance 状态
从 `NATURAL_RUN_READBACK_PENDING` 升级为闭环。

## 十三、2026-08-05 v1.9 运行时与进程归因修复

8 月 4 日 HK 运行的追加事件账本证明模型和产物先成功，但 Windows 沙盒不支持原锁
清理方式，16 ms 后又记录失败；WorkBuddy 随后手工改写/删除 runtime 状态并提前重跑，
造成成功、失败和调度归因互相矛盾。v1.9 完成以下修复：

- `RunCoordinator` 在读取状态、创建锁或调用 Futu 前执行自身硬时间闸；过早调用只追加
  `SKIPPED_EARLY`，不创建 `latest.json` 或 `active.lock`；
- 活动锁不再依赖回收站删除，而是校验所有权后原子移动到 `released_locks/`；只有锁
  成功释放后才追加 `RUN_SUCCEEDED`，失败路径保留原始异常且不被清理异常覆盖；
- HK A01 的不可变 JSON/Markdown 与 manifest 哈希保持一致，`latest.json` 已依据追加
  账本恢复为最后的 `FAILED`，活动锁不存在，因此自然调度会进入唯一允许的 A02；
- 新增 HK/US 专用 WorkBuddy 入口。入口只在 automation、runtime、run record、cwd、
  conversation、market/deployment 全部一致且 `runKind=scheduled` 时注入 Automation ID、
  Automation Run ID、Conversation ID 与 runKind；否则在 Futu/模型前失败关闭；
- HK/US 提示词均升级为 runtime/provenance v1.9，使用 Windows `zoneinfo` 时间命令，禁止
  `TZ=... date`、等待跨闸、重复调用和任何 WorkBuddy runtime/不可变存档改写。

配置只读回读：

| 任务 | Automation ID | 提示词 SHA256 | 唯一受控入口 | 状态/目录/RRULE/推送 |
|---|---|---|---:|---|
| HK | `automation-1785736457372` | `C1DEBDA40BB4D2D3B4EDE29B17A25308439D466B14D73CB7CA4E5E450C0C6DB4` | `workbuddy_hk` 1 次 | 未改变 |
| US | `automation-1785736457815` | `A8270C9A43FC13830B311235559F556152BFB404B463A6ABA42475345F834A3B` | `workbuddy_us` 1 次 | 未改变 |

本地编译与合成回归为 `41 passed`。真实任务的非运行窗口负向校验均返回退出码 4、
`AUTOMATION_NOT_CURRENTLY_RUNNING`、`futu_called=false`、`model_called=false`。没有手工
调用 Futu 或模型。v1.9 的最终闭环仍以 HK 2026-08-05 及 US 2026-08-06 的下一次自然
调度现场证据为准，不以手工运行代替。

## 十四、2026-08-06 v1.9.1 missed recovery 白名单修复

US 现场将两条路径区分清楚：10:37:58 的记录是 `manual_test`，此路径不会建立可供
Northstar 信任的 active runtime，入口正确返回 `AUTOMATION_NOT_CURRENTLY_RUNNING`；
10:49:32 的记录是 WorkBuddy 调度器自动创建的 `missed` recovery，`automation_runs`、
runtime state、conversation ID、cwd 和 automation identity 均一致，provenance 已返回
`VERIFIED`。旧 v1.9 入口随后仅因 `runKind != scheduled` 返回
`WORKBUDDY_RUN_KIND_NOT_SCHEDULED`，证明故障不在数据库维护，而在 run-kind 白名单。

v1.9.1 将允许集合收紧为 `scheduled / missed` 两种自然调度来源；missed recovery 还必须
提供与当前运行相同调度本地日期的 `missedScheduledAt`。`manual_test`、跨日 stale
recovery 与其他类型继续在 Futu/模型前失败关闭。修复没有编辑 WorkBuddy 数据库、US
runtime 或已有不可变产物，也没有重试 2026-08-06 的失败运行。新增
scheduled/missed/manual/stale 四路径回归后，Northstar 测试为 `44 passed`；最终闭环仍
等待补丁后的下一次自然运行证明。

## 十五、2026-08-06 v1.9.2 用户按需触发

用户要求在 WorkBuddy automation 中随时发起报告，而不是绕过 provenance 直接调用
`research.northstar_d1.us`。v1.9.2 保留 10:00 北京时间硬闸、每个用户触发 turn 一次调用、
RESEARCH_ONLY 与零执行边界，同时增加两条受信任身份路径：Run now 产生的
`manual_test`，以及既有后台 automation session 中的新 follow-up turn。允许的有效
run kind 为 `scheduled / missed / manual_test / on_demand_followup`。

US automation 提示词已通过 WorkBuddy UI 更新并只读回查，SHA256 为
`0E560F071D67EBAEE8F26682BFA7E8CF9026B79A7F8D288599620DE7B7A96A2C`；状态、`E:\quant`
工作目录、RRULE 与唯一 `workbuddy_us` 命令均未改变。11:28 的唯一一次真实 Run now 创建
`run-1785986912080-6` 和 conversation `81f09bb2-4897-47be-aed8-37abcca885b0`，但暴露出
WorkBuddy 在运行结束前不填充 `runs_json` 的生命周期差异，因此初次实测仍安全失败关闭，
Futu、模型与 Northstar 产物均为零。

最终修复在 `runs_json` 尚为空时，将唯一、足够新的 `IN_PROGRESS manual_test` 与同工作区、
创建时间相差不超过 2 秒的唯一 active background session 配对；存在多个候选时仍失败关闭。
使用该真实 run/session 的时间与 identity 重放后，结果为
`VERIFIED / ACTIVE_MANUAL_RUN / CREATED_AT_PAIR`。全套测试为 `51 passed`，编译通过。依据
“同一用户触发 turn 不重试”的契约，本轮没有第二次真实触发；现场成功报告留给下一次用户
按需请求闭环。

## 十六、2026-08-07 WorkBuddy 5.3.8 Run now 现场闭环

进一步只读审计 WorkBuddy 5.3.8 的 `main/initialize.js` 后确认，Run now 调用
`startAutomationRun(automation, { persistRuntimeState: false })`，而原实现仅在
`aggregate.persistRuntimeState` 为真时执行 `persistInProgressRun`。因此旧版本在子会话启动前
根本没有可见的 `IN_PROGRESS manual_test`；此前提出的“WAL 未提交快照”不是准确根因。把任意
active session 配到最近 run 的宽松 fallback 既不能看见未创建的当前记录，又会误归因，已删除；
`AUTOMATION_NOT_CURRENTLY_RUNNING` 负向测试恢复为严格失败关闭。

本机安装了版本锁定的兼容补丁：只把 `persistInProgressRun` 移到 session 分派之前，仍保留
`persistRuntimeState=false`，所以 Run now 不会更新自然调度的 runtime/last-run 语义。补丁脚本
只接受 WorkBuddy 5.3.8 原包 SHA256
`75B9E8559217CE4E0AD2526550A17365231A8AB4EEB86DDFA7C76EC6C4297A00`，安装包 SHA256 为
`53B5C5625210411FB62CD261177CF930A5C11A638BA36EA920D34F33A9B8D02C`；ASAR 文件哈希与分块
哈希均已重算验证，原包另存备份。未知版本会被脚本拒绝。

12:08 的唯一真实 Run now 生成 `run-1786075729525-1`；IN_PROGRESS 记录时间为
`1786075729525`，conversation `09d4c25c-b422-4388-affc-7471718a02d8` 创建时间为
`1786075729584`，相差 59 ms。受控入口现场返回
`VERIFIED / ACTIVE_MANUAL_RUN / CREATED_AT_PAIR / manual_test`，WorkBuddy 最终记录为
`PENDING_REVIEW / result_success=1 / resultEvidence=artifact`。本次同槽已有成功报告，因此结果
为 `DUPLICATE_SUPPRESSED / RUN_PASS`，交付既有不可变产物且没有二次 Futu/模型调用。
Northstar 全套测试为 `51 passed`。

## 综述结论

Northstar-D1 的生产形态是“一个私有模型核心、两个独立研究部署、Futu 单一实时
数据入口、已完成日线决策、失败关闭、零交易权限”。WorkBuddy 只负责分别调度 HK
和 US 日报，通过受控入口注入已核验的自然调度或用户按需运行身份，并回读数据、产物与调度记录。
任何合并市场、fallback、因子外泄、runtime 改写或交易执行都属于越界。
