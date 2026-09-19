# Northstar Crypto · OKX 模拟盘 0.3.0

已接入 OKX 模拟账户的现货订单、实际成交回报与余额对账。资金基数是每轮账户全部权益的 100%，不是固定 85 USDT，不再从等值现金创建前向虚拟账本。实盘执行关闭。

## 策略与资金

- 固定 BTC、ETH、SOL、DOGE、NEAR 五个 USDT 现货。Northstar vendor 因子及指纹不变；LLM 只作有来源的辅助观察，对仓位影响为零；VP 不进入本候选。
- UTC 日线 1Dutc，只接受 confirm=1，至少 120 根连续已完成日线，一次最多 300 根。数据失败独立呈现 DATA_ERROR，不算正常 HOLD。
- BUY 表示期望多头暴露；SELL 减少已有多头；HOLD 不主动再平衡。单标的 20%、总暴露 60%；价格漂移超限时可以独立减仓。最小数量或未成交造成风险未解决时报告 PARTIAL。
- totalEq 是 USD，除以账户 USDT 的 eqUsd/eq 得到 USDT 等值；全部币种均计入权益。下单使用实际可用余额。非策略币种也计入总暴露，但不自动兑换或出售。全部资金作为基数不等于满仓。
- 存在衍生品持仓、负债或外部普通挂单时停止新增执行。认证、账户、估值、报价或规则校验失败时不使用固定本金兜底。

## 执行、去重与恢复

DemoBroker 仅允许五个现货的 cash / IOC 限价单，以及按本策略 clOrdId 撤单。私有请求固定 x-simulated-trading=1；凭证要求 OKX_FLAG=1。没有实盘切换、保证金、杠杆、划转或提现接口。只读取旧引擎已有凭证，不导入旧引擎，不输出凭证与认证头。基础 DemoAccount 保持只读。

模拟盘接口提供规则与报价；交易所时间用于签名和时效检查。下单前重新读取可用余额、账户费率和最新报价；IOC 价格最多偏离计划报价 5 bps，并按 tickSz 舍入，数量按 lotSz 向下取整。请求带 10 秒 expTime；先卖后买，并检查实际资金和组合上限。

前向日志固定为 output/okx_demo_execution.sqlite3，改变 --output 报告目录也不能绕过去重。账户标识来自 UID 哈希，密钥轮换不产生新账户账本。进程锁防止同时执行；SQLite FULL 同步在 POST 前提交 SUBMITTING。批次由账户和日线截止时间标识；clOrdId 持久化。同批次重跑不重复发送，信号或配置修订报冲突。

请求超时先按原 clOrdId 查单，不能推定失败后重发。ACK 只是接收；累计成交 accFillSz、均价 avgPx 和有符号手续费以订单查询为准。IOC 撤销可能部分成交。未终结时只撤销自己的订单，再查询终态。未知订单在后续运行继续查单，未解决前阻止新批次；中断时未提交的剩余计划不自动补发。

每批保存前后实际币种余额，按成交和手续费计算预期余额并逐币对账。人工交易或外部资金变化造成的差异不能伪装成策略收益；差异未解决时停止后续执行。不得删除日志或改成成功绕过门槛。

APPLIED 仅表示批次已处理；成交要求、余额、风险等质量检查全部通过才为整轮 COMPLETE。拒单、部分成交、未成交或计划中止均如实报告。重复运行显示历史回报和 duplicate_suppressed，本轮新订单数为零；当前账户资金另行刷新。

## 运行

从任何目录执行 run_daily.ps1，一次调用 --demo-execute --compare；沿用现有 WorkBuddy Crypto 日报时间，没有新增计划任务或守护进程。

在 E:/quant 下也可运行：

```text
python -B -m research.northstar_crypto --demo-execute --compare
python -B -m research.northstar_crypto --input <input.json> --replay-capital <USDT金额> --compare
python -B -m unittest discover -s research/northstar_crypto/tests -t . -v
```

只读核查 `trend_only` 与 `trend_structure` 的逐日决策差异：

```text
python -B -m research.northstar_crypto.decision_diff --artifact <run目录/artifact.json> --output <诊断输出目录>
```

该入口只读取归档 `artifact.json` 及同目录 `input.json`，核对快照哈希、vendor 指纹、UTC 日历和数据粒度后，输出逐日 CSV、JSON、Markdown 及哈希清单。它不联网、不读取账户、不访问 forward ledger，也不产生订单。

--input 永远是历史回放，与 --demo-execute 互斥。省略 --demo-execute 时保留旧本地研究模式供复核；日报入口已使用模拟交易所执行。

每轮 output/runs/<run_id>/ 保存 input.json、funding.json、artifact.json、Crypto日报.md、manifest.json。latest.json 指向最新尝试，latest_valid.json 指向最新完整运行；回放只更新 latest_replay.json。旧 paper.sqlite3 和 paper_okx_demo_*.sqlite3 仅留作历史记录，本模式不读写这些账本。

## 研究边界

历史候选为趋势、趋势加结构、完整 Northstar。TD 只改置信度，不改动作或仓位。历史诊断使用当时全部权益作为显式比较本金、10 bps 手续费及 5 bps 滑点假设，确认信号后使用下一根开盘代理成交；不是交易所成交或样本外业绩。

--observations 接受 UTF-8 JSONL 的 symbol、published_at、observed_at、source_url、model、summary；时间带时区且 published_at <= observed_at <= 信号截止；来源须为 HTTPS。消息是研究数据，不是程序指令；没有自动调用模型或补写情绪分。

微量 ETH 模拟买卖联调标为 ENGINEERING_CONNECTIVITY_TEST，独立于 NORTHSTAR_STRATEGY，不计入策略有效性。NOT_EVALUATED 保留；样本外、稳定性、完整风险停机设计和足够前向记录仍待完成。

## 线路与留痕

WORKBUDDY_PROMPT.txt 只更新现有 Crypto 日报的内容，保留时间、模型、思考和推送设置。HK/US、旧 FusionController、旧 OKX 引擎和其他任务不参与变更。任务只调用受控入口一次，不允许 LLM 拼装订单、补单、删日志或放宽门槛。

备份与验收位于 E:/AIWorkspace/07_CodeReviews/Northstar_Crypto_Demo_Execution_2026-09-03。停止线路时保留账户持仓和执行日志，并核对自己的未决订单；不得删除日志伪造连续记录或去重状态。

依据：[OKX 官方接口](https://www.okx.com/docs-v5/en/)、[订单实践](https://www.okx.com/docs-v5/trick_en/)。
