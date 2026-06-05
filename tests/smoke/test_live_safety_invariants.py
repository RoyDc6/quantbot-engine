# -*- coding: utf-8 -*-
"""
Phase E — Live Safety Invariants 测试套件。

目标：
    将已确认的安全边界固化为自动测试，防止计划任务偏离
    Roy 授权的 Futu 模拟账户自动执行路径，或在生产路径引入
    TrdEnv.REAL。

覆盖范围：
    1. scheduled_hk.bat / scheduled_us.bat 必须包含 --live --confirm-live
    2. scheduled bat 必须调用 unified_runner.py
    3. 生产路径不得出现 TrdEnv.REAL
    4. core/futu_adapter.py place_order 必须使用 TrdEnv.SIMULATE
    5. unified_runner.py --help 必须包含 --live 和 --confirm-live
    6. --live without confirm → OrderExecutor 不实例化（复用 guardrails 测试）
    7. 排除 backups/、_archive/、research/
    8. 文件扫描 helper 能正确识别违规内容
"""

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

PYTHON = sys.executable
UNIFIED_RUNNER = str(PROJECT_ROOT / 'unified_runner.py')

# ── 安全扫描目录（不扫描 backups/、_archive/、research/） ───────
PROD_PATHS = [
    'unified_runner.py',
    'core/',
    'futu_trader/',
    'paper_trading/futu_bridge.py',
    'us_trader/us_pipeline.py',
]

EXCLUDED_DIRS = {'backups', '_archive', 'research', '__pycache__', '.git'}


# ── 文件扫描 helper（提取为模块级，便于单元测试） ──────────────

def scan_py_files(root_dir: Path, prod_paths: list[str],
                  excluded_dirs: set[str]) -> list[Path]:
    """收集生产路径下的所有 .py 文件，排除备份/归档/研究目录。

    返回按路径排序的文件列表。
    """
    files: list[Path] = []
    for rel in prod_paths:
        full = root_dir / rel
        if full.is_file():
            files.append(full)
        elif full.is_dir():
            for py in full.rglob('*.py'):
                if not any(excl in py.parts for excl in excluded_dirs):
                    files.append(py)
    return sorted(files)


def check_content_in_files(files: list[Path], pattern: str,
                           root_dir: Path) -> list[str]:
    """在文件列表中搜索指定模式，返回含该模式的相对路径列表。

    文件读取失败时直接报错（不静默跳过）。
    """
    violations: list[str] = []
    for pyf in files:
        try:
            text = pyf.read_text(encoding='utf-8', errors='replace')
        except Exception as exc:
            raise RuntimeError(
                f'无法读取文件 {pyf}：{exc}'
            ) from exc
        if pattern in text:
            violations.append(str(pyf.relative_to(root_dir)))
    return violations


# ── 不变量 1－2: 定时任务 bat 文件 ─────────────────────────────

class TestScheduledBatFiles:
    """> scheduled_hk.bat / scheduled_us.bat 自动模拟执行扫描。"""

    BAT_FILES = ['scheduled_hk.bat', 'scheduled_us.bat']

    def _read_bat(self, name: str) -> str:
        path = PROJECT_ROOT / name
        assert path.exists(), f'{name} not found at {path}'
        return path.read_text(encoding='utf-8', errors='replace')

    def test_confirmed_live_flags_present(self):
        """计划任务必须自动进入确认执行路径（当前仍是 Futu SIMULATE）。"""
        for name in self.BAT_FILES:
            content = self._read_bat(name)
            assert '--live' in content, (
                f'{name} 未包含 --live，计划任务不会自动执行模拟订单'
            )
            assert '--confirm-live' in content, (
                f'{name} 未包含 --confirm-live，会被 LIVE_BLOCKED_BY_CONFIRM 拦截'
            )

    def test_calls_unified_runner(self):
        """必须调用 unified_runner.py。"""
        for name in self.BAT_FILES:
            content = self._read_bat(name)
            assert 'unified_runner.py' in content, (
                f'{name} 未调用 unified_runner.py'
            )


# ── 不变量 3: TrdEnv.REAL 禁令 ─────────────────────────────────

class TestTrdEnvRealBan:
    """生产路径不得出现 TrdEnv.REAL。"""

    def _gather_py_files(self) -> list[Path]:
        return scan_py_files(PROJECT_ROOT, PROD_PATHS, EXCLUDED_DIRS)

    def test_no_trd_env_real(self):
        """所有生产路径禁止出现 TrdEnv.REAL。"""
        py_files = self._gather_py_files()
        violations = check_content_in_files(py_files, 'TrdEnv.REAL',
                                            PROJECT_ROOT)
        assert not violations, (
            f'以下文件出现了 TrdEnv.REAL（禁止在非确认实盘路径使用）：\n'
            + '\n'.join(f'  ❌ {v}' for v in violations)
        )


# ── 不变量 4: futu_adapter.py place_order 必须使用 SIMULATE ──

class TestFutuAdapterSimulate:
    """core/futu_adapter.py 的 place_order 必须使用 TrdEnv.SIMULATE。"""

    def test_place_order_uses_simulate(self):
        path = PROJECT_ROOT / 'core' / 'futu_adapter.py'
        assert path.exists(), 'core/futu_adapter.py not found'
        text = path.read_text(encoding='utf-8', errors='replace')

        # 确认 place_order 函数体内存在 TrdEnv.SIMULATE
        lines = text.split('\n')
        in_place_order = False
        found_simulate = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('def place_order('):
                in_place_order = True
                continue
            if in_place_order:
                # 到达下一个 def 或类定义退出
                if stripped.startswith('def ') or stripped.startswith('class '):
                    break
                if 'TrdEnv.SIMULATE' in stripped:
                    found_simulate = True
                    break

        assert found_simulate, (
            'core/futu_adapter.py 的 place_order 函数中未找到 TrdEnv.SIMULATE，'
            '可能被意外改为 REAL！'
        )


# ── 不变量 5: --help 包含实盘参数 ─────────────────────────────

class TestHelpLiveFlags:
    """unified_runner.py --help 必须包含 --live 和 --confirm-live。"""

    def test_confirm_live_in_help(self):
        result = subprocess.run(
            [PYTHON, UNIFIED_RUNNER, '--help'],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=30,
        )
        output = result.stdout + result.stderr
        assert '--live' in output, (
            "--help 中缺少 --live 参数"
        )
        assert '--confirm-live' in output, (
            "--help 中缺少 --confirm-live 参数"
        )


# ── 不变量 6: --live without confirm 的执行路径 ──────────────

class TestLiveWithoutConfirmPath:
    """验证 --live 无确认时的安全执行路径。

    注意: guardrails_pure_standalone.py 是纯函数测试，只能验证 guardrail
    决策逻辑本身正确（execution_mode、should_block），不能证明 OrderExecutor
    是否被实例化。OrderExecutor 不实例化的结论由下面的 Mock 测试
    test_execution_path_mock 通过 mock + assert_not_called() 验证。
    """

    def test_live_no_confirm_blocks_executor(self):
        """子进程运行 guardrails 纯函数，验证决策逻辑正确。"""
        script = PROJECT_ROOT / 'tests' / 'smoke' / 'guardrails_pure_standalone.py'
        result = subprocess.run(
            [PYTHON, str(script)],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=30,
        )
        print(result.stdout[:1000])
        if result.stderr:
            print(f'STDERR: {result.stderr[:500]}')
        assert result.returncode == 0, (
            f"guardrails pure functions failed (rc={result.returncode})\n"
            f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )

    def test_execution_path_mock(self):
        """复用 Phase B 的 mock 路径测试 — 导入已有用例。"""
        from tests.smoke.test_live_guardrails import TestExecutionPath
        test = TestExecutionPath()
        test.test_live_no_confirm_blocked()


# ── 不变量 7: 负向检测 — 文件扫描 helper 测试 ──────────────

class TestFileScanHelper:
    """验证 scan_py_files 和 check_content_in_files 能正确识别违规内容。

    使用 pytest tmp_path 创建临时目录和文件，不污染生产环境。
    """

    def test_detect_trd_env_real_in_temp_file(self, tmp_path: Path):
        """在 tmp_path 中创建含 TrdEnv.REAL 的文件，验证能被检出。"""
        # 创建临时目录结构
        target = tmp_path / 'core'
        target.mkdir()
        f = target / 'evil.py'
        f.write_text(
            'trade_env = TrdEnv.REAL\n',
            encoding='utf-8',
        )

        files = scan_py_files(
            tmp_path, ['core/'], EXCLUDED_DIRS,
        )
        violations = check_content_in_files(files, 'TrdEnv.REAL', tmp_path)

        assert len(violations) == 1, (
            f'应检出 1 个违规文件，实际检出 {len(violations)}'
        )
        assert str(Path('core') / 'evil.py') in violations[0], (
            f'违规文件应为 {Path("core") / "evil.py"}，实际为 {violations[0]}'
        )

    def test_detect_live_in_temp_file(self, tmp_path: Path):
        """在 tmp_path 中创建含 --live 的文件，验证能被检出。"""
        f = tmp_path / 'script.bat.txt'
        f.write_text(
            'python unified_runner.py --market HK --live\n',
            encoding='utf-8',
        )

        # 使用该文件作为 prod path
        files = scan_py_files(
            tmp_path, ['script.bat.txt'], EXCLUDED_DIRS,
        )
        violations = check_content_in_files(files, '--live', tmp_path)

        assert len(violations) == 1, (
            f'应检出 1 个违规文件，实际检出 {len(violations)}'
        )

    def test_clean_file_has_no_violations(self, tmp_path: Path):
        """安全文件不应被误报。"""
        target = tmp_path / 'core'
        target.mkdir()
        f = target / 'safe.py'
        f.write_text(
            'trade_env = TrdEnv.SIMULATE\n',
            encoding='utf-8',
        )

        files = scan_py_files(
            tmp_path, ['core/'], EXCLUDED_DIRS,
        )
        violations = check_content_in_files(files, 'TrdEnv.REAL', tmp_path)

        assert len(violations) == 0, (
            f'安全文件不应被检出违规，但找到 {violations}'
        )

    def test_read_failure_raises(self, tmp_path: Path):
        """文件读取失败必须报错，不能静默跳过。"""
        target = tmp_path / 'broken'
        target.mkdir()
        # 创建一个目录伪装成 .py 文件（使 read_text 失败）
        fake_py = target / 'unreadable.py'
        fake_py.mkdir()  # 目录，不是文件，read_text 会报错

        files = scan_py_files(
            tmp_path, ['broken/'], EXCLUDED_DIRS,
        )
        with pytest.raises(RuntimeError, match='无法读取文件'):
            check_content_in_files(files, 'TrdEnv.REAL', tmp_path)


# ── 不变量 8: 负向检测 — 纯字符串断言 ──────────────────────

class TestNegativeDetection:
    """验证检测逻辑能正确识别违规文件（用于测试测试本身）。"""

    def test_detect_trd_env_real_in_memory(self):
        """在纯字符串中搜索 TrdEnv.REAL 应能命中。"""
        safe_text = 'trd_env = TrdEnv.SIMULATE'
        unsafe_text = 'trd_env = TrdEnv.REAL'

        assert 'TrdEnv.REAL' not in safe_text, '安全文本不应包含 REAL'
        assert 'TrdEnv.REAL' in unsafe_text, '检测函数应识别 REAL'

    def test_detect_live_in_memory(self):
        """在纯字符串中搜索 --live 应能命中。"""
        safe_text = 'python unified_runner.py --market HK'
        unsafe_text = 'python unified_runner.py --market HK --live'

        assert '--live' not in safe_text, '安全文本不应包含 --live'
        assert '--live' in unsafe_text, '检测函数应识别 --live'
