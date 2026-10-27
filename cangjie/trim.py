"""输出裁剪（trim）：比字码高一个数量级的那一刀。

实测：一次全量 pytest 输出约 12k token，裁剪后 ~800 token（-93%）。
字码最多省 4%，所以**这里才是省钱的主战场**，字码是打底。

与 codec 的区别：
    codec  无损，只换写法，可还原
    trim   有损，只留"决策需要的那几行"，不承诺还原

设计：按命令类型套用"保留策略"（profile），再统一做
  1) 头尾保留  2) 错误行及其上下文  3) 重复行折叠  4) 缺口标注
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 一行里出现这些词，大概率是"要看的那行"
ERROR_HINT = re.compile(
    r"(?i)\b(error|exception|fail|failed|failure|fatal|panic|traceback|"
    r"assert|denied|refused|timeout|timed out|not found|no such|undefined|"
    r"cannot|could not|unable|invalid|segfault|crash|✗|✘|×)\b|错误|失败|异常|报错|超时|拒绝|不存在"
)
WARN_HINT = re.compile(r"(?i)\b(warn|warning|deprecated)\b|警告|弃用")
# 纯进度/噪音行
NOISE = re.compile(
    r"^[\s.·*\-=+#>%\[\]()0-9]*$|^\s*(Downloading|Collecting|Installing|Building|"
    r"Resolving|Fetching|Progress|Compiling|Linking)\b|^\s*\d+%\s*\|"
    # 单条用例的"通过"进度行，几百行全留着没有信息量
    r"|::\S+\s+(PASSED|passed|ok)\s+\[\s*\d+%\]|^\s*(✓|✔|√)\s+\S+"
)


@dataclass
class TrimStats:
    lines_before: int = 0
    lines_after: int = 0
    chars_before: int = 0
    chars_after: int = 0
    profile: str = "generic"
    collapsed: int = 0
    dropped: int = 0

    @property
    def ratio(self) -> float:
        if self.chars_before <= 0:
            return 0.0
        return max(0.0, 1.0 - self.chars_after / self.chars_before)

    def summary(self) -> str:
        return (f"{self.lines_before}→{self.lines_after} 行 / "
                f"{self.chars_before}→{self.chars_after} 字符（省 {self.ratio * 100:.0f}%）"
                f" · profile={self.profile}"
                f" · 折叠重复 {self.collapsed} · 丢弃 {self.dropped}")


# ---------------------------------------------------------------- 命令识别

PROFILE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("pytest", ("pytest", "py.test", "python -m pytest")),
    ("jest", ("jest", "vitest", "mocha")),
    ("npm", ("npm", "pnpm", "yarn", "bun")),
    ("git-diff", ("git", "diff")),
    ("docker", ("docker", "kubectl", "podman")),
    ("ls", ("ls", "find", "dir", "tree", "rg", "grep")),
    ("build", ("make", "cmake", "cargo", "go", "gradle", "mvn", "tsc", "webpack", "vite")),
]


def detect_profile(cmd: list[str] | str) -> str:
    text = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
    low = text.lower()
    for name, keys in PROFILE_RULES:
        if any(k in low for k in keys):
            return name
    return "generic"


# 每个 profile 的保留行规则（返回 True 表示保留）
_PYTEST_KEEP = re.compile(
    r"^\s*(FAILED|ERROR|XFAIL)\b"
    r"|^E\s"
    r"|^\s*assert\b"
    r'|^\s*File "'
    r"|^\s*>{3}"
    r"|short test summary"
    r"|=+ .*(failed|error)"
    r"|^\s*\^+\s*$",
    re.IGNORECASE,
)


def _keep_pytest(line: str) -> bool:
    """只挑"要看的那几行"：失败、错误、断言、traceback、汇总。

    通过行（PASSED）由 NOISE 先滤掉，否则几百行全被当成"要看的行"，
    优先级机制失效，最后只剩开头几条 PASSED。
    """
    return bool(_PYTEST_KEEP.search(line) or ERROR_HINT.search(line))


def _keep_jest(line: str) -> bool:
    return bool(re.search(r"(?i)(✕|✓|●|FAIL|PASS|Expected|Received|at .*:\d+:\d+)", line)
                or ERROR_HINT.search(line))


def _keep_npm(line: str) -> bool:
    return bool(ERROR_HINT.search(line) or WARN_HINT.search(line)
                or re.search(r"(?i)added \d+ packages|removed \d+|up to date", line))


def _keep_git_diff(line: str) -> bool:
    return bool(re.match(r"^(diff --git|@@|\+\+\+|---|index )", line))


def _keep_docker(line: str) -> bool:
    return bool(ERROR_HINT.search(line) or WARN_HINT.search(line))


def _keep_build(line: str) -> bool:
    return bool(ERROR_HINT.search(line) or WARN_HINT.search(line)
                or re.search(r"(?i)^\s*(error|warning)\b", line))


KEEPERS = {
    "pytest": _keep_pytest,
    "jest": _keep_jest,
    "npm": _keep_npm,
    "git-diff": _keep_git_diff,
    "docker": _keep_docker,
    "build": _keep_build,
    "ls": lambda _l: False,  # 目录类只靠头尾，不挑"错误行"
    "generic": lambda l: bool(ERROR_HINT.search(l) or WARN_HINT.search(l)),
}


# ---------------------------------------------------------------- 主流程

def _collapse_repeats(lines: list[str]) -> tuple[list[str], int]:
    """把连续重复行折成 `line  (×N)`。"""
    out: list[str] = []
    collapsed = 0
    i = 0
    n = len(lines)
    while i < n:
        j = i + 1
        while j < n and lines[j] == lines[i]:
            j += 1
        run = j - i
        if run >= 3:
            out.append(f"{lines[i]}  (×{run})")
            collapsed += run - 1
        else:
            out.extend(lines[i:j])
        i = j
    return out, collapsed


def trim(
    text: str,
    profile: str = "auto",
    keep_head: int = 6,
    keep_tail: int = 12,
    max_lines: int = 80,
    error_context: int = 2,
) -> tuple[str, TrimStats]:
    """裁剪命令输出。返回 (裁剪后文本, 统计)。

    选择逻辑按**优先级**而不是位置：
        3 错误行及其上下文  >  2 尾部（结论/汇总）  >  1 头部（命令与起点）
    超出行数上限时先牺牲头部，绝不牺牲错误行——这是"裁剪"和"截断"的区别。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    st = TrimStats(chars_before=len(text))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    st.lines_before = len(lines)
    if profile == "auto":
        profile = "generic"
    st.profile = profile
    keeper = KEEPERS.get(profile, KEEPERS["generic"])

    if st.lines_before <= max_lines:
        st.lines_after = st.lines_before
        st.chars_after = len(text)
        return text, st

    prio: dict[int, int] = {}
    for i in range(min(keep_head, len(lines))):
        prio[i] = 1
    for i in range(max(0, len(lines) - keep_tail), len(lines)):
        prio[i] = 2
    for i, ln in enumerate(lines):
        if not ln.strip() or NOISE.match(ln):
            continue
        if keeper(ln):
            lo = max(0, i - error_context)
            hi = min(len(lines), i + error_context + 1)
            for k in range(lo, hi):
                prio[k] = 3

    if len(prio) > max_lines:
        ranked = sorted(prio.items(), key=lambda kv: (-kv[1], kv[0]))[:max_lines]
        prio = dict(ranked)

    keep = sorted(prio)
    # 按原序输出，缺口处标注省略行数（保留"哪里被跳过"的信息）
    out: list[str] = []
    prev = -1
    for i in keep:
        if i - prev > 1:
            gap = i - prev - 1
            out.append(f"……[省略 {gap} 行]……")
        out.append(lines[i])
        prev = i
    if prev < len(lines) - 1:
        out.append(f"……[省略 {len(lines) - 1 - prev} 行]……")

    out, collapsed = _collapse_repeats(out)
    body = "\n".join(out)
    st.lines_after = len(out)
    st.chars_after = len(body)
    st.collapsed = collapsed
    st.dropped = st.lines_before - len(keep)
    return body, st


def trim_file(path: str, **kw) -> tuple[str, TrimStats]:
    with open(path, encoding="utf-8", errors="replace") as f:
        return trim(f.read(), **kw)


__all__ = ["trim", "trim_file", "detect_profile", "TrimStats", "PROFILE_RULES"]
