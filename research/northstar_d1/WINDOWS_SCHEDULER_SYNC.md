# Northstar-D1 Windows 调度合同

**版本**：`WINDOWS_TASK_SCHEDULER_V1`
**安装日期**：2026-08-19
**当前状态**：`INSTALLED / ENABLED / HK_NATURAL_RUN_ACCEPTED / WORKBUDDY_DELIVERY_LIVE_ACCEPTED / US_NEXT_NATURAL_RUN_PENDING`
**边界**：`RESEARCH_ONLY / NOT_EVALUATED / formal_publish_allowed=false / ZERO_TRADE_PERMISSION`

## 一、调度权威

Northstar-D1 的准点执行和错过补跑由 Windows Task Scheduler 负责。WorkBuddy 不是研究执行、
调度或产物权威；它只在 Windows 运行之后交付已经验证的 Markdown。WorkBuddy 升级、
`app.asar` 哈希变化和 recurring jitter 均不得影响模型运行合同。

| 市场 | Windows 任务 | 计划 | 受控入口 |
|---|---|---|---|
| HK | `\NorthstarD1-HK` | 周一至周五 16:20，香港时间 | `research.northstar_d1.windows_scheduler_hk` |
| US | `\NorthstarD1-US` | 周二至周六 10:00，北京时间 | `research.northstar_d1.windows_scheduler_us` |

两地当前均为 UTC+8。任务使用当前交互用户、`E:\quant` 工作目录和 Windows PowerShell
包装器 `tools/run_windows_scheduled_northstar.ps1`。

## 二、WorkBuddy 只读交付层

| 市场 | WorkBuddy automation | 计划 | 唯一 helper |
|---|---|---|---|
| HK | `automation-1785736457372` | 周一至周五 16:40 | `research.northstar_d1.delivery_hk` |
| US | `automation-1785736457815` | 周二至周六 10:20 | `research.northstar_d1.delivery_us` |

- 两个 automation 均为 `ACTIVE`；它们不再调用 `workbuddy_hk/workbuddy_us`、Windows runner、
  Futu、模型、账户或订单 API。
- helper 最多只读等待 600 秒，只接受最新已到期 Windows 槽、成功 runtime、不可变归档、
  manifest/hash、完整 Markdown、数据/新鲜度/报告质量及零执行边界全部通过。
- 信号总表、结构信号、序列信号、数据新鲜度和 Paper intents 必须同时展示标的代码与公司
  名称；任一标的缺名或任一明细表漏名时返回 `DELIVERY_NOT_READY`。
- 成功时 `present_files` 恰好附加一个归档 Markdown；JSON、manifest、日志和历史 receipt
  不对外交付。失败时关闭，不补跑模型。
- shell 命令使用 Git Bash 与 Windows 均兼容的正斜杠 Python 路径，避免 WorkBuddy 5.3.14
  把反斜杠解析为转义并自行重试。
- WorkBuddy 5.3.14 系统层会自动读写该 automation 的 memory。无补丁方案保留这一平台记账，
  但 memory 不得作为报告路径、运行状态或交付结论的证据。
- 当前 prompt SHA256：HK
  `9ED4ABB90646B9DF8CB3432118DCED53E6457B3B5697E3D92D0FBAF649BBB958`；US
  `8719A1A4EE33F23EF519B47007B11776186045D65D88C9BE0F4AB98E981BD7EF`。

## 三、错过补跑

- `StartWhenAvailable=true`：电脑或任务服务恢复后允许补跑。
- 启动不晚于计划时间 5 分钟时标记 `SCHEDULED`。
- 超过 5 分钟时标记 `MISSED_RECOVERY`；报告必须同时保留原计划时间与实际开始时间。
- 补跑使用最近一个已经到期且符合星期合同的调度槽。
- 当更新槽已经到期时，不再回写更老的槽；同槽已有哈希验证成功产物时返回
  `DUPLICATE_SUPPRESSED`，不调用 Futu 或模型。
- `MultipleInstancesPolicy=IgnoreNew`；Northstar runtime 继续实施
  `EXACTLY_ONCE_PER_SCHEDULE_SLOT`。

## 四、数据与执行边界

- 每次真正运行仍须重新验证 Futu completed-session 数据、新鲜度、市场隔离和报告质量。
- 不访问账户、持仓、余额、订单或成交；`account_access=false`、
  `order_api_called=false`、`orders=[]`、`fills=[]` 保持。
- 不修改模型、因子、阈值、标的池或交易执行链。
- `MISSED_RECOVERY` 是真实补跑，不得重写为准时自然运行。

## 五、安装与验收证据

| 项目 | 结果 |
|---|---|
| WorkBuddy HK | `ACTIVE`，16:40 只读交付，原 automation ID 和历史记录保留 |
| WorkBuddy US | `ACTIVE`，10:20 只读交付，原 automation ID 和历史记录保留 |
| Windows HK | `READY / ENABLED`，XML 计划时点 `16:20:00` |
| Windows US | `READY / ENABLED`，XML 计划时点 `10:00:00` |
| Start when available | 两任务均为 `true` |
| Multiple instances | 两任务均为 `IgnoreNew` |
| US 端到端测试 | `MISSED_RECOVERY → DUPLICATE_SUPPRESSED`，任务退出码 `0x00000000` |
| HK 自然运行 | 2026-08-19 16:20:06 启动，`SCHEDULED`，lag 6.359 秒，`RUN_PASS` |
| HK 交付验收 | `run-1787129880698-1`，helper 1 次、`present_files` 1 次、Markdown 1 份 |
| HK 公司名校正版交付 | `run-1787130778367-1`，四张表及 Paper intents 均含公司名，附件 SHA256 `3FE4509B...BD179D` |
| 自动化测试 | Northstar 全套测试通过；旧 WorkBuddy receipt 的非当前来源用例按合同跳过 1 项 |

安装时任务 XML SHA256：

- HK：`B9FD29833A377F0629BA6F0F5621A1AF519687A7E8B3DDDC5D3149F7414BF45F`
- US：`2ED613D02B514ED3CF32CA0F26B83F687887B962EBED96BA01AA2ADED4FFFE45`

切换前 WorkBuddy SQLite 在线备份：

- 路径：`C:\Users\RoyGoode\.workbuddy\automation-backups\workbuddy_before_northstar_windows_scheduler_20260819T033951Z.db`
- SHA256：`B975B4369CF7FB2378355C0151D096123E691C3A6B71FD232654A4EA776F782F`
- `quick_check=ok`

交付层配置与现场验收前备份：

- 初始交付配置：`C:\Users\RoyGoode\.workbuddy\automation-backups\northstar-delivery-only-20260819T084640607142Z.db`
- 初始配置 SHA256：`1568E276B49E6399F768E9892D5F66EC788B716C1B2F8C5CA339205C6BBAF488`
- 加速验收前：`C:\Users\RoyGoode\.workbuddy\automation-backups\northstar-delivery-acceptance-20260819T085027179591Z.db`
- 加速验收前 SHA256：`A3FD097771B7DD27C72FCA9631BABF3F3E8BDD8796508F4DA2B5608C2B4876E8`
- Git Bash 路径修正前：`C:\Users\RoyGoode\.workbuddy\automation-backups\northstar-delivery-only-20260819T085611329677Z.db`
- 所有备份与当前数据库均通过 SQLite `quick_check`。

## 六、待完成验收

- HK：Windows 16:20 自然运行与 WorkBuddy delivery-only 现场交付均已验收。交付现场使用加速的
  `next_run_at`，不是 16:40 自然槽；正式 RRULE 已自动恢复至下一工作日 16:40。
- US：等待下一次 10:00 自然任务，核验同一组条件。当前只完成注册任务的
  `MISSED_RECOVERY / DUPLICATE_SUPPRESSED` 端到端验收；US WorkBuddy 10:20 交付也要等待
  下一份 Windows 权威产物，不能用旧 WorkBuddy 来源冒充。

## 综述结论

Northstar-D1 已脱离 WorkBuddy 版本补丁依赖。Windows Task Scheduler 负责准点和错过补跑，
Northstar runtime 负责时间硬闸、exactly-once、不可变归档和零交易权限；WorkBuddy 只负责
延后 20 分钟读取、验证并交付唯一 Markdown。HK 已完成 Windows 自然运行和 WorkBuddy
delivery-only 现场验收；US 下一自然槽仍待独立确认。
