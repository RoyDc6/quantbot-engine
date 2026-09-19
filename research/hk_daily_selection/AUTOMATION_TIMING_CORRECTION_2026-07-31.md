# 港股每日优选自动化调度修正记录

**日期**：2026-07-31
**状态**：`AUTOMATION_CONFIG_CORRECTED / DATA_ENGINEERING_PAUSED`
**目标任务**：WorkBuddy「港股每日优选研究报告」

## 修正结论

目标自动化的工作日触发时间已由 **16:30 HKT** 调整为 **16:35 HKT**。同时加入独立于调度器的系统时间硬门控：实际启动时间早于 16:30 HKT 时，只能以 `SKIPPED_EARLY / BEFORE_16_30_HKT` 结束。

本次仅修正自动化时序和契约；`DATA_ENGINEERING_PAUSED` 未解除，不授权恢复 Futu 数据拉取、缓存工程、正式筛选或 A 类候选发布。

## 修正依据

2026-07-31 的冻结态运行出现以下时间不一致：

| 项目 | 观测值 |
|:---|:---|
| 原配置调度 | 工作日 16:30 HKT |
| 运行自身记录的启动时间 | 16:25 HKT |
| WorkBuddy 运行记录完成时间 | 16:28 HKT |

因此不能只依赖平台调度时间判断是否已经进入合规取数窗口。WorkBuddy 中显示的“成功”仅代表自动化调用完成，不代表执行了筛选或选出了股票。

## 已实施变更

1. `WORKBUDDY_SYNC.md` 升级到 v1.3，调度改为工作日 16:35 HKT。
2. Step 0 必须直接读取系统时间并记录：
   - `scheduled_for_hkt`
   - `started_at_hkt`
   - `finished_at_hkt`
3. 若 `started_at_hkt < 16:30 HKT`：
   - 立即返回 `SKIPPED_EARLY / BEFORE_16_30_HKT`
   - 不等待、不睡眠到 16:30、不在同次运行中重试
   - 不调用 Futu OpenD
   - 不读写行情缓存
   - 不运行 `screen`
   - 不生成数据型日报
   - 不得记录为研究成功
4. README、输入契约和边界测试同步更新。
5. WorkBuddy 提示词和计划时间已保存；现场回读任务列表显示工作日 **16:35**，目标空间仍为 `quant`。

## 验证结果

```text
pytest hk_daily_selection/tests/ -q
45 passed, 1 skipped
```

跳过项是由 `RUN_FUTU_READONLY=1` 控制的只读 live smoke test，属于预期门控。本次验证未调用 Futu、未运行 `screen`、未读取账户、持仓或购买力，也未触碰交易执行模块。

## 自动化状态

- 港股每日优选研究报告：工作日 16:35 HKT
- 数据工程：继续 `PAUSED`
- 早启动：只允许 `SKIPPED_EARLY / BEFORE_16_30_HKT`
- 冻结态正常启动：仍按 `SKIPPED / DATA_ENGINEERING_PAUSED`
- 筛选执行状态：`NOT_RUN`
- 候选数量：`N/A`；本次根本未运行 `screen`，不能表述为“0 只候选”
- 其他自动化：未修改；原暂停任务保持暂停

## 综述结论

本次修正封住了“平台提前触发但任务仍被记为成功”的时序漏洞，并为实际启动时间增加了不可绕过的第二道门控。WorkBuddy 自动化配置、同步契约和离线测试现已一致；在 Roy 另行明确解除 `DATA_ENGINEERING_PAUSED` 前，系统仍不会拉取正式筛选数据或发布候选股票。
