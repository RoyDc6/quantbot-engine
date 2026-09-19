# Northstar-D1 Futu 模拟前向执行

该目录是冻结 Northstar-D1 模型之外的独立执行层，只允许 Futu `SIMULATE`。
它读取哈希验证通过的最新 `RUN_PASS` 产物，将 BUY 转成模拟账户目标仓位差额，
将 SELL 转成现有可卖持仓的减仓数量。

安全约束：

- 每个 Northstar `run_id` 只消费一次，预留失败也禁止盲目重发；
- HK/US 使用独立 SQLite 订单日志、租约、回执与消费标记；
- 未决订单先对账，存在未决或未知状态时禁止新订单；
- 报价、账户、持仓或源文件哈希不完整时失败关闭；
- 不包含、也不允许 `TrdEnv.REAL`。

入口：

```powershell
python -m research.northstar_d1_futu_sim.runner --market HK
python -m research.northstar_d1_futu_sim.runner --market US --execute-sim
```

整合后的 Windows 任务：

- `NorthstarD1-Forward-HK`：工作日 09:35，依次生成上一 HK 完成日信号、校验精确
  `run_id`、读取开盘报价、换算仓位差额并执行 Futu 模拟限价单；
- `NorthstarD1-Forward-US`：工作日 21:35 与 22:35 各触发一次，纽约本地
  09:35–10:00 门禁只放行与当季夏令时匹配的一次，然后完成同一条端到端链路。
- `NorthstarD1-Forward-HK-Manual` 与 `NorthstarD1-Forward-US-Manual`：不设定时触发器，
  可在 Windows 任务计划程序中随时右键“运行”，用于即时 Futu SIMULATE 验证。
- 旧的 `NorthstarD1-HK/US` 与 `NorthstarD1-FutuSim-HK/US` 已停用但保留配置。

也可以在 PowerShell 中手动触发：

```powershell
Start-ScheduledTask -TaskName 'NorthstarD1-Forward-HK-Manual'
Start-ScheduledTask -TaskName 'NorthstarD1-Forward-US-Manual'
```

手动任务绕过自然执行时段门禁，但仍要求实时报价可用、全部信号快照
`market_live=true`、源文件完整且无未决订单；条件不满足时失败关闭。它只连接 Futu
`SIMULATE`，不启用真实交易。

任务启用 `StartWhenAvailable`，但超过市场本地 10:00 后失败关闭，不追补开盘订单。执行前
必须确认所有信号快照 `market_live=true`，并对账全部历史未决订单；存在未决订单时禁止新增
提交。回执在 `receipts/` 与 `forward_receipts/`，调度日志和订单日志在 `runtime/`。
模拟成交只作为 forward evidence，模型继续保持 `NOT_EVALUATED`。

日报生成器只读上述证据，不连接 Futu。它先锁定当天市场开盘窗口对应的 Forward 回执，
嵌入同一 `run_id` 的完整 Northstar-D1 信号报告，再展示该运行对应的 Futu 模拟执行与
对账。信号运行失败时报告 `FAILED_CLOSED / NOT_EXECUTED`，禁止读取其他运行的历史交易
回执补位：

```powershell
python -m research.northstar_d1_futu_sim.report
```

WorkBuddy 使用两个互相隔离的任务：HK 工作日 09:50，US 在当前夏令时按工作日 21:50。
两个任务分别
运行 `workbuddy_entry --market HK --scheduled-delivery` 与
`workbuddy_entry --market US --scheduled-delivery`，各自只交付一份
Markdown。定时入口使用 `--scheduled-delivery`，只读取已有 Forward，绝不发起第二轮交易；
需要即时重跑时使用 Windows Manual 任务。进入冬令时后，US WorkBuddy 时间需同步调整到
北京时间 22:50。
