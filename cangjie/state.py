"""字典与账本：跨会话持久的工程状态。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

from .config import Config, dict_path, ledger_path, state_dir
from .tokenizer import Counter

ISO = "%Y-%m-%dT%H:%M:%S"


def _now() -> str:
    return time.strftime(ISO)


@dataclass
class Entry:
    g: str                    # 载体字
    p: str                    # 原短语
    n: int = 0                # 语料中出现次数
    cls: str = ""             # 义符（语义类）
    cost: float = 0.0         # 原短语 token 单价
    gcost: float = 1.0        # 载体字实测 token 单价
    hits: int = 0             # 实际被压缩命中次数
    created: str = ""
    last_used: str = ""
    note: str = ""
    pinned: bool = False      # 人工/种子锁定：永不淘汰

    @property
    def saved_per_hit(self) -> float:
        return max(0.0, self.cost - 1.0)

    @property
    def entry_cost(self) -> float:
        """字典条目本身的注入成本（字+分隔符+原文+换行）。"""
        return self.cost + 4.0

    @property
    def net(self) -> float:
        return self.hits * self.saved_per_hit - self.entry_cost

    @property
    def density(self) -> float:
        """每 token 字典成本换来的净收益，用于淘汰排序。"""
        return self.net / max(1.0, self.entry_cost)


@dataclass
class Dictionary:
    version: int = 1
    pool: str = "ext_a"
    entries: list[Entry] = field(default_factory=list)
    created: str = ""
    updated: str = ""

    # ---- IO ----
    @classmethod
    def load(cls, root: Path | None = None) -> "Dictionary":
        path = dict_path(root)
        if not path.exists():
            return cls(created=_now(), updated=_now())
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(created=_now(), updated=_now())
        entries = [Entry(**{k: v for k, v in e.items() if k in Entry.__annotations__})
                   for e in data.get("entries", [])]
        return cls(
            version=data.get("version", 1),
            pool=data.get("pool", "ext_a"),
            entries=entries,
            created=data.get("created", _now()),
            updated=data.get("updated", _now()),
        )

    def save(self, root: Path | None = None) -> Path:
        path = dict_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated = _now()
        payload = {
            "version": self.version,
            "pool": self.pool,
            "created": self.created or _now(),
            "updated": self.updated,
            "count": len(self.entries),
            "entries": [asdict(e) for e in self.entries],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # ---- 查询 ----
    def pairs(self) -> dict[str, str]:
        return {e.p: e.g for e in self.entries}

    def by_glyph(self) -> dict[str, Entry]:
        return {e.g: e for e in self.entries}

    def has(self, phrase: str) -> bool:
        return any(e.p == phrase for e in self.entries)

    def chars(self) -> str:
        return "".join(e.g for e in self.entries)

    # ---- 变更 ----
    def add(self, entry: Entry) -> None:
        entry.created = entry.created or _now()
        entry.last_used = entry.created
        self.entries.append(entry)

    def remove(self, glyph: str) -> Entry | None:
        for i, e in enumerate(self.entries):
            if e.g == glyph:
                return self.entries.pop(i)
        return None

    def touch(self, per_glyph: dict[str, int]) -> None:
        """记录实际命中次数（压缩后回写）。"""
        now = _now()
        idx = self.by_glyph()
        for g, k in per_glyph.items():
            e = idx.get(g)
            if e:
                e.hits += k
                e.last_used = now

    def total_entry_cost(self) -> float:
        return sum(e.entry_cost for e in self.entries)

    def summary(self) -> dict:
        hits = sum(e.hits for e in self.entries)
        pinned = sum(1 for e in self.entries if e.pinned)
        gross = sum(e.hits * e.saved_per_hit for e in self.entries)
        cost = self.total_entry_cost()
        return {
            "entries": len(self.entries),
            "pinned": pinned,
            "hits": hits,
            "gross_saved": round(gross, 1),
            "entry_cost": round(cost, 1),
            "net_saved": round(gross - cost, 1),
            "roi": round((gross - cost) / cost, 2) if cost else 0.0,
            "avg_phrase_cost": round(
                sum(e.cost for e in self.entries) / len(self.entries), 2
            ) if self.entries else 0.0,
        }

    # ---- 注入块：给模型看的"密码本" ----
    def block(self, fmt: str = "compact", limit: int | None = None) -> str:
        rows = self.entries[: limit or len(self.entries)]
        if fmt == "json":
            return json.dumps(
                {"v": self.version, "pool": self.pool,
                 "d": {e.g: e.p for e in rows}},
                ensure_ascii=False, separators=(",", ":"),
            )
        if fmt == "md":
            lines = ["| 字 | 原意 | 义符 | 命中 |", "|---|---|---|---|"]
            lines += [f"| {e.g} | {e.p} | {e.cls} | {e.hits} |" for e in rows]
            return "\n".join(lines)
        head = f"<仓颉v{self.version} pool={self.pool} n={len(rows)}>"
        body = [f"{e.g}={e.p}" for e in rows]
        return "\n".join([head, *body, "</仓颉>"])


# ---------------------------------------------------------------- 账本

def append_ledger(event: dict, root: Path | None = None) -> None:
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": _now(), **event}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_ledger(root: Path | None = None) -> list[dict]:
    path = ledger_path(root)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def measure(text: str, counter: Counter) -> dict:
    return {"chars": len(text), "tokens": counter.tokens(text)}


__all__ = ["Entry", "Dictionary", "append_ledger", "read_ledger", "measure"]
