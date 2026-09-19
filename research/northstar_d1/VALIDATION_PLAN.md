# Northstar-D1 样本外验证计划

**状态**：`SIM_FORWARD_STARTED / OOS_FORWARD_COLLECTING`
**模型状态**：`RESEARCH_ONLY / NOT_EVALUATED / formal_publish_allowed=false`
**创建日期**：2026-08-04

## 一、当前结论

现有 `northstar_d1_hk_backtest` 与 `northstar_d1_us_backtest` 是无未来数据的
`IN_SAMPLE_DIAGNOSTIC`，只能验证信号时序、成本处理和回撤计算是否合理，不能作为
样本外放行证据。

Northstar-D1 在完成以下流程前必须保持 `NOT_EVALUATED`，不得连接真实账户、生成真实
订单或改变 `formal_publish_allowed=false`。自 2026-09-16 起，经用户明确授权，允许独立
的 `research.northstar_d1_futu_sim` 执行层读取不可变产物并连接 Futu `SIMULATE` 账户，
用于前向模拟成交、持仓与故障证据采集。该授权不改变模型状态，也不允许 `TrdEnv.REAL`。

## 二、冻结基线

以下 SHA256 作为本次 OOS 准备时点的只读模型基线：

| 文件 | SHA256 |
|---|---|
| `model.py` | `927EC34441F5DB8D05206C29223FE2ACB84803B1235091F29F8DCEE2CA2064AA` |
| `factors.py` | `1FEC506EA694168F15AC2947EADBE94D07B64F252E3BCDB9D535C8B84E28804F` |
| `config.py` | `C35B73D9D27C2A264CE1596A587F765F63318C010AA351331F7DC24B8768542A` |
| `backtest.py` | `0E73EDC7C3E2739950071D2AFF9BFDD385D52B7A47F87BF0B243C8161BB29450` |

正式 OOS 开始前需要生成独立 immutable manifest，记录模型版本、上述哈希、市场、
标的池、数据口径、成本假设、冻结时间和评估结束条件。冻结后若修改模型核心、参数
或信号规则，必须创建新的验证版本，旧窗口不得追认给新版本。

## 三、验证设计

1. HK 与 US 独立评估，不建立合并市场结果。
2. 只使用冻结时点之后新形成的 Futu QFQ 已完成日线；禁止缓存、fallback 和盘中日线。
3. 信号在收盘计算，从下一交易时段开始计入结果，禁止 look-ahead。
4. paper intent 可由独立的 Futu 模拟前向执行层转换为仓位差额；模型包本身仍不访问账户，
   不连接 QuantBot Gate，并继续把 paper intent 标记为非订单。
5. 每次观察追加写入 immutable run archive，不覆盖历史失败或无信号记录。
6. 评估窗口、成本情景、主指标和放行阈值必须在观察结果形成前预先登记；不得看到
   结果后再选择最有利的窗口或阈值。

## 四、必须报告的指标

- 数据与覆盖：预期/实际交易日、缺失交易日、标的覆盖、错误与 fail-closed 次数。
- 信号行为：BUY/SELL/HOLD 数量、paper intent 数量、平均暴露、换手及持有时长。
- 绩效：总收益、基准收益、超额收益、年化收益、年化波动、Sharpe、最大回撤。
- 事件质量：BUY/SELL 后固定观察窗的方向命中率、收益分布与样本数。
- 稳健性：按市场状态、标的和时间段拆分；报告集中度及少数标的主导风险。
- 成本敏感性：至少报告基准成本与更高成本情景，不能只展示零成本结果。

## 五、放行门禁

只有同时满足以下条件，才允许提出从 `NOT_EVALUATED` 升级的评审请求：

- 冻结基线与 OOS manifest 完整且哈希可复核；
- 数据全部为对应市场的 Futu 已完成交易日，且无缓存/fallback；
- 预登记窗口完整结束，没有历史回填或选择性删除；
- 所有主指标、负面结果、失败关闭记录和成本情景均完整交付；
- HK/US 分别得出结论，不以一个市场的结果替另一个市场放行；
- 独立复核确认无 look-ahead、无真实交易连接、无样本期后调参。

即使满足上述条件，也只能提交人工评审；本文件本身不自动改变验证状态或执行权限。

## 六、当前阻塞项

目前没有在模型冻结前预登记的独立未来窗口，因此无法把已有历史回测重新命名为
真正的 OOS。下一步应从冻结基线之后开始积累 forward evidence，或由 Roy 另行指定
一个在模型开发中完全未使用、可证明未被查看的保留区间。

## 综述结论

Northstar-D1 已于 2026-09-16 启动 Futu 模拟前向执行与证据积累，当前仍是
`NOT_EVALUATED`。报告展示和模拟成交不能替代模型有效性验证；在新的、预登记且不可
回看的独立样本形成前，正式发布和真实资金交易继续保持关闭。
