"""结构化工作记忆：状态卡 + 滚动摘要 + 定长工作记忆包。

三件东西解决"越跑越贵"：

    state.json   3–5 个活变量（目标/阻塞/已决/下一步/事实），约 150–250 token，
                 顶替几千 token 的对话历史。**它是压缩率最高的部件。**
    roll.jsonl   只追加的滚动摘要（每轮把结论压一行丢进去），不重写历史。
    pack.md      把「密码本 + 状态卡 + 滚动摘要」拼成**固定预算**的一块，
                 每 5–8 轮重建一次，替换掉冗长的历史叙述。

为什么不是"每次都摘要整段对话"：那是 O(全历史) 的重写，本身就贵。
滚动追加是 O(本轮)，成本与轮数无关。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .config import Config, state_dir, load_config
from .state import Dictionary
from .tokenizer import Counter

STATE_NAME = "state.json"
ROLL_NAME = "roll.jsonl"
PACK_NAME = "pack.md"
MAX_ROLL_CHUNKS = 40


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _mins(text: str, n: int) -> str:
    """把一行压到 n 字以内（按句子切）。"""
    text = " ".join(text.split())
    if len(text) <= n:
        return text
    cut = text[:n]
    for sep in "。；;.!":
        pos = cut.rfind(sep)
        if pos > n * 0.5:
            return cut[: pos + 1]
    return cut.rstrip() + "…"


# ---------------------------------------------------------------- 状态卡

@dataclass
class State:
    goal: str = ""
    blocker: str = ""
    decided: list[str] = field(default_factory=list)
    next: list[str] = field(default_factory=list)
    facts: dict[str, str] = field(default_factory=dict)
    updated: str = ""

    @classmethod
    def load(cls, root: Path | None = None) -> "State":
        p = state_dir(root) / STATE_NAME
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            goal=raw.get("goal", ""),
            blocker=raw.get("blocker", ""),
            decided=list(raw.get("decided", [])),
            next=list(raw.get("next", [])),
            facts=dict(raw.get("facts", {})),
            updated=raw.get("updated", ""),
        )

    def save(self, root: Path | None = None) -> Path:
        self.updated = _now()
        p = state_dir(root) / STATE_NAME
        p.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    # ---- 变更 ----
    def set(self, key: str, value: str) -> None:
        if key in ("goal", "blocker"):
            setattr(self, key, value)
        elif key == "decided":
            self.decided = [value]
        elif key == "next":
            self.next = [value]
        elif key.startswith("fact."):
            self.facts[key[5:]] = value
        else:
            self.facts[key] = value

    def add(self, key: str, value: str) -> None:
        if key not in ("decided", "next"):
            key = "facts"
        if key == "facts":
            if "=" in value:
                k, v = value.split("=", 1)
                self.facts[k.strip()] = v.strip()
            return
        bucket: list[str] = getattr(self, key)
        if value not in bucket:
            bucket.append(value)
        # 只留最近 8 条，防止状态卡自己膨胀
        if len(bucket) > 8:
            del bucket[0: len(bucket) - 8]

    def drop(self, key: str, value: str = "") -> bool:
        if key in ("decided", "next"):
            bucket: list[str] = getattr(self, key)
            if value:
                before = len(bucket)
                bucket[:] = [x for x in bucket if value not in x]
                return len(bucket) != before
            setattr(self, key, [])
            return True
        if key in ("goal", "blocker"):
            setattr(self, key, "")
            return True
        if key in self.facts:
            self.facts.pop(key, None)
            return True
        return False

    @property
    def empty(self) -> bool:
        return not (self.goal or self.blocker or self.decided or self.next or self.facts)

    # ---- 渲染 ----
    def render(self, width: int = 60) -> str:
        if self.empty:
            return ""
        out = ["【状态卡】"]
        if self.goal:
            out.append(f"目标：{_mins(self.goal, width)}")
        if self.blocker:
            out.append(f"阻塞：{_mins(self.blocker, width)}")
        if self.decided:
            out.append("已决：" + "；".join(_mins(x, width // 2) for x in self.decided[-6:]))
        if self.next:
            out.append("下一步：" + "；".join(_mins(x, width // 2) for x in self.next[-6:]))
        if self.facts:
            pairs = list(self.facts.items())[:12]
            out.append("事实：" + "，".join(f"{k}={_mins(v, 24)}" for k, v in pairs))
        return "\n".join(out)

    def to_table(self) -> list[list[str]]:
        rows = []
        if self.goal:
            rows.append(["goal", self.goal[:60]])
        if self.blocker:
            rows.append(["blocker", self.blocker[:60]])
        for i, v in enumerate(self.decided):
            rows.append([f"decided[{i}]", v[:60]])
        for i, v in enumerate(self.next):
            rows.append([f"next[{i}]", v[:60]])
        for k, v in self.facts.items():
            rows.append([f"fact.{k}", v[:60]])
        return rows


# ---------------------------------------------------------------- 滚动摘要

@dataclass
class Chunk:
    ts: str
    tag: str
    text: str

    def line(self, width: int = 100) -> str:
        tag = f"{self.tag}: " if self.tag else ""
        return f"- {tag}{_mins(self.text, width)}"


class Roll:
    def __init__(self, root: Path | None = None):
        self.path = state_dir(root) / ROLL_NAME
        self.chunks: list[Chunk] = self._read()

    def _read(self) -> list[Chunk]:
        if not self.path.exists():
            return []
        out: list[Chunk] = []
        for ln in self.path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            out.append(Chunk(ts=d.get("ts", ""), tag=d.get("tag", ""),
                             text=d.get("text", "")))
        return out

    def add(self, text: str, tag: str = "") -> Chunk:
        c = Chunk(ts=_now(), tag=tag, text=" ".join(text.split()))
        self.chunks.append(c)
        self._rewrite()
        return c

    def _rewrite(self) -> None:
        if len(self.chunks) > MAX_ROLL_CHUNKS:
            self.chunks = self.chunks[-MAX_ROLL_CHUNKS:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            for c in self.chunks:
                f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")

    def clear(self) -> int:
        n = len(self.chunks)
        self.chunks = []
        self._rewrite()
        return n

    def render(self, budget_tokens: int | None = None, counter: Counter | None = None,
               width: int = 100) -> str:
        """从**最新**往前取，直到超出预算——旧结论先被牺牲。"""
        if not self.chunks:
            return ""
        lines = [c.line(width) for c in self.chunks]
        if budget_tokens is None or counter is None:
            return "【滚动结论】\n" + "\n".join(lines)
        keep: list[str] = []
        used = counter.count("【滚动结论】")
        for ln in reversed(lines):
            cost = counter.count(ln) + 0.5
            if used + cost > budget_tokens:
                break
            keep.append(ln)
            used += cost
        keep.reverse()
        if not keep:
            return ""
        return "【滚动结论】\n" + "\n".join(keep)


# ---------------------------------------------------------------- 打包

def build_pack(
    root: Path | None,
    cfg: Config | None = None,
    dic: Dictionary | None = None,
    counter: Counter | None = None,
    budget: int = 2000,
    include_digest: bool = True,
) -> tuple[str, dict]:
    """拼装定长工作记忆包。优先级：密码本 > 状态卡 > 滚动结论 > 摘要。

    返回 (文本, 各段成本明细)。
    """
    root = root or Path.cwd()
    cfg = cfg or load_config(root)
    dic = dic or Dictionary.load(root)
    counter = counter or Counter(cfg.encoding)
    detail: dict[str, float] = {}

    # 1) 密码本：绝不裁剪（裁了就无法解码）
    block = dic.block("compact") if dic.entries else ""
    detail["block"] = counter.count(block)

    # 2) 状态卡：预算的 20% 封顶，超了逐行丢
    state = State.load(root)
    state_txt = state.render()
    state_budget = max(120, int(budget * 0.20))
    if state_txt and counter.count(state_txt) > state_budget:
        lines = state_txt.split("\n")
        while len(lines) > 1 and counter.count("\n".join(lines)) > state_budget:
            lines.pop(1)  # 先丢最早的细节行，保留标题与目标
        state_txt = "\n".join(lines)
    detail["state"] = counter.count(state_txt)

    # 3) 滚动结论：吃掉剩余预算的 70%
    remain = max(0, budget - detail["block"] - detail["state"])
    roll_txt = Roll(root).render(int(remain * 0.7), counter)
    detail["roll"] = counter.count(roll_txt)
    remain -= detail["roll"]

    # 4) 压缩摘要：剩余预算（取尾部，最近的段落最值钱）
    digest_txt = ""
    if include_digest and remain > 40:
        dpath = state_dir(root) / "digest.txt"
        if dpath.exists():
            body = dpath.read_text(encoding="utf-8")
            header = "【项目摘要】\n"
            avail = remain - counter.count(header)
            # 字符→token 的换算不精确，迭代收缩几次直到落进剩余预算
            for _ in range(6):
                cost = counter.count(body)
                if cost <= avail or len(body) <= 120:
                    break
                keep = max(100, int(len(body) * (avail / max(1.0, cost)) * 0.92))
                body = "……[摘要截断]……\n" + body[-keep:]
            digest_txt = header + body
            detail["digest"] = counter.count(digest_txt)

    parts = [p for p in (block, state_txt, roll_txt, digest_txt) if p]
    pack = "\n\n".join(parts)
    detail["total"] = counter.count(pack)
    detail["budget"] = float(budget)
    return pack, detail


def write_pack(root: Path | None, out: Path | None = None, **kw) -> tuple[Path, dict]:
    pack, detail = build_pack(root, **kw)
    root = root or Path.cwd()
    dest = out or (state_dir(root) / PACK_NAME)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(pack, encoding="utf-8")
    return dest, detail


__all__ = ["State", "Roll", "Chunk", "build_pack", "write_pack", "STATE_NAME", "ROLL_NAME"]
