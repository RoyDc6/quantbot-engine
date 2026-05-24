# 缠论量化系统架构设计

> 创建时间：2026-04-14
> 状态：待确认

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      数据层（已有）                           │
│  FutuDataFetcher → DataFrame(date, OHLCV) → 缓存             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   缠论结构识别引擎                            │
├─────────────────────────────────────────────────────────────┤
│  K线数据 → 分型识别 → 包含处理 → 笔划分 → 线段 → 中枢 → 信号  │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                      输出层                                  │
│  结构数据 → 可视化报告 → 交易信号 → 回测系统（已有）          │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 模块职责

### 2.1 分型识别 (`fractal.py`)

**输入**：原始K线 DataFrame

**输出**：标记分型的 DataFrame，新增列：
- `fractal`: 1=顶分型, -1=底分型, 0=无
- `fractal_high`: 顶分型高点（仅顶分型有值）
- `fractal_low`: 底分型低点（仅底分型有值）

**核心逻辑**：
```python
顶分型定义：
  high[i-1] < high[i] > high[i+1]
  且 low[i-1] < low[i] 或 low[i] > low[i+1]

底分型定义：
  low[i-1] > low[i] < low[i+1]
  且 high[i-1] > high[i] 或 high[i] < high[i+1]
```

**依赖**：无（最底层）

---

### 2.2 包含关系处理 (`merge.py`)

**输入**：原始K线 + 分型标记

**输出**：合并后的K线 + 映射关系

**核心逻辑**：
```
包含关系判断：
  K线A和K线A+1有包含关系当：
    high_A <= high_{A+1} 且 low_A >= low_{A+1}  （A被包含）
    或
    high_A >= high_{A+1} 且 low_A <= low_{A+1}  （A+1被包含）

处理方向：
  向上处理（前K线低点 < 更前K线低点）：取两K线高点max、低点max
  向下处理（前K线低点 > 更前K线低点）：取两K线高点min、低点min
```

**难点**：合并后需要重新检测分型，可能级联影响

**输出数据**：
- 合并后的K线序列（长度 ≤ 原始长度）
- 原始索引 → 合并后索引的映射表

---

### 2.3 笔划分 (`stroke.py`)

**输入**：合并后K线 + 分型标记

**输出**：笔的列表

```python
class Stroke:
    start_idx: int       # 起点索引（合并后）
    end_idx: int         # 终点索引（合并后）
    direction: int       # 1=向上, -1=向下
    start_price: float   # 起点价格
    end_price: float     # 终点价格
    high: float          # 笔内最高
    low: float           # 笔内最低
```

**核心规则**：
```
笔的构成：
  顶分型 → 底分型 = 向下笔（至少5根K线）
  底分型 → 顶分型 = 向上笔（至少5根K线）

破坏判断：
  新笔必须破坏前笔的极端点才能确立
```

---

### 2.4 线段划分 (`segment.py`)

**输入**：笔的列表

**输出**：线段的列表

```python
class Segment:
    start_idx: int       # 起笔索引
    end_idx: int         # 终笔索引
    direction: int       # 1=向上, -1=向下
    strokes: List[Stroke]  # 包含的笔
    high: float          # 线段最高
    low: float           # 线段最低
```

**核心规则**：
```
线段定义：
  至少3笔构成
  方向由第一笔决定

破坏规则：
  新线段必须破坏前线段（突破前极端点）
```

---

### 2.5 中枢识别 (`pivot.py`)

**输入**：线段的列表

**输出**：中枢的列表

```python
class Pivot:
    start_idx: int       # 起始线段索引
    end_idx: int         # 结束线段索引
    zg: float            # 中枢高点 ZG = min(线段高点)
    zd: float            # 中枢低点 ZD = max(线段低点)
    gg: float            # 中枢最高 GG = max(线段高点)
    dd: float            # 中枢最低 DD = min(线段低点)
    level: int           # 中枢级别
```

**核心规则**：
```
中枢定义：
  至少3个连续线段的重叠区间
  ZG = min(线段高点)
  ZD = max(线段低点)

中枢延伸：
  新线段的高低点在中枢区间内，中枢延伸
  超出区间，中枢新生或扩展
```

---

### 2.6 买卖点信号 (`signals.py`)

**输入**：笔 + 线段 + 中枢

**输出**：买卖信号

```python
class ChanSignal:
    date: datetime       # 信号日期
    signal_type: str     # 'buy1', 'buy2', 'buy3', 'sell1', 'sell2', 'sell3'
    price: float         # 信号价格
    strength: int        # 信号强度 1-5
    reason: str          # 信号原因描述
```

**信号定义**：
```
一类买卖点（趋势转折）：
  buy1:  下跌趋势中，底分型+背驰
  sell1: 上涨趋势中，顶分型+背驰

二类买卖点（中枢确认）：
  buy2:  一买后，次级别回踩不破一买点
  sell2: 一卖后，次级别反弹不破一卖点

三类买卖点（中枢突破）：
  buy3:  突破中枢后，回踩不回中枢
  sell3: 跌破中枢后，反弹不回中枢
```

---

### 2.7 可视化 (`visualize.py`)

**功能**：
- K线图（蜡烛图）
- 标注分型（顶/底标记）
- 连接笔（线段连接分型点）
- 绘制中枢（矩形框）
- 标记买卖点（箭头）

**技术选型**：
- 方案A：mplfinance（轻量，静态图，适合报告）
- 方案B：plotly（交互式，适合探索）
- 方案C：echarts（Web展示，适合生产）

**建议**：先用 mplfinance 快速验证，后期再优化

---

## 3. 数据流图

```
原始K线 (DataFrame)
    │
    ├──────────────────┐
    ▼                  ▼
  分型识别          包含关系处理
    │                  │
    └──────┬───────────┘
           ▼
      合并K线 + 分型
           │
           ▼
         笔划分
           │
           ▼
         线段划分
           │
           ▼
         中枢识别
           │
           ▼
         买卖信号
           │
           ▼
       可视化输出
```

---

## 4. 与现有框架集成

### 4.1 数据接口

**输入**：复用 `FutuDataFetcher.get_kline()`

```python
from data_fetcher import FutuDataFetcher

fetcher = FutuDataFetcher()
df = fetcher.get_kline("HK.00700", "2024-01-01", "2026-04-13")
```

### 4.2 信号输出格式

兼容现有 `signals.py` 的输出格式：

```python
{
    "bucket": "structural",      # 分桶
    "position_pct": 0.15,        # 仓位建议
    "direction": "BULL_BREAK",   # 方向
    "reason": "一买信号+背驰确认", # 原因
    "vix_regime": "RELEASED",    # VIX环境
}
```

### 4.3 回测集成

在 `backtester.py` 中增加缠论信号源：

```python
from chan.signals import compute_chan_signals

# 计算信号时
sig = compute_chan_signals(df)
trading_signal = generate_chan_trading_signal(symbol, sig, vix_regime)
```

---

## 5. 文件结构

```
E:\quant\
├── chan/                     # 缠论模块（新建）
│   ├── __init__.py           # 模块入口
│   ├── ARCHITECTURE.md       # 本文档
│   ├── fractal.py            # 分型识别
│   ├── merge.py              # 包含关系处理
│   ├── stroke.py             # 笔划分
│   ├── segment.py            # 线段划分
│   ├── pivot.py              # 中枢识别
│   ├── signals.py            # 买卖点信号
│   ├── visualize.py          # 可视化
│   └── tests/                # 单元测试
│       ├── test_fractal.py
│       ├── test_merge.py
│       └── test_stroke.py
├── data_fetcher.py           # 现有
├── signals.py                # 现有
├── backtester.py             # 现有
└── config.py                 # 现有
```

---

## 6. 开发优先级

### Phase 1: 基础识别（1-2天）
1. `fractal.py` - 分型识别（核心）
2. `merge.py` - 包含关系处理（最复杂）
3. `stroke.py` - 笔划分
4. 测试：用 HK.00700 验证

### Phase 2: 中枢与线段（1-2天）
5. `segment.py` - 线段划分
6. `pivot.py` - 中枢识别
7. 测试：多标的中枢识别

### Phase 3: 信号与可视化（1-2天）
8. `signals.py` - 买卖点信号
9. `visualize.py` - K线+结构可视化
10. 集成到回测系统

---

## 7. 待确认问题

### Q1: 是否需要多级别递归？
- 选项A：单级别（日线），简化实现
- 选项B：多级别递归（日线→30分→5分），完整实现

**建议**：先做单级别，验证通过后再扩展

### Q2: 包含关系处理策略？
- 选项A：严格按缠论原文（复杂）
- 选项B：简化版（合并后重新检测分型）

**建议**：先用简化版，后期再优化

### Q3: 可视化技术选型？
- 选项A：mplfinance（静态图）
- 选项B：plotly（交互式）

**建议**：先用 mplfinance 快速验证

### Q4: 是否需要实时更新？
- 选项A：仅历史数据回测
- 选项B：支持实时K线更新

**建议**：先做历史数据，实时更新后续扩展

---

## 8. 下一步行动

确认以上设计后：
1. 创建 `chan/` 目录结构
2. 实现 `fractal.py` 分型识别
3. 用 HK.00700 数据测试验证
4. 逐步推进后续模块

---

**等待确认后开始实现。**
