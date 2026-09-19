# WorkBuddy 同步交接：港股每日优选自动化

- 文档版本：`v1.5`
- 同步日期：`2026-08-05`（含确定性主报告、约束原因审计与报告质量闸）
- 面向对象：WorkBuddy
- 自动化任务：`港股每日优选研究报告`
- 工作区：`quant`
- 当前状态：`ENABLED / RESEARCH_ONLY`
- 调度：香港时间每周一至周五 `16:35`
- 时间硬闸：实际开始时间 `<16:30 HKT` 时只能记录
  `SKIPPED_EARLY / BEFORE_16_30_HKT` 后结束
- 冻结状态：已于 `2026-07-31 16:57 HKT` 由 Roy 明确授权解除；MVP 缓存工程和数据拉取已恢复

## 一、任务结论

本任务在港股收市后，对最后一个已完成交易日执行香港股票日频研究筛选，并输出
一份 Markdown 报告。它只产生研究候选，不读取账户、持仓或购买力，不生成订单，
不触发任何实盘或模拟盘执行链路。

调度触发不等于允许发布候选。只有当日 HSCI Top 50 排名、日线、行业、显式停牌、
公司行动、点时成分和市场门控全部通过时，才可输出 0–5 只 A 类研究候选；关键
数据不足时必须失败关闭，只输出 `DATA_READINESS / PARTIAL` 报告。系统保证每次
运行给出可信状态，不保证每天非空；空名单是合格结果，禁止强行补票。

Roy 于 `2026-07-30` 明确授权本项研究自动化。这是对本任务的窄范围例外，不扩展
为 QuantBot 生产扫描、交易执行或其他定时任务的授权。

## 二、权威文件及职责

每次运行前依次读取：

1. `C:\Users\RoyGoode\.workbuddy\SOUL.md`
2. `E:\quant\research\hk_daily_selection\WORKBUDDY_SYNC.md`
3. `E:\quant\research\hk_daily_selection\HK每日优选个股策略_v1.md`
4. `E:\quant\research\hk_daily_selection\README.md`
5. `E:\quant\research\hk_daily_selection\resources\INPUT_CONTRACTS.md`
6. `E:\quant\research\hk_daily_selection\default_config.json`

职责划分：

| 文件 | 权威内容 |
|---|---|
| `SOUL.md` | 身份、权限和执行边界 |
| `WORKBUDDY_SYNC.md` | 每日调度、运行步骤、状态和交付口径 |
| `HK每日优选个股策略_v1.md` | 策略方法、因子定义和研究边界 |
| `INPUT_CONTRACTS.md` | 输入字段、点时规则和质量状态 |
| `default_config.json` | 当前生效的数值参数 |
| `README.md` | CLI 使用方法和包边界 |

若文件之间出现冲突：

- 安全和交易权限采用更严格口径；
- 数值参数以 `default_config.json` 为准；
- 输入字段以 `INPUT_CONTRACTS.md` 为准；
- 不得自行修改策略来绕过数据问题。

## 三、触发机制

### 3.1 调度触发

WorkBuddy 配置为香港时间周一至周五 `16:35` 唤醒任务，以吸收平台可能出现的
数分钟提前调度。实际开始后必须按以下顺序检查：

1. 从系统时钟读取并记录 `scheduled_for_hkt`、`started_at_hkt`；
2. 若 `started_at_hkt < 16:30 HKT`，立即记录
   `SKIPPED_EARLY / BEFORE_16_30_HKT` 并结束；
3. 检查当前运行状态；若为 `DATA_ENGINEERING_PAUSED`，按 Step 1 结束；
4. 仅在未冻结时，检查 Futu/HSCI 交易日序列是否出现新的已完成交易日；
5. 检查该交易日是否已有同版本、同输入的成功报告。

`SKIPPED_EARLY` 分支不得等待到 16:30、不得把平台任务的“成功”误写成筛选
`SUCCESS`，也不得调用 Futu、读取旧缓存、运行 `screen` 或生成数据型日报。

### 3.2 执行日期

将最后一个真实出现且已完成的港股交易日记为 `SESSION_DATE`：

- 不用自然日猜测周末或假期；
- 不使用 `16:15 HKT` 前的当日日线；
- 市场休市或没有新交易日时，不重复发布旧名单；
- 若相同 `SESSION_DATE` 的报告已经成功生成且输入、配置未变化，状态记为
  `SKIPPED_DUPLICATE`。

### 3.3 候选发布触发

只有以下条件同时成立，才运行正式筛选：

- `SESSION_DATE` 已完成；
- 基准 `800701.HK` 日线覆盖 `SESSION_DATE`；
- 股票日线全部截断至 `SESSION_DATE`，不存在未来记录参与本次计算；
- 当日 HSCI 成分是点时有效数据；
- 当前 Futu 板块快照仅用于其真实 `source_asof` 日期，不回填历史；
- 关键输入通过结构、唯一性、OHLC、量额、停牌和覆盖检查；
- 未启用 `--allow-survivorship-bias`；
- **市场宽度可计算**；若宽度样本为空或非有限，状态固定为
  `UNKNOWN`，不允许降级到 `OFF`。
- **PIT 校验分两套**：
  - 每日 `screen` 调用 `validate_pit_for_screen`，只校验
    `SESSION_DATE` 当日覆盖；
  - `backtest` 调用 `validate_pit_for_backtest`，校验全部 `trading_dates`。
  `run_selection(mode='screen', session_date=...)` 与
  `run_selection(mode='backtest')` 互不替代，互不降级。
- 正式 `screen` 对 `SESSION_DATE` 每条 bar 强制校验
  `fetched_at_hkt`；缺失即失败关闭。同日抓取必须 `>=16:30 HKT`。
- 日筛母池为当日有效 HSCI `STOCK` 成分，执行池为按同日
  `total_market_val` 降序、代码升序取得的 Top 50；排名证据不完整时不得回退到
  HK 7 或其他任意小样本。
- Top 50 的日线、宽度、行业、停牌与公司行动证据必须分别达到
  `default_config.json` 的覆盖阈值；缺失证券保留为 `NotEvaluated`，不能算作
  `Reject` 或从分母删除。
- 当日日筛使用同日 16:30 HKT 后冻结的 `QFQ` 序列，并保留 Futu rehab 事件
  证据；QFQ/HFQ 若在 `SESSION_DATE` 之后重抓，正式 screen 必须拒绝。
- `screen` 必须先截断 `bars.date <= SESSION_DATE`，且
  `daily_scores.csv` 只能包含 `SESSION_DATE` 当日记录。
- 当日有效的每条 current snapshot 必须满足：
  `source_asof` 非空且 `<=SESSION_DATE`，`observed_at_hkt` 非空。

任一条件不满足时，不得生成确认 A 类名单。

## 四、每日运行流程

### Step 0：系统时间硬闸

- 从操作系统时钟取得带时区的 HKT 时间，不得根据计划时间、文件时间或模型推断；
- 记录：
  - `scheduled_for_hkt`：当日 `16:35 HKT`；
  - `started_at_hkt`：实际开始时间；
- 若 `started_at_hkt < 16:30 HKT`：
  - 状态写 `SKIPPED_EARLY / BEFORE_16_30_HKT`；
  - 记录实际 `finished_at_hkt`；
  - 不调用 Futu；
  - 不读取旧缓存或推断 `SESSION_DATE`；
  - 不运行 `screen`；
  - 不生成新的数据型日报；
  - 立即结束，不等待、不重试、不写 `SUCCESS`。

只有 `started_at_hkt >= 16:30 HKT` 才能进入 Step 1。

### Step 1：状态门与只读预检

当前状态若为 `DATA_ENGINEERING_PAUSED`，立即结束：

- 不调用 Futu；
- 不读取旧缓存推断新 `SESSION_DATE`；
- 不运行 `screen`；
- 不生成新的数据型日报；
- 运行记录写 `SKIPPED / DATA_ENGINEERING_PAUSED`；
- `selection_execution` 写 `NOT_RUN`，`candidate_count` 写 `N/A`；
- 不得把未执行筛选表述为“0 只候选”或“没有股票通过”；
- 同时保留 Step 0 记录的三个系统时间字段。

只有未冻结时，才继续以下预检：

- 确认 Futu OpenD 为 `127.0.0.1:11111`；
- 只允许 `OpenQuoteContext` 和行情读取；
- 禁止访问交易上下文、账户、持仓、购买力和订单接口；
- 禁止调用 `yfinance`；
- 确认所有写入目标位于：
  - `E:\quant\research\hk_daily_selection\data\`
  - `E:\quant\research\hk_daily_selection\output\`

### Step 2：确定 `SESSION_DATE`

根据 HSCI 已完成日线确定最后交易日。若没有新交易日，记录 `SKIPPED` 并结束。

当日基准日线（800701.HK）只能由 `_require_fetch_window` 通过的 fetch 调用
产生：历史日期无 wall-clock 限制；当日（today）须满足 wall time ≥
`16:30 HKT`，wall time < `16:15 HKT` 一律拒绝；同一调用记录
`fetched_at_hkt`（秒级 ISO 字符串）到日线 DataFrame 上。旧报告若
`fetched_at_hkt` 早于 16:30 或缺失，必须重抓后重生成。

### Step 3：冻结输入

正式当日日筛输入约定为（`YYYYMMDD` 替换为 `SESSION_DATE`）：

```text
E:\quant\research\hk_daily_selection\data\hk_daily_bars_top50_qfq_YYYYMMDD.csv
E:\quant\research\hk_daily_selection\data\hsci_membership_screen_YYYYMMDD.csv
E:\quant\research\hk_daily_selection\data\hsci_top50_symbols_YYYYMMDD.txt
```

冻结规则：

- `bars.csv` 只保留不晚于 `SESSION_DATE` 的已完成日线；
- `SESSION_DATE` 每条 bar 必须有可解析的 `fetched_at_hkt`；
- 同日 `fetched_at_hkt` 必须不早于 `16:30 HKT`；
- `date + symbol` 唯一；
- `membership.csv` 使用
  `effective_from <= SESSION_DATE <= effective_to`；
- 当前快照必须带 `is_current_snapshot=true` 与真实 `source_asof`；
- 当前快照每条记录必须带真实 `observed_at_hkt`；
- 每条当日有效 current snapshot 的 `source_asof` 不得为空或晚于
  `SESSION_DATE`；
- 当前快照不得代替历史成分；
- 当日日筛使用 `adjustment=QFQ`，且只能使用同一 `SESSION_DATE` 在 16:30 HKT
  后冻结的序列；严格历史回测使用 `adjustment=NONE`；
- QFQ/HFQ 序列若 `fetched_at_hkt` 日期晚于 `SESSION_DATE`，正式日筛失败关闭；
- Top 50 必须有同日总市值排名、行业、显式停牌状态和公司行动证据；
- 不删除停牌、退市、被剔除或异常样本来美化结果。

### Step 4：数据就绪判断

先按 `default_config.json` 计算并写入报告：

| 指标 | 最低覆盖 |
|---|---:|
| Top 50 排名证据 | 95% |
| 已完成日线 | 90% |
| 市场宽度 | 80% |
| 行业 | 90% |
| 显式停牌 | 100% |
| 公司行动 | 100% |

以下任一情况直接降级为 `PARTIAL`：

- 缺少点时 HSCI 成分；
- 当前快照日期晚于 `SESSION_DATE`；
- 缺少 HSCI 基准日线或市场宽度不可计算；
- 关键 OHLCV/turnover 字段缺失或不合法；
- 历史停牌、证券类型或有效区间无法确认；
- 数据不足以形成至少 120 日特征窗口；
- Futu 历史额度不足，无法完成本次所需增量。
- 任一覆盖率低于上表门槛；
- 复权口径不是同日冻结的 `QFQ`，或 QFQ/HFQ 在后续日期重抓；

降级后 `formal_publish_allowed=false`，所有原本可评分记录只能标为
`Diagnostic`，数据缺失记录标为 `NotEvaluated`，确认 A 类必须为空。

不得通过以下方式强行继续：

- 使用当前 500+ 成分回填历史；
- 无缓存地重拉全部 HSCI 历史；
- 添加 `--allow-survivorship-bias` 生成正式名单；
- 把 B 类升级成 A 类凑数；
- 用主观判断补齐缺失字段。

### Step 5：运行筛选

从 `E:\quant\research` 先构建只读缓存；若同一交易日的 membership 元数据已经
成功冻结，可加 `--reuse-membership` 只增量更新日线：

```powershell
python -B -m hk_daily_selection futu-screen-cache `
  --config .\hk_daily_selection\default_config.json `
  --start 2025-01-02 `
  --completed-session SESSION_DATE `
  --screen-top-n 50 `
  --recent-action-lookback-days 90 `
  --adjustment QFQ `
  --membership-output .\hk_daily_selection\data\hsci_membership_screen_YYYYMMDD.csv `
  --bars-output .\hk_daily_selection\data\hk_daily_bars_top50_qfq_YYYYMMDD.csv `
  --symbols-output .\hk_daily_selection\data\hsci_top50_symbols_YYYYMMDD.txt
```

缓存完成后执行：

```powershell
python -B -m hk_daily_selection screen `
  --bars .\hk_daily_selection\data\hk_daily_bars_top50_qfq_YYYYMMDD.csv `
  --membership .\hk_daily_selection\data\hsci_membership_screen_YYYYMMDD.csv `
  --config .\hk_daily_selection\default_config.json `
  --as-of SESSION_DATE `
  --output-dir .\hk_daily_selection\output\SESSION_DATE\top50_qfq `
  --main-report .\hk_daily_selection\output\SESSION_DATE\HK每日优选_SESSION_DATE.md
```

执行时把 `SESSION_DATE` 替换为 `YYYY-MM-DD`，把 `YYYYMMDD` 替换为无连字符日期。
正式运行禁止加入
`--allow-survivorship-bias`。

CLI 原生证据文件为：

```text
daily_scores.csv
daily_candidates_YYYY-MM-DD.md
```

`daily_scores.csv` 必须包含 `selection_reason`。它记录每条可评分证券的第一项约束
原因：`selected_under_constraints`、`sector_limit`、`issuer_limit`、
`candidate_limit` 或 `market_gate`；数据未就绪时使用 `data_readiness`。报告不得
根据行业或排名自行猜测未入选原因。

对外主报告统一保存为：

```text
E:\quant\research\hk_daily_selection\output\YYYY-MM-DD\HK每日优选_YYYY-MM-DD.md
```

对外主报告必须由 `screen --main-report` 调用
`write_canonical_daily_report` 从当次内存评分表直接生成。WorkBuddy 只能校验并
交付该文件，**不得另写、扩写、重排或二次计算主报告**。如果同日已存在需要纠正
的主报告，保留原文件并把修正版写为
`CORRECTION_HK每日优选_YYYY-MM-DD_vN.md`。CSV 只作为内部可复核证据；用户交付
仅使用 Markdown，不生成 HTML。

### Step 6：报告质检

主报告至少包含：

- `SESSION_DATE`、生成时间、数据源和配置版本；
- 运行状态与证据状态；
- HSCI 市场门控及三项条件；
- A/B/C/Reject 漏斗数量；
- A 类候选的总分和六项因子分；
- A 类的“入选序”和未经重编号的“因子原始排名”；
- 直接来自评分表的 `ATR20`、`extension_atr_20` 与入场质量；
- B 类的 `selection_reason`，不得从行业分布反推；
- 每只 A 类的首要反证或待核验事项；
- 主要排除原因；
- 点时成分、显式停牌覆盖、公司行动状态及最近行动日期和成本警告；
- 独立的 `综述结论`。

A 类为零时必须明确写“允许空名单”，不得为填满版面而补票。

报告质量硬闸：

- 不得出现评分表无法复算的 ATR、扩张度、排名、数量或日期；
- 不得把基准指数的零成交量归到 Top 50 股票；
- 不得把“显式状态均为 False”写成“无显式停牌证据”；
- 不得加入未经程序计算支持的“大盘/中小盘风格”结论；
- 不得使用“适合持仓者”“适合入场”等个性化交易措辞；
- 主报告生成失败时本次状态不得写 `SUCCESS`。

## 五、筛选逻辑

### 5.1 可投资性硬过滤

先从当日有效 HSCI `STOCK` 母池按同日总市值冻结 Top 50。只有覆盖门通过后才进入
硬过滤与评分；Top 50 外记录不是 Reject，而是本次执行池之外。

证券必须同时满足：

| 条件 | 当前参数 |
|---|---:|
| 当日点时 HSCI 成分 | 必须 |
| 证券类型 | `STOCK` |
| 已完成日线 | 必须 |
| 非停牌 | 必须（停牌由显式 `is_suspended` 字段或外部停牌账本判定，**不再由 `volume=0` 推断**） |
| 公司行动证据 | 必须有同日 rehab 查询结果；QFQ 当日日筛保留近期行动证券，NONE 则排除近期行动 |
| 量额大于 0 | 必须（数据质量过滤；零量=警告但不等于停牌） |
| 历史长度 | `>=120` 日 |
| 股价 | `>=HK$1` |
| 60 日成交额中位数 | `>=HK$30m` |
| 近 20 日有效交易日 | `>=18` |
| 20 日年化实现波动 | `<=80%` |
| 单日绝对收益 | `<=25%` |
| 正向趋势 | `Close > MA20 > MA60` |

### 5.2 市场门控

计算三项：

1. HSCI `Close > MA60`；
2. HSCI `MA20 > MA60`；
3. 成分中 `Close > MA20` 的市场宽度。

| 状态 | 条件 | A 类上限 |
|---|---|---:|
| `ON` | 三项全部成立，宽度 `>=55%` | 5 |
| `CAUTION` | 至少两项成立，宽度 `>=45%` | 3 |
| `OFF` | 其他可计算情形 | 0 |
| `UNKNOWN` | 基准或宽度不可计算（含宽度样本为空 / `breadth` 非有限） | 0 |

> **2026-07-30 修正**：当 `breadth` 不可计算时，必须报告为 `UNKNOWN`，
> 不允许落入 `OFF`。`_regime_for_date` 已在代码层固定该行为。

### 5.3 六项评分因子

原始因子先转换为 0–1 横截面百分位，再按：

```text
70% 全市场排名 + 30% 同行业排名
```

行业有效样本少于 3 只时退回全市场排名。

| 因子 | 权重 | 核心含义 |
|---|---:|---|
| Momentum | 20% | 20/60 日动量，跳过最近 5 日 |
| Relative Strength | 15% | 个股 20 日收益减 HSCI 20 日收益 |
| Trend Quality | 15% | 均线趋势强度与 20 日路径效率 |
| Participation | 20% | 5/20 日成交额比与上涨日成交确认 |
| Risk Quality | 15% | 低实现波动、低回撤 |
| Entry Quality | 15% | 距 MA20 约 0.5 ATR 最优，惩罚追高 |

总分：

```text
Score =
  0.20 × Momentum
  + 0.15 × RelativeStrength
  + 0.15 × TrendQuality
  + 0.20 × Participation
  + 0.15 × RiskQuality
  + 0.15 × EntryQuality
```

### 5.4 排名约束

- 总分降序，同分按证券代码升序；
- 每行业最多 2 只；
- 同发行人最多 1 只；
- `ON` 最多 5 只，`CAUTION` 最多 3 只；
- `OFF`、`UNKNOWN` 或关键数据不完整时 A 类为空；
- 排名只表示后续尽调优先级，不是买入建议。

## 六、状态定义

| 状态 | 含义 | 是否可有确认 A 类 |
|---|---|---:|
| `SUCCESS` | 严格输入通过，筛选与报告完成 | 是，可为 0 |
| `SKIPPED` | 无新完成交易日、休市或重复运行 | 否 |
| `SKIPPED_EARLY / BEFORE_16_30_HKT` | 平台提前唤醒；系统时间硬闸后立即结束 | 否 |
| `SKIPPED / DATA_ENGINEERING_PAUSED` | 数据工程冻结；不进入数据流程 | 否 |
| `PARTIAL` | 数据覆盖不足，只生成就绪度报告 | 否 |
| `FAILED` | 程序、依赖或系统错误导致未完成 | 否 |

状态语义硬规则：`SKIPPED_EARLY` 和 `SKIPPED / DATA_ENGINEERING_PAUSED`
都表示 `selection_execution = NOT_RUN`，此时 `candidate_count = N/A`，候选结果
不存在。只有实际运行 `screen` 并得到空集合时，才允许写“0 只候选”。

每次运行都记录：

```text
status
selection_execution
candidate_count
session_date
scheduled_for_hkt
started_at_hkt
finished_at_hkt
config_path
input_paths
evidence_posture
report_path
warnings
```

若运行失败，也应尽可能在当日输出目录写入
`HK每日优选_YYYY-MM-DD_FAILED.md`，保留错误摘要；不得删除失败证据后重跑到“看起来
成功”为止。

## 七、绝对禁止事项

- 不访问账户、持仓、购买力或交易上下文；
- 不下单、不撤单、不改单；
- 不运行 `QuantBot Unified Runner`、`FusionController`、
  `order_executor` 或 `paper_trading`；
- 不修改生产核心、XMM、VP、Gate、执行器或风控参数；
- 不调用 `yfinance`；
- 不使用未来数据、当前成分回填历史或后来披露的财务数据；
- 不因候选太少而放宽门槛；
- 不向外部邮箱、聊天软件或群组自动发送报告；
- 不恢复任何已暂停任务，包括 `OKX v8 XMM Channel Monitor`；
- 不得忽略 Step 0 时间硬闸；实际开始早于 `16:30 HKT` 时，禁止进入任何数据
  或筛选步骤；
- **2026-07-31 起**：Roy 已于 `2026-07-31 16:57 HKT` 明确授权解除数据工程冻结。
  MVP 缓存工程、HK 7 核心 / HSCI Top N 股票日线拉取、A 类确认名单输出均已恢复。
  冻结期间的 `SKIPPED / DATA_ENGINEERING_PAUSED` 记录为历史事实，不追溯修正。
- `PARTIAL / STALE_INTRADAY_BAR` 只用于修正 2026-07-30 的历史事故，不得
  在后续日期重复沿用。

## 八、每日完成检查

WorkBuddy 结束任务前逐项确认：

- [ ] `scheduled_for_hkt/started_at_hkt/finished_at_hkt` 来自系统时钟；
- [ ] 实际开始 `<16:30 HKT` 时只记录
      `SKIPPED_EARLY / BEFORE_16_30_HKT`；
- [ ] 未进入 `screen` 时记录 `selection_execution=NOT_RUN`、
      `candidate_count=N/A`，不写“0 只候选”；
- [ ] 使用最后一个已完成港股交易日；
- [ ] 当日数据在 `16:30 HKT` 或之后抓取；
- [ ] `SESSION_DATE` 每条 bar 均有合规 `fetched_at_hkt`；
- [ ] screen 输出不含 `SESSION_DATE` 之后日期；
- [ ] 点时 HSCI 成分覆盖 `SESSION_DATE`；
- [ ] 当日 current snapshot 每条记录均有合规
      `source_asof/observed_at_hkt`；
- [ ] Top 50 有同日总市值排名，且执行池不是 HK 7 或其他临时小样本；
- [ ] 日线/宽度/行业/停牌/公司行动覆盖率达到配置门槛；
- [ ] 日筛为同日 16:30 后冻结的 QFQ；历史回测仍使用 NONE；
- [ ] 数据缺失记录为 `NotEvaluated`，低就绪度可评分记录仅为 `Diagnostic`；
- [ ] 未使用 `--allow-survivorship-bias`；
- [ ] 未访问任何账户或交易接口；
- [ ] 市场门控与六项因子均可复核；
- [ ] A 类数量符合 `0/3/5` 上限；
- [ ] 数据不足时没有确认 A 类；
- [ ] 主报告路径与文件名正确；
- [ ] 主报告由 `--main-report` 直接生成，WorkBuddy 未二次改写；
- [ ] `selection_reason`、因子原始排名、ATR、扩张度和公司行动日期与
      `daily_scores.csv` 一致；
- [ ] 停牌统计只覆盖 Top 50 股票，不包含 HSCI 基准；
- [ ] 报告不含未计算的风格叙事或个性化交易措辞；
- [ ] 报告包含 `综述结论`；
- [ ] 已记录 `SUCCESS/SKIPPED/PARTIAL/FAILED`；
- [ ] 其他自动化任务状态未被改变。

## 综述结论

WorkBuddy 的职责是稳定、保守、可复核地执行研究流程，而不是每天必须推荐股票。
调度配置为交易日 `16:35 HKT` 唤醒，并以系统时间 `16:30 HKT` 作为不可绕过的
最早执行硬闸；数据完整时从 HSCI 股票母池按同日总市值冻结 Top 50，在排名、
日线、宽度、行业、停牌和公司行动证据就绪后，按可投资性过滤、市场门控和六因子
排名生成 0–5 只研究候选；数据不完整时只报告缺口并保持空名单。任何候选都只用于
后续研究，不构成投资建议，更不取得交易执行权限。数据工程已于 `2026-07-31 16:57 HKT`
由 Roy 授权解除冻结，恢复完整流程。
