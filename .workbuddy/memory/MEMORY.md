# MEMORY.md - 长期记忆

## 信号报告规范（2026-05-07）
**教训**：报告交易信号时，必须穿透三层——信号层（fusion_level/score）→ 执行层（风控规则是否放行）→ 结果层（实际订单/排除原因）。不能只看信号就下结论，必须追踪完整的规则链路。

关键风控规则：
- `exclude_if_pnl_above: 0.05` — 浮盈>5%时，SELL信号反转清仓被排除（"让利润跑"）
- `FIXED_STOP_PCT: -0.12` / `TRAILING_STOP_PCT: -0.12` / `PORTFOLIO_DD_PCT: -0.30` 三重止损
- `MAX_POSITION_PCT: 0.20` / `MAX_TOTAL_PCT: 0.80` 仓位限制
- `STOP_COOLDOWN_DAYS: 10` 止损冷却期

## 缠论模块 API 备忘（2026-05-07）
- `mark_fractals()` 返回 DataFrame，列 `fractal`（1=底, -1=顶, 0=非）和 `fractal_price`
- `identify_strokes()` 不存在于 `chan.stroke`，需要确认实际函数名
- 缠论分析脚本 `test_chan.py` 已删除（不存在）

## 系统运行环境
- Python 3.12 路径: `C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe`
- 工作目录: `E:\quant`
- Futu OpenD: 127.0.0.1:11111
- **OpenD 部署位置**: `C:\Users\RoyGoode\AppData\Roaming\Futu_OpenD\`（自升级机制）
- **OpenD 快捷方式**: `C:\Users\RoyGoode\Desktop\Futu OpenD.lnk` → `AppData\Roaming\Futu_OpenD\Futu_OpenD.exe`
- **OpenD 配置**: `C:\Users\RoyGoode\AppData\Roaming\Futu_OpenD\FutuOpenD.xml`（已填入 Roy 的牛牛号 3433285）
- **OpenD 更新方式**: 自动自升级，升级后检查旧版本目录(`Futu_OpenD_10.x.xxxx_Windows`)可手动清理
- **futu-api SDK**: pip install futu-api，从 Aliyun 镜像 `https://mirrors.aliyun.com/pypi/simple/` 安装
- **futu-api 10.6 新增接口**：条件选股（多维度筛选）、个股基本面（三大报表/分析师评级/估值/分红/股东持股）、moomoo AU 模拟交易比赛账户
- **统一 Futu 连接层**: `FutuAdapter` (`core/futu_adapter.py`) 是唯一的 Futu API 连接入口。`core/data_fetcher.py` 已归档至 `_archive/`。
- TICKFLOW_API_KEY 文件回退: `E:\quant\config\tickflow_key.txt`（SYSTEM 账户无法读 HKCU 注册表，需文件回退）

## 数据源优先级（2026-05-13 用户明确纠正）
**重要：Futu优先，TickFlow作为备选补充**（港美股统一策略）
- 港美股均优先使用 Futu OpenD 获取 K 线数据
- TickFlow 仅在 Futu 不可用时作为降级方案
- `scanner2/us_daily_scan.py` 和 `scanner2/hk_daily_scan.py` 已按此逻辑实现

## SYSTEM 账户限制（2026-05-08 教训）
- **根因**: SYSTEM 账户无法读取 HKCU 注册表，导致 TickFlow 降级为 free 版本，港股数据不足
- **修复**: `tickflow_env.py` 增加 `config/tickflow_key.txt` 文件回退 + `data_fetcher.py` 港股增加 Futu 回退

## Futu API 关键模式（2026-05-13 踩坑记录）
```python
from futu import OpenQuoteContext
ctx = OpenQuoteContext(host=config.FUTU_HOST, port=config.FUTU_PORT)
ret, data, page_key = ctx.request_history_kline(
    futu_code, start=start_dt, end=end_dt,
    ktype='K_DAY',       # 用字符串 'K_DAY'，不是枚举 QKType
    autype='qfq'         # ⚠️ 用 'qfq'(前复权)，禁用 None！见下方说明
)
# ret==0 且 data 为 DataFrame 才算成功
```
- 股票代码格式：`US.AAPL` / `HK.00700`
- 返回 3 值：`(ret, data, page_key)`，不是 2 值
- **⚠️ `autype='qfq'` 警钟（2026-05-20 重大教训）**: 之前记录 `autype=None`（不复权），导致 NVDA 回测出现灾难性假信号——2024/6/7 的 10:1 拆股使价格从 ~$1037 暴跌至 ~$121（-90%），策略误判为POC跌破出仓。使用 `autype='qfq'`（前复权）后 NVDA 策略收益从 **-61.93%→+248.58%**，最大回撤从 **-91.74%→-31.74%**。**此后端API的默认不复权(None)行为是陷阱——回测必须用前复权数据。**
- **⚠️ `get_cur_kline` vs `request_history_kline`（2026-05-21 教训）**: `get_cur_kline()` 需要先调用 `subscribe()` 订阅才能用，未订阅时返回 `ret=-1` + 错误字符串。`request_history_kline()` 不需要订阅，直接可用。**所有获取K线的代码统一用 `request_history_kline`，不要用 `get_cur_kline`。**`market_state/classifier.py` 的 `load_price_data()` 已修复。

## LLM情绪因子API故障 - 已修复（2026-05-20）
- **原始现象**: event_summary 报 `JSON 解析失败 (增强解析器 v3 无法提取)`
- **根因**: mistralai/mixtral-8x7b-instruct-v0.1 返回 JSON 键名含转义下划线（如 `sentiment\_score`），`json.loads` 报 `Invalid \escape`
- **修复**: `event_detector.py` 的 `_parse_json()` 开头添加 `text.replace('\\_', '_')` 预归一化
- **影响解除**: LLM 情绪因子(`event_sentiment_score`)恢复有效信号

## 徐小明（XMM）策略核心设计（2026-05-19 最终版）
**核心原则**: "趋势为王，结构修边，序列为辅"

### 趋势线定义
- **短顶**: EMA(H, 25) | **短底**: EMA(L, 25)
- **长顶**: EMA(H, 90) | **长底**: EMA(L, 90)
- **UP**: 收盘价 > 长顶 | **DOWN**: 收盘价 < 长底 | **SIDEWAYS**: 介于两者之间

### 决策矩阵（13条规则）

| 趋势 | 结构 | TD | 动作 | 仓位 |
|------|------|----|------|------|
| UP | 底结构 | 低9 | BUY | 80-100% |
| UP | 顶结构 | 无 | SELL止盈 | 25% |
| UP | **顶钝化** | **高9+连续N天** | **SELL override** | **40%** |
| UP | 无 | 任意 | HOLD | — |
| DOWN | 顶结构 | 高9 | SELL做空 | 80-100% |
| DOWN | 底结构 | 无 | HOLD(不抄底) | — |
| DOWN | 底钝化 | 低9 | BUY轻仓 | 10-20% |
| DOWN | 无 | 任意 | HOLD空仓 | — |
| 震荡 | 有结构+TD共振 | — | 轻仓试探 | 20-40% |
| 震荡 | 有结构无共振 | — | HOLD观望 | — |
| 震荡 | 无 | — | HOLD空仓 | — |

### 关键override规则
趋势向上时，**顶部钝化 + TD高9 + 连续钝化≥N天(默认3)** → 无条件减仓40%
**不依赖ATR止损** — 认为ATR止损是事后优化，实盘无效

### 关键文件
- `E:\quant\xmm-strategy\modules\engine.py` — 决策融合引擎
- `E:\quant\xmm-strategy\modules\structure.py` — MACD结构/钝化计算
- `E:\quant\xmm-strategy\modules\trend.py` — 双EMA趋势线
- `E:\quant\xmm-strategy\modules\td_sequence.py` — TD9序列
- `E:\quant\xmm-strategy\xmm_strategy.py` — 主入口脚本(SPY)

## 港股信号剧烈波动警告（2026-05-21 更新）
6天内4次全面翻转：5/14全多(BUY:6)→5/15全空(SELL:5)→5/18全空(SELL:4)→5/19全多(BUY:6)→5/20全空(SELL:4)→5/21偏空(BUY:1,SELL:5)
- **特征**: BULL regime下信号极度不稳定，市场可能在CRAB/BULL边界震荡
- **影响**: 短线频繁反转导致5/14建仓→5/15止损→5/19再建仓→5/20再看空的循环
- **建议**: 考虑加入信号平滑/去抖机制，或在regime临界状态降低执行频率
- **持仓影响**: 5/19买入00700→5/21信号反转卖出，亏损约-0.3%（成本458.40→卖出457.00）

## 扫描股票池（config.py）
- **美股**（15只）：AAPL/AMZN/MSFT/GOOGL/META/NVDA/TSLA/AMD/AVGO/ORCL/NFLX/CRM/ADBE/INTC/QCOM
- **港股**（7只）：00700/00388/09988/03690/09618/01024/06055

## Volume Profile 策略核心发现（2026-05-20 最终版）
**文件**: `E:\quant\fusion_framework\volume_profile.py`

### 策略架构
- **Volume Smearing**: 每根K线成交量均摊到 [low,high] 区间bins
- **双指针扩张**: 从POC向两侧按体积扩张至70%确定VAH/VAL
- **三个空间状态**: above_box→VP_BUY(conf=0.8, 奇点A), below_box→VP_SELL(conf=0.8), inside_box→HOLD(conf=0.2)

### 奇点A（Breakout）— 原生入场信号
- 价格突破VAH → VP_BUY (conf=0.8)。这就是Volume Profile策略的标准用法，不是后来"加入"的
- 不需要大阳线或放量确认条件——纯版本策略，等实盘数据反馈后再决定是否加条件

### 奇点B — **已废弃（已从代码中彻底删除）**
- 回测证明在09988/BTC/ETH/03690上均为净负贡献
- 已于2026-05-20从 `volume_profile.py` 和 `vp_benchmark.py` 中完全移除

### 跨资产回测结论（2026-05-20 POC跌破退出规则）

| 标的 | 策略收益 | B&H收益 | 超额 | 回撤 | 胜率 | 交易次 |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| 00700.HK | **+109.97%** | +89.23% | **+20.74%** | -20.88% | 100% | 5 |
| 03690.HK | **-9.58%** | -45.42% | **+35.84%** | -37.33% | 25% | 8 |
| 01810.HK | +88.04% | +203.68% | -115.64% | -32.40% | 33.3% | 9 |
| **NVDA** ⚡ | **+248.58%** | +969.22% | -720.64% | -31.74% | 60% | 10 |
| TSLA | +33.15% | +101.43% | -68.28% | -43.65% | 50% | 8 |

**⚠️ NVDA 修复关键**：修复前（`autype=None` 不复权）NVDA 策略收益 -61.93%，最大回撤 -91.74%（拆股假信号）。修复后（`autype='qfq'` 前复权）NVDA +248.58%，回撤 -31.74%。

**核心规律（更新）**：VP+POC跌破在**震荡/下跌市**碾压基准（00700/03690均超额正），在**强势上涨市**赚得不错但跑不赢纯持有（NVDA/TSLA/01810跑输B&H）。00700 100%胜率是亮点。
- **策略定位不变**: FusionEngine权重25%，作为低暴露度风险防护层
- 最大回撤方面策略均优于基准（00700 -20.88% vs -51.48%, 03690 -37.33% vs -69.96%）

### 回测脚本
- `E:\quant\tmp\vp_benchmark.py` — 纯净版，无奇点B/对比模式
- 用法: `python vp_benchmark.py <data.json> --lookback 120 --bins 100`

## 备份体系（2026-05-26 建立）
### Skills 技能备份
- **tar.gz**: `E:\quant\backups\skills_2026-05-26_0550.tar.gz` (406KB, 恢复: `tar xzf`)
- **Git**: `E:\quant\backups\skills-git/` → GitHub 私有仓库 `RoyDc6/skills-backup` (tag: v2026-05-26)
- GitHub Token: 已保存至 `~/.workbuddy/MEMORY.md`

### QuantBot 全量备份
- **tar.gz**: `E:\quant\backups\quantbot_full_2026-05-26.tar.gz` (456MB, 3606文件)
- **Download**: https://github.com/RoyDc6/quantbot-backup/releases/download/v2026-05-26/quantbot_full_2026-05-26.tar.gz
- **GitHub**: `RoyDc6/quantbot-backup`（私有仓库, Release v2026-05-26）
- 包含: Agent身份(S0UL/IDENTITY/USER/MEMORY) + 32个Skills + E:\quant全量代码含_archive
- 排除: __pycache__ / .git / node_modules / *.pyc
- 恢复命令: `tar xzf quantbot_full_2026-05-26.tar.gz -C /`

## 架构前瞻建议 — 未来 FusionController 接入参考（2026-05-21 用户提供）
**来源**: LLMBiasModel 备份讨论中用户提出的两个潜在风险点，当前无需修改，留待扩容/实盘中控时参考。

### 1. EventCache 缓存穿透风险（Cache Stampede）
- **场景**: 多个策略或多个时间周期的进程同时启动，并发请求 `event_cache` 磁盘 JSON
- **当前状态**: 一天只跑一次标的，Dict 序列化缓存够用。7只港股 + 15只美股 = 22标的
- **临界点**: 股票池扩展到 100+ 标的时，磁盘 I/O 锁可能导致小卡顿
- **建议方案**: SQLite 本地表 或 Redis 替代磁盘 JSON 缓存

### 2. LLM 异步并发（Async I/O）
- **场景**: `scan_market()` 是 for 循环同步等待，每个标的串行执行 + time.sleep 限流
- **当前状态**: 10-22只标的没问题
- **临界点**: 50只标的时，预热过程可能需要几分钟
- **建议方案**: asyncio.gather 并发请求 + Semaphore 控制并发量，速度可提升 5x+

## 统一符号格式约定（2026-05-21 新增 ⚠️）
**核心规则：系统内部统一使用 `TICKER.MARKET` 格式（标准符号），Futu API 使用 `MARKET.TICKER`。**
- **标准符号**（系统内）：`AAPL.US`、`00700.HK`、`US.NVDA`
- **Futu 代码**（API层）：`US.AAPL`、`HK.00700`
- **转换函数**：`UniverseManager.to_futu_code()` 和 `.to_standard_symbol()` 通过 `.` 分割后调换顺序
- **关键教训**：config.py 里美股定义使用 `US.AAPL` 格式。新代码（FusionController）必须转换后再使用。UniverseManager 的 `__init__` 负责自动转换。
- **影响范围**：FutuAdapter.fetch_kline()、fetch_quotes()、TradeExecutor 均依赖此格式约定

## FusionController 权重体系（2026-05-21）
**移除所有 Regime-Dependent 动态权重**（BULL/BEAR/CRAB/RECOVERY/CORRECTION x 5组权重全部删除），替换为静态硬编码绝对权重：
- `BASE_WEIGHTS = {'xmm': 0.60, 'vp': 0.25, 'llm': 0.15}`
- **断流降级逻辑**: `_compute_active_weights()` 将异常源权重归零，存活源按比例重新归一化。LLM 断流时 → XMM 70.6% / VP 29.4%
- **影响文件**: `fusion_framework/fusion_engine.py`, `core/fusion_controller.py`

## 平台化架构 — 资产配置解耦 (2026-05-21)
### 核心变化
- **3层适配器架构**: BaseAdapter(抽象) → FutuAdapter/CryptoAdapter(实现) → AdapterFactory(工厂注册表)
- **YAML 配置驱动**: `config/universe_config.yaml` 为单一真相源，`config.py` 的 UNIVERSE_HK/US 为降级方案

### AdapterFactory 用法
```python
# 按 ticker 自动路由
adapter = AdapterFactory.get_adapter('00700.HK')  # → FutuAdapter
adapter = AdapterFactory.get_adapter('BTC.USDT')  # → CryptoAdapter
df = adapter.fetch_kline(symbol, count=252)
```

### 注册新适配器
```python
from core.base_adapter import BaseAdapter
from core.adapter_factory import AdapterFactory

class MyAdapter(BaseAdapter):
    def fetch_kline(self, symbol, count=252, ktype='K_DAY', **kwargs):
        ...

AdapterFactory.register('MyAdapter', MyAdapter)
# 然后在 universe_config.yaml 添加 adapter: MyAdapter
```

### 支持 26 个标的
| 市场 | 数量 | 适配器 | 数据源 |
|------|:----:|--------|--------|
| HK | 7 | FutuAdapter | Futu(primary) / TickFlow(fallback) |
| US | 15 | FutuAdapter | Futu(primary) / TickFlow(fallback) |
| Crypto | 4 | CryptoAdapter | OKX API v5 |

- **CryptoAdapter 注意**: 通过 `shell=True` 调用 `okx` CLI（PS1 包装器），`BTC.USDT` → `BTC-USDT` instId

### 关键文件
- `core/base_adapter.py` — BaseAdapter + NullAdapter
- `config/universe_config.yaml` — 资产配置中心
- `core/adapter_factory.py` — 工厂注册表+缓存
- `core/crypto_adapter.py` — OKX CLI 适配器
- `core/universe_manager.py` — YAML 加载为主
- `core/futu_adapter.py` — 继承 BaseAdapter
- `core/fusion_controller.py` — 使用 AdapterFactory，不再直接持有适配器

## NVIDIA NIM API — 已恢复（2026-05-26）
**修复**：✅ 2026-05-26 11:40 重建 nvidia-api skill（含 scripts/nvidia_api.py），API 测试通过。同时恢复了 futuapi、install-futu-opend、tickflow、nvidia-market-consensus、xmm-strategy、chan-theory 共 6 个 skill。
**NIM_AVAILABLE** = True ✅
