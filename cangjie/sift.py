"""sift（筛）：上下文安检——检验一段上下文里什么有用、什么没用。

与 trim 的区别：
    trim   预算驱动：超出 max_lines 就砍，有损，目标是"短"
    sift   证据驱动：逐行给出判定（证据 / 中性 / 噪音），目标是"不丢有用的"

安全铁律：**保留所有有用的**。
    只有高置信噪音（进度行 / 分隔线 / 空白 / 连续重复）才允许被 --drop 删掉；
    任何拿不准的行一律保留。宁可少省，不可错杀——所以默认只报告，原文不动。

判定信号（keep = 至少命中一条保留证据）：
    错误/警告    复用 trim 的 ERROR_HINT / WARN_HINT
    指标         数字 + 单位（% / ms / 秒 / tok / MB / qps / 倍 / 元 …）
    路径与 URL   C:\\x\\y、src/config.yaml、https://
    决策与计划   决定 / 结论 / 下一步 / blocker / TODO / 风险 …
    日期与版本   2026-10-31、v1.0.1
    术语命中     命中字典短语（含 seed 锁定术语）——专名永不丢
    代码块       围栏内整段保留（与"代码块不压"同一条铁律）

噪音（唯一会被删的）：
    进度行（NOISE）、纯分隔线/横幅、空白行、连续重复 ≥3 的行（只留一份）
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .trim import ERROR_HINT, NOISE, WARN_HINT

# 数字 + 单位（指标是"决策需要"的最强信号之一）
METRIC = re.compile(
    r"\d(?:\.\d+)?\s*(?:%|ms|秒|毫秒|分钟|小时|min\b|GB|MB|KB|TB|"
    r"tok(?:en)?s?|rps|qps|倍|元|美元)",
    re.IGNORECASE,
)
PATH_LIKE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s|]+"
    r"|/?(?:usr|var|etc|home|opt|tmp|Users|root|src|tests?|docs?|cangjie)/[\w./\\-]+"
    r"|[\w./\\-]+\.(?:py|js|ts|tsx|jsx|json|ya?ml|toml|md|txt|log|cfg|ini|sh|ps1|html|css|sql)\b)"
)
URL_RE = re.compile(r"https?://")
DECISION = re.compile(
    r"(?i)\b(decided|decision|todo|fixme|next step|blocker|risk|conclusion)\b"
    r"|决定|已决|结论|下一步|阻塞|风险|待办|注意"
)
DATE_VERSION = re.compile(
    r"\b20\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b"
    r"|\bv\d+\.\d+(?:\.\d+)?\b"
)
SEP_LINE = re.compile(r"^\s*[-=_*#~=·—–\s]{4,}$")


@dataclass
class Verdict:
    no: int          # 1 起始行号
    text: str
    tag: str         # "keep"（有证据）| "neutral"（无证据也不噪音）| "noise" | "dup"
    reason: str      # 证据名（+ 连接）或噪音类型


@dataclass
class SiftStats:
    lines: int = 0
    keep: int = 0          # 有证据的行
    neutral: int = 0       # 无证据也不噪音（默认保留）
    noise: int = 0         # 高置信噪音行
    dup: int = 0           # 其中被折叠的重复行
    fenced: int = 0        # 代码块内的行
    chars_before: int = 0
    chars_after: int = 0
    noise_chars: int = 0   # 噪音行占用的字符数

    @property
    def ratio(self) -> float:
        if self.chars_before <= 0:
            return 0.0
        return min(1.0, self.noise_chars / self.chars_before)

    def summary(self) -> str:
        return (f"{self.lines} 行：证据 {self.keep} · 中性 {self.neutral} · "
                f"噪音 {self.noise}（重复 {self.dup}）· 代码块 {self.fenced} 行 · "
                f"噪音约占 {self.ratio * 100:.0f}% 字符")


def _evidence(line: str, terms: tuple[str, ...]) -> str:
    found: list[str] = []
    if terms and any(t and t in line for t in terms):
        found.append("术语命中")
    if ERROR_HINT.search(line):
        found.append("错误")
    if WARN_HINT.search(line):
        found.append("警告")
    if METRIC.search(line):
        found.append("指标")
    if PATH_LIKE.search(line) or URL_RE.search(line):
        found.append("路径")
    if DECISION.search(line):
        found.append("决策")
    if DATE_VERSION.search(line):
        found.append("日期/版本")
    return "+".join(found)


def _mark_dups(vs: list[Verdict]) -> None:
    """连续重复 ≥3 的行：只留第一份，其余标 dup（内容完全相同，删了不丢信息）。"""
    i, n = 0, len(vs)
    while i < n:
        j = i + 1
        while j < n and vs[j].text == vs[i].text:
            j += 1
        run = j - i
        if run >= 3 and vs[i].tag in ("keep", "neutral"):
            for k in range(i + 1, j):
                vs[k].tag, vs[k].reason = "dup", f"重复×{run}"
        i = j


def verdicts(text: str, terms: Iterable[str] = ()) -> list[Verdict]:
    """逐行判定。terms 传字典短语（含锁定术语），命中即视为证据行。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    terms_t = tuple(terms)
    vs: list[Verdict] = []
    in_fence = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith("```"):
            in_fence = not in_fence
            vs.append(Verdict(i + 1, ln, "keep", "代码块"))
        elif in_fence:
            vs.append(Verdict(i + 1, ln, "keep", "代码块"))
        else:
            vs.append(Verdict(i + 1, ln, "", ""))

    for v in vs:
        if v.tag:                      # 代码块内已保护
            continue
        if not v.text.strip():
            v.tag, v.reason = "noise", "空白"
        elif SEP_LINE.match(v.text):
            v.tag, v.reason = "noise", "分隔线"
        elif NOISE.match(v.text):
            v.tag, v.reason = "noise", "进度"
        else:
            ev = _evidence(v.text, terms_t)
            v.tag, v.reason = ("keep", ev) if ev else ("neutral", "—")
    _mark_dups(vs)
    return vs


def sift(text: str, drop: bool = False,
         terms: Iterable[str] = ()) -> tuple[str, SiftStats]:
    """检验上下文。

    drop=False：只判定不改动，返回 (原文, 统计)。
    drop=True ：只删高置信噪音（noise/dup），缺口处标注省略行数；
                证据行与中性行一律保留——"保留所有有用的"是硬约束。
    """
    vs = verdicts(text, terms)
    st = SiftStats(lines=len(vs), chars_before=len(text),
                   keep=sum(1 for v in vs if v.tag == "keep" and v.reason != "代码块"),
                   neutral=sum(1 for v in vs if v.tag == "neutral"),
                   noise=sum(1 for v in vs if v.tag == "noise"),
                   dup=sum(1 for v in vs if v.tag == "dup"),
                   fenced=sum(1 for v in vs if v.reason == "代码块"),
                   noise_chars=sum(len(v.text) + 1 for v in vs
                                   if v.tag in ("noise", "dup")))
    if not drop:
        st.chars_after = st.chars_before
        return text, st

    out: list[str] = []
    gap = 0
    for v in vs:
        if v.tag in ("noise", "dup"):
            gap += 1
            continue
        if gap and out:                      # 段首/段尾的噪音不标，免得白占两行
            out.append(f"……[sift 略去 {gap} 行噪音]……")
        gap = 0
        out.append(v.text)
    body = "\n".join(out)
    st.chars_after = len(body)
    return body, st


def report(text: str, terms: Iterable[str] = (), show: int = 20) -> str:
    """人类可读的安检报告。只报告，不改原文。"""
    vs = verdicts(text, terms)
    st = SiftStats(lines=len(vs), chars_before=len(text),
                   keep=sum(1 for v in vs if v.tag == "keep" and v.reason != "代码块"),
                   neutral=sum(1 for v in vs if v.tag == "neutral"),
                   noise=sum(1 for v in vs if v.tag == "noise"),
                   dup=sum(1 for v in vs if v.tag == "dup"),
                   fenced=sum(1 for v in vs if v.reason == "代码块"),
                   noise_chars=sum(len(v.text) + 1 for v in vs
                                   if v.tag in ("noise", "dup")))
    lines = [f"[仓颉sift] {st.summary()}"]
    noise = [v for v in vs if v.tag in ("noise", "dup")]
    if noise:
        lines.append(f"可省行明细（{len(noise)} 行，显示前 {min(show, len(noise))} 条）：")
        for v in noise[: max(0, show)]:
            body = v.text if len(v.text) <= 60 else v.text[:57] + "..."
            lines.append(f"  L{v.no} [{v.reason}] {body}")
    else:
        lines.append("未发现高置信噪音行。")
    lines.append("原文未改动；确认后可加 --drop 生成筛后文本（拿不准的行一律保留）。")
    return "\n".join(lines)


__all__ = ["Verdict", "SiftStats", "verdicts", "sift", "report"]
