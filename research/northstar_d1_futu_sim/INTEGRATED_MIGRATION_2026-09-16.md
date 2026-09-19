# Northstar-D1 信号与 Futu 模拟执行整合回执

- 迁移时间：2026-09-16 15:13 Asia/Shanghai
- 模式：`NORTHSTAR_D1_INTEGRATED_FORWARD`
- 账户范围：Futu `SIMULATE`；`TrdEnv.REAL` 仍禁止
- 结果：四个分离 Windows 任务收敛为两个市场级 Forward 任务

## 当前工作流

每个市场的一次 Forward 调用按固定顺序完成：

1. 检查市场本地时间必须位于 09:35–10:00；
2. 重新读取 Futu OpenD 数据并生成上一完成交易日的 Northstar-D1 信号；
3. 写入不可变研究产物、manifest 和 SHA256；
4. 验证 `RUN_PASS`、`data_ready`、`freshness_ready` 及全部快照 `market_live=true`；
5. 只允许执行器消费本轮精确 `run_id`；
6. 查询模拟账户、持仓与最新报价；
7. 对账全部历史订单；存在未决订单时禁止新提交；
8. 换算仓位差额，提交 Futu DAY 限价模拟单；
9. 回读持仓、保存执行回执并生成当日整合报告。

## Windows 任务

| 任务 | 触发时间（上海） | 市场时段门禁 | 状态 |
|---|---|---|---|
| `NorthstarD1-Forward-HK` | 工作日 09:35 | 香港 09:35–10:00 | Enabled / Ready |
| `NorthstarD1-Forward-US` | 工作日 21:35、22:35 | 纽约 09:35–10:00，只放行与夏令时匹配的一次 | Enabled / Ready |

已禁用并保留定义：

- `NorthstarD1-HK`
- `NorthstarD1-US`
- `NorthstarD1-FutuSim-HK`
- `NorthstarD1-FutuSim-US`

旧任务和新任务 XML 位于：

`E:\quant\research\northstar_d1_futu_sim\migration_backups\20260916T151338`

## WorkBuddy 日报

| 日报 | 时刻 | 状态 |
|---|---|---|
| Northstar-D1 Futu港股模拟交易日报 | 工作日 10:10 | ACTIVE |
| Northstar-D1 Futu美股模拟交易日报 | 工作日 23:00 | ACTIVE |

旧的纯研究报告交付任务 `Northstar-D1 HK 报告交付`、`Northstar-D1 US 报告交付`
已暂停，避免与整合日报重复。WorkBuddy 数据库迁移前备份：

- `C:\Users\RoyGoode\.workbuddy\automation-backups\workbuddy-before-northstar-integrated-20260916T151346.db`
- SHA256：`1080cf649b01cdd8e8acef083b70b5ed5f423bd6f31fc025e70263ecad2f929b`

## 失败关闭与幂等边界

- 错误的美股夏令时触发点只记录 `SKIPPED_EARLY` 或 `MISSED_EXECUTION_WINDOW`，不连接交易执行。
- 电脑延迟启动超过市场本地 10:00 后不补下开盘订单。
- 报告只接受上海日期为当天的执行回执，禁止用历史回执冒充当日运行。
- 同一研究 `run_id` 只能消费一次；执行器还会在每次新提交前对账所有历史未决订单。
- 任何 `SUBMITTED`、`UNKNOWN`、`TIMEOUT` 或对账错误都会阻断新增订单。

迁移时出现的 `AMD.US` BUY 414 股模拟订单（订单号 `9196837`）已按用户要求撤销：

- 富途终态：`CANCELLED_ALL`
- 已成交数量 / 均价：`0 / 0`
- 本地订单日志：`CANCELLED_ALL`
- 撤单回执：`E:\quant\research\northstar_d1_futu_sim\receipts\20260916T152644_us_cancel_9196837.json`
- 回执 SHA256：`9456B6ABCC60ED66037FD860C61B9257F99DA2FAFFD98C123E9DB78DC39626B1`

撤单后未决订单列表为空。`NorthstarD1-Forward-US` 保持启用，由 2026-09-16
21:35（上海时间）自然触发纽约 09:35 的新运行槽；22:35 的备用触发受纽约时段门禁约束。
WorkBuddy 美股整合日报保持 `ACTIVE`，下一次运行时间为当晚 23:00。

## 验证

- Python 编译检查通过。
- PowerShell 两个脚本均为 `SYNTAX_OK`。
- Northstar-D1、整合执行、报告和调度相关测试：`52 passed`。
- 迁移后的 WorkBuddy 数据库 `PRAGMA quick_check=ok`。
- 新任务为 `StartWhenAvailable=true`、`MultipleInstances=IgnoreNew`、最长运行 20 分钟。

## 2026-09-17 日报链路修正

首次 US 自然运行暴露出两个问题：Forward 把调度时钟传入实时数据源，触发
`injected clocks are disabled for live scans` 并失败关闭；旧日报生成器随后错误选取同日
下午的历史执行回执，导致报告缺失完整信号部分并把旧 AMD 撤单记录混入当晚日报。

现已修正：

- 调度时钟只用于时段门禁与运行槽；生产实时数据源使用其权威当前时钟；
- 日报以开盘窗口的 Forward 回执为根证据，并校验同一 `run_id` 的信号 JSON、Markdown、
  manifest 与 SHA256；
- 日报完整嵌入 Northstar-D1 信号报告，然后才展示同一运行的模拟执行与对账；
- 信号失败时显示 `FAILED_CLOSED / NOT_EXECUTED`，禁止历史执行回执补位；
- WorkBuddy HK/US 日报合同升级为 V3，任务保持 `ACTIVE`；
- 相关测试 `47 passed`，其中整合执行与日报专项测试 `15 passed`。

昨晚 US 更正报告：

- `E:\quant\research\northstar_d1_futu_sim\reports\2026-09-16_northstar_d1_futu_sim_us_daily.md`
- SHA256：`DC962A6181FBA701069259A5D2603583596DF43BBF99DA3ADE5CFD9E570CA9B4`

### 2026-09-17 港股修复验收

- 09:35 与 09:42 两次 HK Forward 进程均在修复版本加载前启动，因此仍因
  `injected clocks are disabled for live scans` 失败关闭，未进入 Futu 执行；
- 10:17 使用修复后的生产数据入口完成一次只读全量扫描：7/7 标的成功，
  `RUN_PASS / data_ready=true / freshness_ready=true / errors=0`；
- 只读扫描结果为 SELL 1、HOLD 6，仅验证信号层，未写研究产物、未读取账户、未生成订单；
- 今日执行窗口已在 10:00 关闭，未补跑模拟交易；
- `NorthstarD1-Forward-HK` 下一次为 2026-09-18 09:35，状态 `Ready / Enabled`；
- WorkBuddy HK V3 日报下一次为 2026-09-18 10:10，状态 `ACTIVE`；
- WorkBuddy 数据库备份：
  `C:\Users\RoyGoode\.workbuddy\automation-backups\workbuddy-before-northstar-integrated-20260917T101736.db`；
- 备份 SHA256：`19aea9d2d3d908f470ce4957b069e2f8ae77f32023fbebfc641acd9caf84b717`。

用户要求不等待下一交易日，因此 10:24 追加了一次明确标记、与生产时段门禁隔离的
`USER_AUTHORIZED_MANUAL_VALIDATION`。生产 Windows 任务仍严格限制在 09:35–10:00。

- Run ID：`NORTHSTAR_D1_HK-20260917T1024-A01`
- 信号：`RUN_PASS`，信号日 2026-09-16，SELL 1 / HOLD 6 / errors 0
- Futu：`SIMULATE`，连接、账户、报价、仓位与执行换算均完成
- 执行：`00700.HK` 为 SELL，但模拟账户无可卖持仓，按规则跳过
  `NO_POSITION_DELTA_OR_BELOW_LOT`
- 订单：planned 0 / submitted 0 / unresolved 0
- 对账：`PASS`
- 合并日报：`REPORT_READY`，Forward 状态 `PASS`
- 日报：
  `E:\quant\research\northstar_d1_futu_sim\reports\2026-09-17_northstar_d1_futu_sim_hk_daily.md`
- 日报 SHA256：`38DE9A4544D84373929ABBC633E080986B6E6C94D25ECCB218935AD1FC04B85D`
- Forward 回执：
  `E:\quant\research\northstar_d1_futu_sim\forward_receipts\20260917T102416227786_hk_forward.json`
- 执行回执：
  `E:\quant\research\northstar_d1_futu_sim\receipts\20260917T102416_hk_execute.json`
- 专项测试：`16 passed`

### 默认自动与随时手动触发

- 原自动任务保持启用：HK 工作日 09:35；US 工作日 21:35/22:35，并由纽约时段门禁
  只放行正确的一次；
- 新增 `NorthstarD1-Forward-HK-Manual` 与 `NorthstarD1-Forward-US-Manual`，两者均
  为 Enabled、无定时触发器，只在用户右键“运行”或调用 `Start-ScheduledTask` 时启动；
- 手动任务调用 `--manual-validation`，只允许 Futu `SIMULATE`，仍保留实时行情、哈希、
  未决订单与对账门禁；
- WorkBuddy HK/US 合同升级为 V4：定时或手动触发均调用 `workbuddy_entry`；当天已有
  PASS/ATTENTION_REQUIRED Forward 时只复用，缺失或失败时才执行一次手动验证恢复。
- 手动任务安装回执目录：
  `E:\quant\research\northstar_d1_futu_sim\migration_backups\20260917T103205`；
- WorkBuddy V4 迁移前数据库备份：
  `C:\Users\RoyGoode\.workbuddy\automation-backups\workbuddy-before-northstar-integrated-20260917T103216.db`，
  SHA256 `1ad2f1b378ff49d81cc4d24665f9b2dfe60db64baa38b78fdbd9ea4a034a4cc7`；
- 新入口即时复用测试返回 `CURRENT_FORWARD_REUSED / PASS`，HK Forward 回执数量保持
  `6 -> 6`，未重复执行；专项测试 `18 passed`。

### 2026-09-16 美股日报补全

2026-09-17 10:39（上海）通过 WorkBuddy V4 使用的同一入口触发 US
`MANUAL_VALIDATION_RECOVERY`。对应纽约市场时间为 2026-09-16 22:39，恢复运行完整扫描
15 个标的，修复前的 injected-clock 错误未再出现。

- Run ID：`NORTHSTAR_D1_US-20260916T2239-A01`
- 信号日：2026-09-16；`RUN_PASS / data_ready=true / freshness_ready=true / errors=0`
- 信号：BUY 2（INTC、NVDA）/ SELL 1（NFLX）/ HOLD 12
- Forward：`FAILED_CLOSED`；执行对账：`NOT_EXECUTED`
- 原因：触发时纽约已结束盘后交易，15 个标的均为 `AFTER_HOURS_END`；未生成新的执行回执，
  未读取或展示 2026-09-16 下午的历史 AMD 执行回执
- 报告日期改为取 Forward 的市场本地日期，因此恢复运行归档至 2026-09-16，而非上海恢复日
- 补全日报：
  `E:\quant\research\northstar_d1_futu_sim\reports\2026-09-16_northstar_d1_futu_sim_us_daily.md`
- 日报 SHA256：`2D774747A117AD1FEDB2F4587C592B5F02D85BB968CE711B8D220B8974B18924`
- Forward 回执：
  `E:\quant\research\northstar_d1_futu_sim\forward_receipts\20260917T103934526035_us_forward.json`
- 被替换报告已备份至：
  `E:\quant\research\northstar_d1_futu_sim\migration_backups\20260917T104123\2026-09-16_northstar_d1_futu_sim_us_daily.before.md`
- 完整信号嵌入、同一 run_id、JSON/Markdown 哈希、旧执行回执隔离均验证通过；专项测试
  `19 passed`

### WorkBuddy 提前交付（夏令时）

- HK 从北京时间 10:10 提前到 09:50；
- US 从北京时间 23:00 提前到 21:50，适用于当前美股夏令时；进入冬令时后调整到 22:50；
- 两个交付时间均位于 09:35 Forward 后 15 分钟；
- WorkBuddy 合同升级为 V5，定时入口增加 `--scheduled-delivery`，只读取已有 Forward，
  Forward 延迟或缺失时不会另起一轮 Northstar/Futu 模拟交易；
- 用户即时触发继续使用 `NorthstarD1-Forward-HK-Manual` 与
  `NorthstarD1-Forward-US-Manual`。
- 下一次 HK 日报：2026-09-18 09:50；下一次 US 日报：2026-09-17 21:50；两者均为
  `ACTIVE`；
- V5 迁移前数据库备份：
  `C:\Users\RoyGoode\.workbuddy\automation-backups\workbuddy-before-northstar-integrated-20260917T104850.db`，
  SHA256 `1eda5acd41f27fcd393b276f6b9752c8a19cdf0e6567a7336a7af89473b09fdd`；
- 定时入口实测 HK/US Forward 回执数量分别保持 `6 -> 6` 与 `3 -> 3`，确认不会触发
  第二轮模拟交易；WorkBuddy 数据库 `quick_check=ok`；专项测试 `20 passed`。
