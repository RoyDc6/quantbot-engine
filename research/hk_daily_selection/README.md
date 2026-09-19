# Hong Kong Daily Selection Research Prototype

这是一个与 QuantBot 生产链路完全隔离的香港股票日频研究包。它实现：

- 恒生综合指数（HSCI）点时成分股契约；
- 已完成日线校验、流动性/停牌/趋势过滤；
- 市场门控、行业中性多因子排名和每日 0–5 只候选；
- T 日收盘信号、T+1 开盘、持有 3–10 个交易日的研究回测；
- 费用、容量、未来数据污染和幸存者偏差防护；
- Markdown 日报及回测摘要。

它不会读取账户、持仓或交易权限，也不会生成或提交订单。每日筛选可以在输入
契约达到 `READY` 时发布研究候选；真实历史业绩仍为 `PRELIMINARY`，不能把
当日横截面排名解释为已验证 alpha。

## 快速开始

从 `E:\quant\research` 执行：

```powershell
python -m pip install -r .\hk_daily_selection\requirements.txt

python -B -m hk_daily_selection demo `
  --output-dir .\hk_daily_selection\artifacts\synthetic_demo

python -B -m pytest .\hk_daily_selection\tests `
  -q -p no:cacheprovider -c NUL
```

主策略说明见
[HK每日优选个股策略_v1.md](./HK每日优选个股策略_v1.md)，输入字段见
[INPUT_CONTRACTS.md](./resources/INPUT_CONTRACTS.md)。

## 用自有点时数据运行

正式日筛的 `bars.csv` 必须为 `SESSION_DATE` 的每条记录提供
`fetched_at_hkt`。同日数据须在 `16:30 HKT` 或之后抓取；当前成分快照须为每条
`is_current_snapshot=true` 记录提供非空 `source_asof` 和
`observed_at_hkt`。正式股票池为当日 HSCI `STOCK` 成分按同日总市值排序的
Top 50；排名、日线、宽度、行业、停牌和公司行动覆盖未达到配置阈值时，报告
会失败关闭并禁止发布 A 类。完整定义见
[INPUT_CONTRACTS.md](./resources/INPUT_CONTRACTS.md)。

```powershell
python -B -m hk_daily_selection screen `
  --bars D:\data\hk_daily_bars.csv `
  --membership D:\data\hsci_membership_intervals.csv `
  --config .\hk_daily_selection\default_config.json `
  --as-of 2026-07-29 `
  --output-dir D:\output\hk_screen_20260729\evidence `
  --main-report D:\output\hk_screen_20260729\HK每日优选_2026-07-29.md

python -B -m hk_daily_selection backtest `
  --bars D:\data\hk_daily_bars.csv `
  --membership D:\data\hsci_membership_intervals.csv `
  --config .\hk_daily_selection\default_config.json `
  --start 2022-01-01 `
  --end 2026-07-29 `
  --label PIT_HSCI_RESEARCH `
  --output-dir D:\output\hk_backtest
```

历史回测默认拒绝当前成分股快照。`--allow-survivorship-bias` 只用于诊断，
并会把输出明确标为 `PRELIMINARY / SURVIVORSHIP_BIASED`；它不是正式验证路径。

`screen --as-of` 会把特征输入截断到指定日期，并只在
`daily_scores.csv` 中输出该日记录。指定更早日期时，输入文件中更晚的记录不会
进入特征或输出。

`--main-report` 由程序从同一份评分表确定性生成对外 Markdown：保留因子原始排名、
ATR/扩张度、公司行动日期和 `selection_reason`。自动化执行者只能校验和交付该
文件，不应另行扩写或重算主报告，以免产生数值漂移或错误归因。

## Futu 只读入口

读取当天 HSCI 成分快照：

```powershell
python -B -m hk_daily_selection futu-universe `
  --output D:\data\hsci_current_20260730.csv
```

这是当前观察快照，命令会记录真实 `observed_at_hkt`。`--as-of` 只能与实际 HKT
观察日期一致，不能把今天的成分回填成历史快照。

读取少量证券的已完成历史日线：

```powershell
python -B -m hk_daily_selection futu-history `
  --symbols D:\data\hk_symbols.txt `
  --start 2025-01-01 `
  --completed-session 2026-07-29 `
  --adjustment NONE `
  --output D:\data\hk_bars_raw.csv
```

Futu 历史额度按证券计数。不要用此命令无缓存地回填整个 HSCI；应先准备点时
成分事件表，再做有缓存、可续传的数据工程。当前快照不能替代历史成分。

构建当日日筛所需的 HSCI Top 50 只读缓存：

```powershell
python -B -m hk_daily_selection futu-screen-cache `
  --config .\hk_daily_selection\default_config.json `
  --start 2025-01-02 `
  --completed-session 2026-07-31 `
  --screen-top-n 50 `
  --recent-action-lookback-days 90 `
  --adjustment QFQ `
  --membership-output .\hk_daily_selection\data\hsci_membership_screen_20260731.csv `
  --bars-output .\hk_daily_selection\data\hk_daily_bars_top50_qfq_20260731.csv `
  --symbols-output .\hk_daily_selection\data\hsci_top50_symbols_20260731.txt
```

该命令只使用 `OpenQuoteContext`，并为 Top 50 冻结同日总市值排名、行业、显式
停牌状态、公司行动证据和 QFQ 日线。QFQ 缓存只能用于其同一
`completed-session` 的正式日筛；未来日期重抓的 QFQ/HFQ 不得回写历史结果。
严格回测继续使用 `NONE` 加点时公司行动账本。

可选的本机 OpenD 在线测试：

```powershell
$env:RUN_FUTU_READONLY = "1"
python -B -m pytest `
  .\hk_daily_selection\tests\test_futu_readonly.py `
  -q -p no:cacheprovider -c NUL
```

## 包边界

- 允许：`pandas`、`numpy`、Futu `OpenQuoteContext`。
- 禁止：生产 `core`、FusionController、账户、持仓、交易上下文、下单器、
  Unified Runner。
- 研究输出：CSV 与 Markdown。
- 对外主报告：仅由 `screen --main-report` 确定性生成；不接受二次自由改写。
- 执行输出：无。

## 综述结论

这个目录提供的是一个可复核、会在数据不足时失败关闭的研究基线。当日 HSCI
Top 50 输入达到 `READY` 时可以发布 0–5 只研究候选；空名单也是合格结果，绝不
为满足数量而补票。严格历史验证仍需完整的点时 HSCI 成分、历史停牌和公司行动
数据做 rolling walk-forward；不能把单日排名解释为已证明的选股优势。若未来
明确设置 `DATA_ENGINEERING_PAUSED`，定时任务才只记 `SKIPPED`，筛选状态为
`NOT_RUN`、候选数量为 `N/A`。WorkBuddy 调度设为工作日 `16:35 HKT`；
若平台实际开始时间早于 `16:30 HKT`，必须记录
`SKIPPED_EARLY / BEFORE_16_30_HKT` 并立即结束。
