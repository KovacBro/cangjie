"""载体层：一个"字"从哪来 —— 按**实测 token 单价**选型。

⚠ 一条来自实测的关键事实（o200k_base，2026-10 实测，见 cangjie/data/carriers-o200k.json）：

    常用汉字      U+4E00 区 80% 是 2 token/字，只有 19.5% 是 1 token
    CJK 扩展 A    U+3400 区 96% 是 3 token/字        ← 生僻字做载体反而最贵
    私有区 PUA    平均 2.4 token/字                  ← 自造字并不便宜
    几何/数学/箭头/西里尔/希腊/注音/拉丁扩展
                 有大量 1 token/字的字符

所以"用生僻字当压缩载体"在真实分词器上是亏的（3 tok 换 3 tok）。
仓颉的做法是：**先量，再选，按短语价值配载体**——
短语只值 3 tok 就配 1 tok 的符，短语值 8 tok 才配 2 tok 的汉字。

三档预设：
    sym    符号族优先（几何/数学/箭头/希腊/西里尔/注音），单价最低，最不易撞词
    han    汉字优先（同价里挑汉字），形义相合、看着最像"真字"
    forge  私有区自造字，需要装字体；形态独一无二，但单价 2–3 tok
    auto   = sym → han 逐级降档（默认）
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"          # 随包分发（pip install 后可用）
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"  # 源码仓库布局
CENSUS_NAME = "carriers-o200k.json"

# 视觉族：族序决定同价时的取字顺序（数字越小越优先）
FAMILIES: list[tuple[str, tuple[tuple[int, int], ...]]] = [
    ("几何", ((0x25A0, 0x2600), (0x2580, 0x25A0))),
    ("数学", ((0x2200, 0x2300),)),
    ("箭头", ((0x2190, 0x2200),)),
    ("记号", ((0x2700, 0x27C0), (0x2460, 0x24FF))),
    ("希腊", ((0x0370, 0x0400),)),
    ("西里尔", ((0x0400, 0x0530),)),
    ("注音", ((0x3130, 0x3190),)),
    ("拉丁扩展", ((0x00C0, 0x0250), (0x1E00, 0x1F00))),
    ("汉字", ((0x3400, 0x4DC0), (0x4E00, 0xA000))),
    ("私有区", ((0xE000, 0xF900),)),
]

# 绝不用作载体的字符：ASCII、空白、组合符、日常标点、全角字母数字
_EXCLUDE = [
    (0x00, 0xBF), (0x2000, 0x2070), (0x20A0, 0x20D0), (0x2100, 0x2190),
    (0x2300, 0x23FF), (0x2500, 0x2580), (0x25A0, 0x25A0), (0x2600, 0x2700),
    (0x3000, 0x3030), (0x3040, 0x3100), (0xFE10, 0xFE70), (0xFF00, 0xFF70),
    (0xD800, 0xE000),
]

# 1 token 的常用标点也不能当载体（会跟正文打架）
_EXTRA_BAN = set("、。〃〈〉《》「」『』【】〔〕〖〗〜・･ー￣￥！％＆（）＊＋，－．／０１２３４５６７８９：；＜＝＞？＠［＼］＾＿｀｜～")

PRESET_ORDER = {
    "sym": ["几何", "数学", "箭头", "记号", "希腊", "西里尔", "注音", "拉丁扩展", "汉字", "私有区"],
    "han": ["汉字", "几何", "数学", "箭头", "记号", "希腊", "西里尔", "注音", "拉丁扩展", "私有区"],
    "forge": ["私有区", "几何", "数学", "箭头", "记号", "希腊", "西里尔", "注音"],
    "auto": ["几何", "数学", "箭头", "记号", "希腊", "西里尔", "注音", "拉丁扩展", "汉字", "私有区"],
}
PRESETS = tuple(PRESET_ORDER)


def family_of(ch: str) -> str:
    cp = ord(ch)
    for name, ranges in FAMILIES:
        for lo, hi in ranges:
            if lo <= cp < hi:
                return name
    return "其他"


def _excluded(ch: str) -> bool:
    if ch in _EXTRA_BAN:
        return True
    cp = ord(ch)
    for lo, hi in _EXCLUDE:
        if lo <= cp < hi:
            # 拉丁扩展里的字母保留，标点丢弃
            return True
    return False


def estimate_cost(ch: str) -> float:
    """无实测数据时按区段估单价（保守偏高，避免高估收益）。"""
    fam = family_of(ch)
    return {"汉字": 2.0, "私有区": 3.0, "几何": 1.0, "数学": 1.0, "箭头": 2.0,
            "记号": 2.0, "希腊": 1.0, "西里尔": 1.0, "注音": 1.0,
            "拉丁扩展": 1.0}.get(fam, 2.0)


# ---------------------------------------------------------------- 普查数据

def census_path(encoding: str) -> Path:
    return DATA_DIR / f"carriers-{encoding.replace('/', '_')}.json"


def load_census(encoding: str = "o200k_base") -> dict[str, float]:
    """载入"单 token 字符清单"。键是字符，值是实测单价。"""
    name = f"carriers-{encoding.replace('/', '_')}.json"
    for p in (
        Path(os.environ["CANGJIE_CENSUS"]) if os.environ.get("CANGJIE_CENSUS") else None,
        DATA_DIR / name,
        ASSET_DIR / name,
        DATA_DIR / CENSUS_NAME,
        ASSET_DIR / CENSUS_NAME,
    ):
        if p and p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict):
                return {k: float(v) for k, v in raw.items()}
            if isinstance(raw, list):
                return {chr(int(cp)) if isinstance(cp, int) else str(cp): 1.0 for cp in raw}
    return {}


def calibrate(counter, encoding: str = "o200k_base", span: tuple[int, int] = (0x20, 0x10000)) -> dict[str, float]:
    """实测 BMP 每个字符的 token 单价，只保留 1 token 的字符（其余按区段估算）。"""
    out: dict[str, float] = {}
    lo, hi = span
    for cp in range(lo, hi):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        try:
            if counter.count(ch) <= 1.0:
                out[ch] = 1.0
        except Exception:
            continue
    return out


def save_census(data: dict[str, float], encoding: str = "o200k_base") -> Path:
    p = census_path(encoding)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 载体池

class CarrierPool:
    def __init__(
        self,
        preset: str = "auto",
        counter=None,
        conflicts: str = "",
        census: dict[str, float] | None = None,
        encoding: str = "o200k_base",
        max_cost: float = 3.0,
    ):
        if preset not in PRESET_ORDER:
            preset = "auto"
        self.preset = preset
        self.counter = counter
        self.encoding = encoding
        self.max_cost = max_cost
        self.census = census if census is not None else load_census(encoding)
        self._ban = set(conflicts or "") | _EXTRA_BAN
        self._queue: list[tuple[str, float]] = self._build()
        self._idx = 0
        self._taken: list[str] = []

    # ---- 建队列 ----
    def _cost(self, ch: str) -> float:
        if ch in self.census:
            return float(self.census[ch])
        if self.counter is not None:
            try:
                return max(1.0, self.counter.count(ch))
            except Exception:
                pass
        return estimate_cost(ch)

    def _build(self) -> list[tuple[str, float]]:
        order = PRESET_ORDER[self.preset]
        rank = {name: i for i, name in enumerate(order)}
        pool: list[tuple[int, float, int, str]] = []
        for name, ranges in FAMILIES:
            if name not in rank:
                continue
            r = rank[name]
            for lo, hi in ranges:
                for cp in range(lo, hi):
                    ch = chr(cp)
                    if ch in self._ban or _excluded(ch):
                        continue
                    cost = self._cost(ch)
                    if cost > self.max_cost:
                        continue
                    pool.append((r, cost, cp, ch))
        pool.sort(key=lambda t: (t[1], t[0], t[2]))
        return [(ch, cost) for _, cost, _, ch in pool]

    # ---- 属性 ----
    @property
    def size(self) -> int:
        return len(self._queue)

    @property
    def cost(self) -> float:
        """队首（最优）载体的单价。"""
        return self._queue[self._idx][1] if self._idx < len(self._queue) else 3.0

    def head_distribution(self, n: int = 128) -> dict[float, int]:
        """前 n 个会被实际用到的载体的价格分布（比全池分布更能说明问题）。"""
        d: dict[float, int] = {}
        for _, c in self._queue[:n]:
            d[c] = d.get(c, 0) + 1
        return dict(sorted(d.items()))

    def distribution(self) -> dict[float, int]:
        d: dict[float, int] = {}
        for _, c in self._queue:
            d[c] = d.get(c, 0) + 1
        return dict(sorted(d.items()))

    def describe(self) -> str:
        dist = "、".join(f"{int(k)}tok×{v}" for k, v in self.distribution().items())
        src = "实测" if self.census else "估算"
        return f"{self.preset}({src}) 可用 {self.size} 字 [{dist}]"

    # ---- 取字 ----
    def pick(self, phrase_cost: float, min_gain: float = 1.5) -> tuple[str, float] | None:
        """给一个价值 phrase_cost 的短语配最便宜的可用载体。"""
        while self._idx < len(self._queue):
            ch, cost = self._queue[self._idx]
            if phrase_cost - cost >= min_gain:
                self._idx += 1
                self._taken.append(ch)
                return ch, cost
            if cost > 1.0:
                return None
            # 队列按价格升序，且这条短语连 1 token 的载体都配不上——
            # 说明该短语本身就不值得压缩，不消耗载体。
            return None
        return None

    def assign(self, n: int, phrase_costs: list[float] | None = None,
               min_gain: float = 1.5) -> list[str]:
        out: list[str] = []
        for i in range(n):
            pc = phrase_costs[i] if phrase_costs else 8.0
            got = self.pick(pc, min_gain)
            if not got:
                break
            out.append(got[0])
        return out

    def taken(self) -> list[str]:
        return list(self._taken)


__all__ = ["CarrierPool", "PRESETS", "PRESET_ORDER", "FAMILIES", "family_of",
           "load_census", "save_census", "calibrate", "estimate_cost", "census_path"]
