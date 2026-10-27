"""Token 计量。

优先用 tiktoken 真实计量；离线/未安装时退化为校准过的启发式估算。
所有上游算法（挖掘打分、预算控制、报表）只依赖本模块的 ``Counter.count()``，
因此换成任何真实分词器都不影响其余逻辑。
"""
from __future__ import annotations

import math
import os
import re
import threading

# 中日韩统一表意文字（含扩展 A、兼容区、扩展 B 以上）
_CJK_PATTERN = (
    r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef"
    r"\U00020000-\U0003ffff"
)

_LEX_RE = re.compile(
    rf"([{_CJK_PATTERN}])"          # 单个 CJK 字/全角标点
    r"|([A-Za-z][A-Za-z0-9_'\u2019\-]*)"
    r"|([0-9]+(?:[.,][0-9]+)*)"
    r"|(\s+)"
    r"|(.)"
)

_CJK_CHAR_RE = re.compile(rf"[{_CJK_PATTERN}]")


def is_cjk(ch: str) -> bool:
    return bool(_CJK_CHAR_RE.match(ch))


class Counter:
    """token 计数器。

    离线环境注意：tiktoken 首次使用会去下载 BPE 文件，网络不通时会长时间挂起。
    因此这里用"带超时的后台线程"探测，超时即判定不可用并永久降级，
    绝不让计量环节卡住主流程（可用环境变量 CANGJIE_TOKENIZER=heuristic 强制跳过）。
    """

    _probe_cache: dict[str, object] = {}

    def __init__(self, encoding: str = "o200k_base", timeout: float = 2.5):
        self.encoding = encoding
        self.backend = "heuristic"
        self._enc = None
        mode = os.environ.get("CANGJIE_TOKENIZER", "auto").lower()
        if mode != "heuristic":
            enc = self._probe(encoding, timeout)
            if enc is not None:
                self._enc = enc
                self.backend = f"tiktoken:{encoding}"
            else:
                self.backend = "heuristic(v1)"

    @classmethod
    def _probe(cls, encoding: str, timeout: float):
        key = encoding
        if key in cls._probe_cache:
            return cls._probe_cache[key]
        result: list = [None]

        def work() -> None:
            try:  # pragma: no cover - 依赖外部包/网络
                import tiktoken  # type: ignore

                enc = tiktoken.get_encoding(encoding)
                enc.encode("校准")
                result[0] = enc
            except Exception:
                result[0] = None

        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(timeout)
        cls._probe_cache[key] = result[0]
        return result[0]

    # ---- 主接口 ----
    def count(self, text: str) -> float:
        if not text:
            return 0.0
        if self._enc is not None:
            try:
                return float(len(self._enc.encode(text, disallowed_special=())))
            except Exception:
                pass
        return _heuristic_count(text)

    def tokens(self, text: str) -> int:
        return int(round(self.count(text)))

    # ---- 载体字单价：决定"一个字"值多少 token ----
    def char_cost(self, ch: str) -> float:
        return self.count(ch)

    def pool_cost(self, chars: str) -> float:
        if not chars:
            return 0.0
        return self.count(chars) / len(chars)


def _heuristic_count(text: str) -> float:
    """启发式估算：CJK 约 1 token/字，拉丁约 4.5 字符/token。

    与 cl100k/o200k 的经验误差约 ±20%，仅用于排序与预算估算；
    需要精确数字时请安装 tiktoken。
    """
    total = 0.0
    for m in _LEX_RE.finditer(text):
        cjk, word, num, space, other = m.groups()
        if cjk:
            total += 1.0
        elif word:
            total += max(1.0, round(len(word) / 4.5, 2))
        elif num:
            total += max(1.0, round(len(num) / 3.0, 2))
        elif space:
            total += 0.05 * len(space) if len(space) > 1 else 0.1
        elif other:
            total += 0.6
    return total


def savings_ratio(before: float, after: float) -> float:
    if before <= 0:
        return 0.0
    return max(0.0, 1.0 - after / before)


def fmt_saved(before: float, after: float) -> str:
    ratio = savings_ratio(before, after)
    return f"{int(before)}→{int(after)} tok ({ratio * 100:.1f}% 节省)"


def approx_tokens_per_char(text: str) -> float:
    if not text:
        return 0.0
    return _heuristic_count(text) / len(text)


__all__ = ["Counter", "is_cjk", "savings_ratio", "fmt_saved", "math"]
