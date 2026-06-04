# Phase E — Live Safety Invariants + CI

**日期**: 2026-06-04
**状态**: 待 Codex 审查后提交

---

## 一、CI 固化的安全边界

以下安全不变量由 `tests/smoke/test_live_safety_invariants.py` 自动验证，
GitHub Actions (`smoke.yml`) 在每次 push/PR 时执行：

| # | 不变量 | 验证方式 | 违反后果 |
|:-:|--------|----------|----------|
| 1 | `scheduled_hk.bat` / `scheduled_us.bat` 不得包含 `--live` | 逐行扫描 | CI FAIL |
| 2 | 定时任务 bat 必须调用 `unified_runner.py` | 检查文件内容 | CI FAIL |
| 3 | 明确列出的活跃执行路径禁止出现 `TrdEnv.REAL` | 递归扫描 5 个目录 | CI FAIL |
| 4 | `core/futu_adapter.py` `place_order` 必须使用 `TrdEnv.SIMULATE` | 函数体内扫描 | CI FAIL |
| 5 | `--help` 必须包含 `--live` 和 `--confirm-live` | 子进程解析 | CI FAIL |
| 6 | `--live` 无确认时 `OrderExecutor` 不实例化 | Mock + pytest | CI FAIL |
| 7 | 文件扫描 helper 能正确识别违规内容 | `tmp_path` 临时文件 + 断言 | CI FAIL |
| 8 | 纯字符串负向检测 | 安全/不安全字符串断言 | CI FAIL |

### 不变量 7 详细说明

提取自文件扫描的辅助函数：
- `scan_py_files(root_dir, prod_paths, excluded_dirs)` → 收集 `.py` 文件列表
- `check_content_in_files(files, pattern, root_dir)` → 扫描内容，读取失败时抛 `RuntimeError`

通过 `tmp_path` 创建含 `TrdEnv.REAL` 和 `--live` 的临时文件，验证 helper 能正确检出，
且安全文件不会被误报。文件读取失败的场景也覆盖在内。

## 二、CI 扫描的活跃执行路径

以下路径是明确列出的活跃执行路径：

```
unified_runner.py
core/
futu_trader/
paper_trading/futu_bridge.py
us_trader/us_pipeline.py
```

### 排除的目录及原因

| 目录 | 排除原因 |
|------|----------|
| `backups/` | 生产基线快照，含旧版本代码，可能含历史 `--live` |
| `_archive/` | 废弃代码归档，不是活跃生产路径 |
| `research/` | 临时研究脚本，含调试性 `TrdEnv.REAL` 引用 |
| `__pycache__/` | 编译缓存 |
| `.git/` | Git 内部数据 |

## 三、CI 运行内容

**`.github/workflows/smoke.yml`**

| 步骤 | 内容 | 说明 |
|------|------|------|
| Checkout | `actions/checkout@v4` / `fetch-depth: 0` | 拉取代码，含范围检查所需历史 |
| Setup Python | `actions/setup-python@v5`, 3.12 | 隔离环境 |
| 安装依赖 | `python -m pip install pytest futu-api pandas numpy` | 最小依赖集；不升级 pip，不隐藏 stderr |
| compileall | `python -m compileall -q unified_runner.py reports/fusion_report_v3.py core` | 语法检查，exit code 严格反映所有文件 |
| pytest | `tests/smoke/ -q` | 全部 smoke tests |
| git show --check / git diff --check | push: `github.event.before...github.sha` ; push (第一次): `git show --check --format= HEAD` ; PR: `base.sha...HEAD` | 尾随空格检测 |
| Summary | 打印失败提示 | 帮助定位 |

### CI 依赖限制

- **`futu-api`**: 仅用于 import 检查，不连接 OpenD。CI 不会启动 Futu 进程。
- **`pandas` / `numpy`**: 部分 smoke tests 使用（如 `guardrails_pure_standalone`），不用于重量级数据处理。
- **不安装**: `scipy`, `scikit-learn`, `tensorflow`, `torch` 等重型依赖。
- `requirements.txt` 包含 `matplotlib`，但 smoke tests 不需要，CI 不安装它。
- 当前 CI 依赖: `pytest`, `futu-api`, `pandas`, `numpy`。
- 如果未来新增 smoke tests 增加依赖，需同步更新 CI `pip install` 行。

### CI shell / stderr 说明

- 所有步骤使用 `pwsh`（PowerShell）
- `pip install` 使用 `python -m pip` 形式，不加 `pip install --upgrade pip`
- 不隐藏 `pip` 的 stderr 输出（`$null` / `2>&1` 等），方便调试

## 四、CI 不能证明什么

以下安全属性不在此 CI 范围内：

- ❌ 交易逻辑正确性（订单金额、仓位计算、止损触发）
- ❌ 信号质量（XMM/VP/LLM 策略的输出是否正确）
- ❌ Gate / confidence 决策逻辑
- ❌ Futu OpenD 连接可用性
- ❌ 真实市场有订单场景的 pipeline 完整性
- ❌ 交易密码安全存储
- ❌ 非 Windows runner 兼容性（CI 使用 `windows-latest`）

## 五、未验证项（仍需人工关注）

来自 Phase D 报告，CI 无法覆盖：

1. **真实 OpenD 有订单路径**: 无法在无 OpenD 的 CI 环境中验证 pre-trade summary、guardrail JSON 的金额一致性。需等市场出现 BUY 信号后手动重演。
2. **执行后账户对称快照**: CI 不连接 OpenD，无法验证执行前后账户余额和持仓。
3. **`--live --confirm-live` 在真实连接上的行为**: CI 只验证 Mock 路径，真实 OrderExecutor 行为需在 SIMULATE 环境中手工验证。

## 六、技术说明

### 安全不变量测试设计原则

- 所有测试都是**纯文件扫描或纯 Mock 测试**，零外部服务连接；CI 仍需安装 Python 依赖（pytest、futu-api 等）
- `futu-api` 在 CI 中仅用于模块 import 有效性，不实例化任何连接
- 负向测试（`TestFileScanHelper` + `TestNegativeDetection`）在纯字符串层面验证检测逻辑，不修改生产文件
- 定时任务 bat 文件作为文本扫描，不执行批处理命令
- 文件读取失败的场景直接抛 `RuntimeError`（不静默 `continue`），确保 CI 能捕获 IO 问题

### 关于 `E:\.github\workflows\smoke.yml` 遗留文件

> ⚠️ 已删除（2026-06-04 Phase E 修正）。

初次写入时路径错误导致 `E:\.github\workflows\smoke.yml` 落盘在项目外（应为 `E:\quant\.github\workflows\smoke.yml`）。
后续已删除，仅保留项目内路径。