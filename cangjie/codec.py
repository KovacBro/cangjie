"""编解码器：文本 ⇄ 仓颉字。

三条硬约束：
1. **可逆**：decode(encode(x)) == x，逐字节无损（含代码块、反斜杠路径）。
2. **保真**：代码块 / 行内代码 / URL / 文件路径默认不压缩，避免把可执行内容改坏。
3. **最长优先**：同一位置多个短语可匹配时取最长，保证语义粒度最大。

转义只用一种形式：``\\X`` 表示"原文里的字面 X"，普通反斜杠不动，因此
Windows 路径 ``C:\\Users`` 在解码后依然是 ``C:\\Users``。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .mine import split_protected

_END = "\x00"  # trie 终止标记（不可能出现在真实键里）


@dataclass
class CodecStats:
    chars_before: int = 0
    chars_after: int = 0
    hits: int = 0
    per_glyph: dict[str, int] = field(default_factory=dict)
    protected_chars: int = 0

    def merge(self, other: "CodecStats") -> None:
        self.chars_before += other.chars_before
        self.chars_after += other.chars_after
        self.hits += other.hits
        self.protected_chars += other.protected_chars
        for k, v in other.per_glyph.items():
            self.per_glyph[k] = self.per_glyph.get(k, 0) + v


class Codec:
    def __init__(
        self,
        pairs: dict[str, str] | None = None,
        protect_code: bool = True,
        strict_escape: bool = True,
    ):
        """pairs: {短语: 载体字}"""
        self.p_to_g: dict[str, str] = dict(pairs or {})
        self.g_to_p: dict[str, str] = {g: p for p, g in self.p_to_g.items()}
        self.protect_code = protect_code
        self.strict_escape = strict_escape
        self._trie: dict = {}
        self._max_len = 1
        self._rebuild()

    # ---- 构建 ----
    def _rebuild(self) -> None:
        trie: dict = {}
        max_len = 1
        for phrase, glyph in self.p_to_g.items():
            if not phrase:
                continue
            max_len = max(max_len, len(phrase))
            node = trie
            for ch in phrase:
                node = node.setdefault(ch, {})
            node[_END] = glyph
        self._trie = trie
        self._max_len = max_len

    def add(self, phrase: str, glyph: str) -> None:
        self.p_to_g[phrase] = glyph
        self.g_to_p[glyph] = phrase
        self._rebuild()

    @property
    def empty(self) -> bool:
        return not self.p_to_g

    # ---- 编码 ----
    def _match(self, text: str, i: int) -> tuple[int, str] | None:
        node = self._trie
        best: tuple[int, str] | None = None
        j = i
        n = len(text)
        limit = i + self._max_len
        while j < n and j < limit:
            node = node.get(text[j])
            if node is None:
                break
            j += 1
            g = node.get(_END)
            if g:
                best = (j, g)
        return best

    def _escape(self, seg: str) -> str:
        """把段落里字面出现的载体字转义。只有真的出现才付出代价。"""
        if not self.strict_escape or not self.g_to_p:
            return seg
        if not any(ch in self.g_to_p for ch in seg):
            return seg
        out: list[str] = []
        i, n = 0, len(seg)
        while i < n:
            ch = seg[i]
            if ch == "\\" and i + 1 < n and seg[i + 1] in self.g_to_p:
                out.append("\\\\")
                out.append(seg[i + 1])
                i += 2
                continue
            if ch in self.g_to_p:
                out.append("\\")
                out.append(ch)
                i += 1
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    def _encode_plain(self, seg: str, st: CodecStats) -> str:
        out: list[str] = []
        i, n = 0, len(seg)
        while i < n:
            m = self._match(seg, i)
            if m:
                j, g = m
                out.append(g)
                st.hits += 1
                st.per_glyph[g] = st.per_glyph.get(g, 0) + 1
                i = j
            else:
                out.append(seg[i])
                i += 1
        return "".join(out)

    def encode(self, text: str) -> tuple[str, CodecStats]:
        st = CodecStats(chars_before=len(text))
        if self.empty or not text:
            st.chars_after = len(text)
            return text, st
        parts: list[str] = []
        pieces = split_protected(text) if self.protect_code else [(False, text)]
        for is_prot, seg in pieces:
            if is_prot:
                st.protected_chars += len(seg)
                parts.append(seg)
            else:
                parts.append(self._encode_plain(self._escape(seg), st))
        out = "".join(parts)
        st.chars_after = len(out)
        return out, st

    # ---- 解码 ----
    def decode(self, text: str) -> str:
        if not text or not self.g_to_p:
            return text
        out: list[str] = []
        i, n = 0, len(text)
        g2p = self.g_to_p
        while i < n:
            ch = text[i]
            if ch == "\\" and i + 1 < n:
                nxt = text[i + 1]
                if nxt == "\\" and i + 2 < n and text[i + 2] in g2p:
                    out.append("\\")
                    out.append(text[i + 2])
                    i += 3
                    continue
                if nxt in g2p:
                    out.append(nxt)
                    i += 2
                    continue
            if ch in g2p:
                out.append(g2p[ch])
                i += 1
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    # ---- 便利函数 ----
    def roundtrip_ok(self, text: str) -> bool:
        enc, _ = self.encode(text)
        return self.decode(enc) == text

    def coverage(self, stats: CodecStats) -> float:
        if stats.chars_before == 0:
            return 0.0
        return max(0.0, 1.0 - stats.chars_after / stats.chars_before)


__all__ = ["Codec", "CodecStats"]
