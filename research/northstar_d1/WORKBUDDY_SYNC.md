# Northstar-D1 → WorkBuddy 同步文档

> **2026-08-19 状态变更**：本文已被
> [`WINDOWS_SCHEDULER_SYNC.md`](WINDOWS_SCHEDULER_SYNC.md) 取代。两个 WorkBuddy
> Northstar 自动化已改为 `ACTIVE` 的 delivery-only 任务；WorkBuddy 不再负责 HK/US 模型
> 运行或补跑，只在 Windows 权威产物生成后只读验证并交付唯一 Markdown。下文是旧
> WorkBuddy runner 合同的历史快照，不是当前执行合同。

**版本**：1.9.3 + WorkBuddy 5.3.13 restricted patch + zero-jitter v1 + zh-CN/company-name delivery v1
**同步日期**：2026-08-17
**接收方**：WorkBuddy / WK
**当前状态**：`DELIVERY_GUARDS_LIVE_ACCEPTED / ZH_CN_COMPANY_NAME_DELIVERY_INSTALLED / SCHEDULER_ZERO_JITTER_INSTALLED / HK_NATURAL_RUN_ACCEPTED / US_NEXT_NATURAL_RUN_ACCEPTANCE_PENDING`
**边界**：`RESEARCH_ONLY / NOT_EVALUATED / formal_publish_allowed=false / ZERO_TRADE_PERMISSION`

> 本文是 WorkBuddy 当前执行契约。WorkBuddy 只负责自然调度或用户按需触发、单次调用、只读核验和
> Markdown 交付；源码、`.runtime` 修复与不可变存档恢复由 Codex 负责。

## 一、必须先理解的结论

1. 2026-08-04 HK 的全 HOLD、confidence=0 是模型在无新趋势/结构事件时的合法结果，
   不能仅凭这一点判定模型未运行。
2. 当次模型确实完成并写出有效产物；随后旧锁清理依赖 Windows 沙盒不支持的回收站
   机制，导致 `RUN_SUCCEEDED` 后 16 ms 又出现 `RUN_FAILED`。
3. WorkBuddy 随后手工改写/删除 runtime 状态并提前重跑，进一步造成 A01 存档冲突。
   此类“修复”以后禁止执行。
4. 运行时、HK/US 受控入口和两条 WorkBuddy 提示词均已升级到 v1.9.3；提示词带有
   `WORKBUDDY_NO_MEMORY_V1 / WORKBUDDY_MARKDOWN_ONLY_V1 / WORKBUDDY_RECEIPT_V1 /
   WORKBUDDY_ZH_CN_DELIVERY_V1 / WORKBUDDY_COMPANY_NAME_V1` 标记。
5. WorkBuddy 5.3.13 的定向补丁在后置 automation system reminder 中识别这些标记，明确覆盖
   通用 memory 提醒；同时把数据库回执上限由 4,000 提升至 20,000 字符，并恢复 Run now 的
   `IN_PROGRESS manual_test` 先写标记。完整交付由程序化 Markdown receipt 承担。
6. restricted delivery 补丁、数据库合同与静态/单元测试已经通过，并由 2026-08-13 HK、
   2026-08-14 US 的真实 scheduled run 验收：`memory_call_count=0`、`present_files_count=1`，
   且附件只有完整 receipt Markdown。
7. 2026-08-14 确认 WorkBuddy 会对 recurring automation 应用确定性 jitter；旧算法把
   Northstar HK 固定提前 5 分钟、US 固定提前 10 分钟。zero-jitter v1 只对白名单中的两个
   Northstar Automation ID 返回原 RRULE 时间，其他 automation 的 jitter 行为保持不变。

## 二、唯一正式命令

工作目录固定为 `E:\quant`。

### HK

```powershell
C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe -m research.northstar_d1.workbuddy_hk
```

- Automation ID：`automation-1785736457372`
- WorkBuddy RRULE：周一至周五 16:20
- 平台下一唤醒：2026-08-18 16:20 HKT
- Northstar 有效时间闸：16:20 HKT

### US

```powershell
C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe -m research.northstar_d1.workbuddy_us
```

- Automation ID：`automation-1785736457815`
- WorkBuddy RRULE：周二至周六 10:00（北京时间）
- 平台下一唤醒：2026-08-18 10:00（北京时间）
- Northstar 有效时间闸：10:00（北京时间）

不得把正式命令改回 `research.northstar_d1.hk` 或
`research.northstar_d1.us`。这两个直接入口只保留给本地诊断，不能提供严格的
WorkBuddy 进程归因。

## 三、受控入口的失败关闭条件

受控入口会在任何 Futu/模型调用前只读核验 WorkBuddy 数据库。公共证据必须同时成立：

- automation ID 位于固定 allowlist；
- automation 为 `ACTIVE`，没有被删除；
- 工作目录严格为 `E:\quant`；
- 提示词中只有对应市场的受控入口；
- conversation ID、工作目录、market 和 deployment ID 全部一致；
- 下列三种身份模式恰好命中一种：
  1. `ACTIVE_RUNTIME`：scheduler runtime 为 active，且唯一 run record 与 runtime 启动时间一致；
  2. `ACTIVE_MANUAL_RUN`：唯一、足够新的 `IN_PROGRESS manual_test` 与活跃后台 session 匹配；
     运行中 `runs_json` 为空时，只接受同工作区、创建时间相差不超过 2 秒的唯一配对；
  3. `ACTIVE_AUTOMATION_SESSION`：活跃后台 follow-up session 由本 automation 的既有 run
     唯一链接，输出的有效 run kind 为 `on_demand_followup`；
- `runKind` 为 `scheduled / missed / manual_test / on_demand_followup` 之一。

任一条件不成立时，入口必须返回退出码 4，并显示：

```text
status=FAILED_CLOSED
futu_called=false
model_called=false
```

非运行窗口或证据不唯一均不得调用 Futu、模型或写入 Northstar runtime。
`missed` 只有在 automation、runtime、唯一 run record、conversation、cwd 与部署身份全部
通过同一套只读校验，且 `missedScheduledAt` 与当前运行属于同一调度本地日期时才允许
继续；不得靠手工编辑数据库把测试运行伪装成 recovery。

## 四、时间读取规则

HK 只允许使用：

```powershell
C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe -c "from datetime import datetime; from zoneinfo import ZoneInfo; print(datetime.now(ZoneInfo('Asia/Hong_Kong')).isoformat())"
```

US 只允许使用：

```powershell
C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe -c "from datetime import datetime; from zoneinfo import ZoneInfo; print(datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())"
```

禁止使用 `TZ=... date`、`date -u`、GNU/WSL `date`，也禁止把 UTC 或本机时间重新贴上
HKT/北京时间标签。早于硬闸时立即返回 `SKIPPED_EARLY`；不得等待、sleep、调用 Futu、
运行模型或在同一次 WorkBuddy 任务中重试。

## 五、WorkBuddy 永久禁止事项

- 不得编辑、删除、移动或重建：
  - `.runtime/latest.json`
  - `.runtime/active.lock`
  - `.runtime/events.jsonl`
  - `.runtime/released_locks/`
  - `runs/<run_id>/`
- 不得把已有 `FAILED` 手工覆盖为 `SUCCESS`。
- 不得清空状态后重新生成同一个 A01。
- 不得在同一次 automation run 中执行第二个 Northstar 市场命令。
- 不得为补 provenance、补表格或修路径而重跑模型。
- 不得修改源码、因子、阈值、标的池、Futu 适配器、调度或其他 automation。
- 不得访问账户、持仓、余额、购买力、订单、成交或任何执行 API。
- 不得把 `paper_intents` 发送给执行器。

发生 runtime 异常时，只记录 stdout、stderr、退出码、automation run ID 和证据路径，
然后交给 Codex 修复。

## 六、当前 HK runtime 基线

| 项目 | 当前值 |
|---|---|
| Slot | `HK:2026-08-12:16:20:Asia/Hong_Kong` |
| Latest status | `SUCCESS` |
| Latest run ID | `NORTHSTAR_D1_HK-20260812T1620-A01` |
| Attempt | `1` |
| 最后一条事件 | `RUN_SUCCEEDED` |
| 该运行事件数 | `2` |
| Active lock | `false` |
| A01 JSON hash | `CC8EF2B701A76158720622E54CAB9C19C015287EFC3D26E41D2A57CDA869BC8C` |
| A01 Markdown hash | `2D22B8AA65886C11E533FE0D5000E9A036BE87792AD7CD35EAC7A2C5FEC92434` |
| Hash vs manifest | JSON/Markdown 均匹配 |

A01 产物必须保留。下一交易槽由 runtime 正常生成新的日期/attempt；不得删除旧归档或复用身份。

## 七、运行成功验收

一次运行只有同时满足以下条件，才可报告 `SUCCESS`：

### 1. WorkBuddy 调度证据

- `runKind` 为 `scheduled / missed / manual_test / on_demand_followup` 之一，且 provenance 为 `VERIFIED`；
- Automation ID 与目标任务一致；
- Automation Run ID 与本次 `automation_runs.thread_id` 一致；
- Conversation ID 与对应 identity mode 的 runtime/run/session 证据一致；
- 报告内 `invocation_source=WORKBUDDY_AUTOMATION`；
- 报告内 Automation ID、Run ID、Conversation ID、Run Kind 均不是
  `NOT_PROVIDED`。

### 2. Northstar runtime 证据

- 事件顺序为 `RUN_STARTED → RUN_SUCCEEDED`；
- `RUN_SUCCEEDED` 后不得再出现同一运行的 `RUN_FAILED`；
- `latest.json.status == SUCCESS`；
- `active.lock` 不存在；
- `lock_release.status == RELEASED`；
- 锁的归档路径位于 `.runtime/released_locks/`；
- archive 目录唯一且未覆盖；
- archive JSON/Markdown 的 SHA256 与 manifest 一致。

### 3. 数据与报告门禁

- `run_verdict == RUN_PASS`；
- `data_ready == true`；
- `freshness_ready == true`；
- `summary.errors == 0` 且 `errors == []`；
- 13 项运行门禁全部为 `true`；
- `report_quality_ready == true`，全部报告质量检查为 `true`；
- 每个标的均为 `FUTU_OPEND_LIVE`、`cache_used=false`、
  `fallback_used=false`；
- HK/US 市场隔离正确，没有动态 `--market` 或跨市场标的；
- `execution.enabled=false`、`account_access=false`、`orders=[]`、`fills=[]`；
- `validation_status=NOT_EVALUATED` 与 `formal_publish_allowed=false` 保持不变。

### 4. 最终 Markdown 交付

- 对外只交付 Markdown；JSON 和 manifest 作为内部核验证据；
- 完整列出全部标的的“结构信号”表；
- 完整列出全部标的的“序列信号”表；
- 不得只写“审计通过”或只给聚合汇总；
- 信号日未变化时标记 `UNCHANGED_SIGNAL_DATE`，仍完整展示两张表；
- 任一表缺失时必须标记 `REPORT_PRESENTATION_INCOMPLETE`；
- 不披露内部公式、参数名、阈值、周期、原始指标值或 reason codes。

## 八、运行后确定性回执

市场命令成功完成一次后，不再单独调用 provenance helper；receipt helper 内部完成只读身份核验，
并只在受保护 runtime/archive 之外新建一个非覆盖 Markdown：

```powershell
# HK fresh success
C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe -m research.northstar_d1.workbuddy_receipt --automation-id automation-1785736457372 --market HK --runner-status SUCCESS --runner-exit-code 0

# US fresh success
C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe -m research.northstar_d1.workbuddy_receipt --automation-id automation-1785736457815 --market US --runner-status SUCCESS --runner-exit-code 0
```

若市场入口返回 `DUPLICATE_SUPPRESSED`，只把 `--runner-status` 改成该值。helper 不调用 Futu、
模型、账户或订单 API，也不写 runtime/archive。失败时不得补跑或猜测 Automation Run ID。

## 九、当前配置证据

| 任务 | 状态 | Prompt 版本 | Prompt SHA256 | 唯一入口 | 回归 |
|---|---|---|---|---|---:|
| HK | `ACTIVE` | restricted receipt v1.9.3 + zh-CN/company-name v1 | `DDB3F7780B99767A13DD3B3697DFE3482548B20B5D1FE75BD57BCB5CBB26D31C` | `workbuddy_hk` | 2026-08-17 自然槽 16:20、实际 16:21:11 启动并通过；公司名模板静态通过 |
| US | `ACTIVE` | restricted receipt v1.9.3 + zh-CN/company-name v1 | `50BADAE0A937AA063F33036EF688F5A890965F77BDC794A86CF26BF77A421186` | `workbuddy_us` | delivery 现场通过；公司名模板/zero-jitter 静态通过，下一自然槽待验收 |

2026-08-13 对 WorkBuddy 复核确认：automation system reminder 会在用户 prompt 之后
强制读写 automation memory，而 `automation_runs.runs_json[].output` 又固定截断到 4,000 字符。
定向补丁只对带 Northstar marker 的任务启用 restricted reminder，同时将通用数据库输出上限改为
20,000，并恢复 manual-test 的预先 `IN_PROGRESS` 标记。2026-08-14 的 zero-jitter v1 进一步只对
HK/US 两个 Northstar Automation ID 禁用调度 jitter；其他 automation 的 memory 与 jitter 语义不变。

安装证据：WorkBuddy 5.3.13 原 ASAR SHA256
`6A8BEDA11AB3482985B880BA81D8AAF80F48598DBA87A4C088832A06C96FD2E3`，当前 zero-jitter 补丁 ASAR SHA256
`7585A4BC726977B9D720519F7A2F72E2B8E2F27B98DFD53B909ADB970704884E`；补丁后的
`main/initialize.js` SHA256 为
`46461E813E9421C125B45DBDC8A1112C806EF1FAFA0ECFFF2D9EB60C60782B18`，
`main/workbuddy-auth-product-coordinator.js` SHA256 为
`9DAABD2F1498E095086E8EFD8646C66E9C4228AEFD97D6DC9D6492D6ADB6B625`。ASAR 文件哈希、
分块完整性与 JavaScript 语法均已验证，原包保留为同目录备份。WorkBuddy 升级会覆盖补丁，升级后
必须重新审计，禁止跨版本盲装。

## 十、WorkBuddy 回执格式

receipt Markdown 必须按以下顺序完整记录：

所有人类可读标题、字段名、状态说明和枚举说明必须以中文展示；为保证机器审计稳定，
`Automation ID`、路径、SHA256 与 `SUCCESS / RUN_PASS / BUY / SELL / HOLD` 等原始技术值
保留在反引号或中文说明后的括号中。不得再交付以英文表头为主的 receipt。
所有逐标的表必须同时展示“标的代码”和“公司名称”；公司名称缺失时必须失败关闭，
不得只交付代码或临时猜测名称。

1. `SUCCESS / FAILED_CLOSED / SKIPPED_EARLY / DUPLICATE_SUPPRESSED`；
2. Automation ID、Automation Run ID、Conversation ID、Run Kind、Origin Run Kind、Identity Mode；
3. Northstar Archive Run ID、slot、attempt、计划时间、实际开始/结束、退出码；
4. signal_asof、BUY/SELL/HOLD、errors、数据与报告门禁；
5. canonical 与 archive Markdown/JSON/manifest 路径及 SHA256；
6. 完整“结构信号”表；
7. 完整“序列信号”表；
8. `RESEARCH_ONLY / NOT_EVALUATED / formal_publish_allowed=false / ZERO_EXECUTION`；
9. 若失败，只报告原始错误和证据，不改状态、不补跑。

WorkBuddy 的普通 assistant response 必须少于 2,000 字符，只保留状态、两类 run ID、receipt
路径/hash 和四项边界标签；除不可翻译的技术标识外，说明文字必须使用中文。`present_files` 只能
调用一次且只附 receipt Markdown；JSON 和 manifest
始终是内部证据。

## 综述结论

WorkBuddy 不负责修复 Northstar runtime。它在自然调度或明确的用户按需触发中，只调用一次对应
受控入口，再生成并交付一个程序化 Markdown receipt。v1.9.3 的 memory reminder 冲突隔离、Markdown-only、
20,000 字符数据库回执已由真实 HK/US scheduled run 验收。zero-jitter v1 已在 WorkBuddy 5.3.13
重新安装并通过 ASAR 完整性、JavaScript 语法和白名单行为测试；HK 2026-08-17 自然槽已通过，
US 仍须由下一次自然调度确认实际启动时间与 RRULE 一致。
