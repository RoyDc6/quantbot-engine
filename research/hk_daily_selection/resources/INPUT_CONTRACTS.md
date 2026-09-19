# 输入数据契约

## 1. `bars.csv`

每行是一只证券的一个已完成交易日。

| 字段 | 必需 | 类型 | 定义 |
|---|---|---|---|
| `date` | 是 | date | 香港市场已完成交易日，不含盘中未完成日线 |
| `symbol` | 是 | string | `00700.HK`；也接受并规范化 `HK.00700` |
| `open` | 是 | float | 当日开盘价，必须大于 0 |
| `high` | 是 | float | 当日最高价，必须覆盖 OHLC 其余价格 |
| `low` | 是 | float | 当日最低价，必须不高于 OHLC 其余价格 |
| `close` | 是 | float | 当日收盘价，必须大于 0 |
| `volume` | 是 | float | 非负成交量 |
| `turnover` | 是 | float | 非负成交额，单位 HKD |
| `is_suspended` | 否 | bool | 若行情提供则保留；缺失保持未知，禁止默认成 `false` |
| `suspension_source` | 否 | string | bar 级停牌状态的来源；`volume=0` 不能替代停牌证据 |
| `adjustment` | 是 | string | 当日日筛使用同日冻结的 `QFQ`；严格回测使用 `NONE` 加点时公司行动账本 |
| `source` | 否 | string | 数据来源与版本 |
| `fetched_at_hkt` | 日筛必需 | datetime | 数据实际抓取时间，必须带时区；同日抓取须不早于 16:30 HKT |
| `source_high` / `source_low` | 修复时必需 | float | Futu 原始高低价；只有小幅包络异常被有界修复时写入 |
| `ohlc_envelope_repaired` | 否 | bool | 是否做过可审计的 OHLC 包络修复 |

约束：

- `date + symbol` 必须唯一；
- OHLC 必须有限且大于 0；
- `high`、`low` 必须满足价格包络。Futu 原始包络偏差不超过 0.5% 时可按
  `high=max(open,high,low,close)`、`low=min(open,high,low,close)` 有界修复，
  但必须保留 `source_high/source_low` 和修复标记；更大偏差直接失败；
- 量额不得为负；
- 零量或零额不会被删除，会产生质量警告并在筛选时排除。
- 正式 `screen` 要求 `SESSION_DATE` 每条输入记录都有
  `fetched_at_hkt`。若抓取日期等于 `SESSION_DATE`，时间必须
  `>=16:30 HKT`。`QFQ/HFQ` 调整序列若在 `SESSION_DATE` 之后重抓，可能把
  后续公司行动编码进历史，因此正式日筛直接拒绝；历史补抓只能使用 `NONE`。
- `screen` 会先截断 `bars.date <= SESSION_DATE`，并且
  `daily_scores.csv` 只能包含该 `SESSION_DATE`，不得带出之后日期。
- `daily_scores.csv` 必须保留 `selection_reason`，记录第一项实际约束决定；
  `sector_limit`、`issuer_limit`、`candidate_limit` 和 `market_gate` 不得由报告层
  事后猜测。对外主报告只能由 `screen --main-report` 从同次评分表确定性生成。

基准日线使用 `800701.HK`，即 Futu 的 `HK.800701`。

## 2. `membership.csv`

每行是一个成分资格的点时有效区间；首尾日期均为包含关系。

| 字段 | 必需 | 类型 | 定义 |
|---|---|---|---|
| `symbol` | 是 | string | 成分代码 |
| `effective_from` | 是 | date | 首个有效交易日 |
| `effective_to` | 是 | date/blank | 最后有效交易日；空值表示仍有效 |
| `name` | 是 | string | 当时可知名称 |
| `sector` | 是 | string | 当时可知行业；空值会削弱行业约束并警告 |
| `security_type` | 否 | string | 缺省 `STOCK`；基线仅允许 `STOCK` |
| `issuer_id` | 否 | string | 同发行人的不同股类映射；缺省为证券代码 |
| `lot_size` | 否 | number | 当时每手股数 |
| `is_current_snapshot` | 否 | bool | 当前快照必须为 `true` |
| `source_asof` | 当前快照必需 | date | 该记录实际可获知日期 |
| `observed_at_hkt` | 当前快照必需 | datetime | 快照实际观察时间，必须带 HKT 时区 |
| `membership_source` | 否 | string | 来源、公告或版本标识 |
| `total_market_val` | Top N 日筛必需 | float | `SESSION_DATE` 同日市场快照总市值 |
| `screen_universe_rank` | Top N 日筛必需 | integer | 按总市值降序、代码升序确定的日筛排名 |
| `screen_universe_source` | Top N 日筛必需 | string | 日筛子池的定义与来源 |
| `snapshot_is_suspended` | Top N 日筛必需 | bool | Futu 同日市场快照显式停牌状态 |
| `suspension_source` | Top N 日筛必需 | string | 停牌状态来源 |
| `market_snapshot_observed_at_hkt` | Top N 日筛必需 | datetime | 市场快照实际观察时间 |
| `corporate_action_status` | Top N 日筛必需 | string | `CLEAR`、`RECENT_ACTION` 或 `UNKNOWN` |
| `has_recent_corporate_action` | Top N 日筛必需 | bool | 配置回看窗口内是否有 Futu rehab 事件 |
| `last_corporate_action_date` | 否 | date | 最近一次已知公司行动日期 |
| `corporate_action_observed_at_hkt` | Top N 日筛必需 | datetime | 公司行动证据实际观察时间 |

约束：

- 同一证券的有效区间不得重叠；
- 每个回测交易日必须至少有一只有效成分；
- 当前快照的 `source_asof` 之前不得用于严格历史回测；
- 日筛对每条当日有效的 `is_current_snapshot=true` 记录逐行检查：
  `source_asof` 不得为空或晚于 `SESSION_DATE`，`observed_at_hkt`
  不得为空；
- 退市、停牌、代码变更及被剔除证券必须保留，不能只保留今天仍存在的代码。
- `get_plate_stock` 当前快照不得由调用方回填一个历史
  `observed_asof`；`source_asof` 必须来自真实观察日。
- 正式当日日筛使用 HSCI 股票成分中按同日 `total_market_val` 排名的 Top 50；
  排名覆盖、日线、宽度、行业、停牌、公司行动覆盖分别达到配置阈值后，才允许
  发布 A 类。缺失记录保留为 `NotEvaluated`，不得当成 `Reject` 或从分母删除。

## 3. 点时公司行动账本

当前原型尚未实现完整公司行动现金流。正式回测应补充：

- 公告日、除净日、支付日；
- 拆合股、供股、特别股息、现金股息；
- 代码和股类变更；
- 每个字段的来源与当时可知时间。

严格历史回测保存原始 `AuType.NONE` 价格，再按事件日期处理。每日横截面日筛可
使用在 `SESSION_DATE` 当日 16:30 HKT 后冻结的 `AuType.QFQ` 序列，同时保留
Futu rehab 事件证据；该快照只对同一 `SESSION_DATE` 有效，不得用于更早日期或
在未来日期重抓后回写旧日筛。这样既避免近期除权造成的伪动量，也阻断未来公司
行动泄漏。

## 4. 完整性状态

| 状态 | 含义 |
|---|---|
| `RESEARCH_GRADE_INPUT_CONTRACT` | 数据通过结构和点时契约；不等于策略有效 |
| `PRELIMINARY` | 仍缺公司行动、历史停牌或样本外稳定性等证据 |
| `PARTIAL` | 明确存在覆盖缺口 |
| `SURVIVORSHIP_BIASED` | 显式允许当前成分快照回填历史，仅作诊断 |
| `SYNTHETIC_DEMO_ONLY` | 只验证代码路径，不能用于收益结论 |
| `STALE_INTRADAY_BAR` | 特定报告使用了收盘前抓取的当日缓存；仅用于事故修正 |
| `DATA_ENGINEERING_PAUSED` | 数据工程被冻结；自动运行应 `SKIPPED`，`selection_execution=NOT_RUN`、`candidate_count=N/A`，不得重复使用旧数据 |
| `SKIPPED_EARLY / BEFORE_16_30_HKT` | 平台实际开始时间早于 16:30 HKT；禁止访问数据并立即结束，筛选结果不存在 |

## 综述结论

真实策略验证的最低数据门槛不是“有一份历史价格”，而是同一时点可知的成分、
完整已收盘日线、停牌状态与公司行动。任一关键字段缺失时，应保留证券和缺口，
并降级证据状态，而不是删除问题样本。若未来再次明确设置数据工程冻结，才记录
`SKIPPED / DATA_ENGINEERING_PAUSED`；当前状态以 `WORKBUDDY_SYNC.md` 为准。
WorkBuddy 即使配置在
16:35 HKT，也必须使用系统时钟执行 16:30 HKT 硬闸；提前唤醒只允许记录
`SKIPPED_EARLY / BEFORE_16_30_HKT`。上述两个跳过状态都表示根本没有执行
筛选，候选数量必须为 `N/A`；只有实际运行 `screen` 后得到空集合，才可记为
“0 只候选”。
