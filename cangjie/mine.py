"""语料采集 + 高频短语挖掘 + 造字选型。

挖掘不是简单数 n-gram——那样只会挖出"果的重排序模型用"这种从词中间切开的碎片。
这里用新词发现（unsupervised word discovery）的三件套做过滤：

1. **频次**     出现次数够多；
2. **凝聚度**   内部结合紧密度（PMI 近似），防止把"数据库+连接池"这种偶然拼邻当整体；
3. **自由度**   左右邻接熵，两侧都能接不同的字才算独立词，
                这一条专门杀掉"切在词中间"的碎片（它们的左邻永远固定）。

再加上文档频次（df）与坐标上限，最后才进入 token 收益打分。
"""
from __future__ import annotations

import math
import re
from collections import Counter as _Counter
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, SKIP_DIRS
from .tokenizer import Counter, is_cjk

# ---------------------------------------------------------------- 语料采集

_CODE_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(r"(?:https?|ftp)://[^\s)>\]\"']+")
_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+|(?:\.{0,2}/[\w\-.@]+){2,}")
_DOC_SPLIT_RE = re.compile(r"\n<<<[^>]*>>>\n")

# 句读：跨过这些字符的 n-gram 一律不算（否则会挖出半句话）
_BREAK_CHARS = set("。！？；!?;\n\r，,")


def split_protected(text: str) -> list[tuple[bool, str]]:
    """切成 (是否受保护, 片段)。受保护 = 代码块/行内代码/URL/路径。"""
    spans: list[tuple[int, int]] = []
    for rx in (_CODE_FENCE_RE, _INLINE_CODE_RE, _URL_RE, _PATH_RE):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end()))
    if not spans:
        return [(False, text)]
    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out: list[tuple[bool, str]] = []
    cur = 0
    for s, e in merged:
        if s > cur:
            out.append((False, text[cur:s]))
        out.append((True, text[s:e]))
        cur = e
    if cur < len(text):
        out.append((False, text[cur:]))
    return out


def gather(paths: list[str], cfg: Config) -> tuple[str, dict]:
    stats = {"files": 0, "bytes": 0, "skipped": 0}
    chunks: list[str] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            targets = [p]
        elif p.is_dir():
            targets = [
                f for f in p.rglob("*")
                if f.is_file()
                and f.suffix.lower() in set(cfg.scan_exts)
                and not any(part in SKIP_DIRS for part in f.parts)
            ]
        else:
            stats["skipped"] += 1
            continue
        for f in targets:
            try:
                if f.stat().st_size > cfg.max_file_bytes:
                    stats["skipped"] += 1
                    continue
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                stats["skipped"] += 1
                continue
            if "\x00" in text[:2048]:
                stats["skipped"] += 1
                continue
            stats["files"] += 1
            stats["bytes"] += len(text.encode("utf-8", "ignore"))
            chunks.append(f"\n<<<{f.name}>>>\n{text}\n")
    return "".join(chunks), stats


def split_docs(text: str) -> list[str]:
    parts = _DOC_SPLIT_RE.split(text)
    return [p for p in parts if p.strip()]


# ---------------------------------------------------------------- n-gram 切分

_UNIT_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
    r"|[A-Za-z][A-Za-z0-9_'\-]*"
    r"|[0-9]+(?:[.,][0-9]+)*"
)


@dataclass
class Unit:
    text: str
    start: int
    end: int
    cjk: bool


def line_units(line: str) -> list[Unit]:
    return [
        Unit(m.group(), m.start(), m.end(), is_cjk(m.group()[0]))
        for m in _UNIT_RE.finditer(line)
    ]


# 起始/结尾是这些虚词，基本可以断定是从句子中间切出来的碎片
# 只放"几乎不可能作为一个术语开头/结尾"的虚词——宁可漏杀，不可错杀
# （"向量/在线/存在/总和/参与"这类真词会因此被误杀，所以集合刻意收窄）
_FRAG_PREFIX = set("的了着过而就也都其之把被于以")
_FRAG_SUFFIX = set("的着过而就也都其之把被于以")
_DIGIT_RE = re.compile(r"\d")


def fragmentish(phrase: str) -> str:
    """判断短语是不是"句子碎片"，返回原因（空串表示通过）。"""
    if not phrase:
        return "空"
    if phrase[0] in _FRAG_PREFIX:
        return "以虚词开头"
    if phrase[-1] in _FRAG_SUFFIX:
        return "以虚词结尾"
    if _DIGIT_RE.search(phrase) and not re.fullmatch(r"[A-Za-z_][\w.\-]*", phrase):
        return "含数字（跨文档不稳定）"
    if phrase.count(" ") > 3:
        return "含空格过多"
    return ""


STOP_PHRASES = {
    "这个", "那个", "我们", "你们", "他们", "可以", "如果", "因为", "所以",
    "但是", "然后", "就是", "一个", "什么", "怎么", "已经", "还是", "以及",
    "the", "and", "for", "with", "that", "this", "from", "you", "are",
}


@dataclass
class Corpus:
    docs: int = 0
    units: int = 0
    total: dict = field(default_factory=dict)     # 短语 → 出现次数
    prose: dict = field(default_factory=dict)     # 短语 → 正文区出现次数
    df: dict = field(default_factory=dict)        # 短语 → 命中文档数
    uni: dict = field(default_factory=dict)       # 单字/单词 → 次数


def _iter_ngrams(line: str, cfg: Config):
    """产出一行内的候选短语（含起止位置与两侧邻字）。"""
    us = line_units(line)
    n_us = len(us)
    for i in range(n_us):
        maxn = cfg.max_ngram_cjk if us[i].cjk else cfg.max_ngram_latin
        maxn = min(maxn, n_us - i)
        for n in range(1, maxn + 1):
            j = i + n - 1
            ok = True
            for k in range(i, j):
                gap = line[us[k].end: us[k + 1].start]
                if any(c in _BREAK_CHARS for c in gap):
                    ok = False
                    break
            if not ok:
                break
            first, last = us[i], us[j]
            span_len = last.end - first.start
            if n == 1:
                # 单个中文字永远不划算；单个英文词需足够长
                if us[i].cjk or span_len < 8 or n < cfg.min_len_latin:
                    continue
            elif not us[i].cjk and n < cfg.min_len_latin:
                continue
            if span_len > cfg.max_phrase_chars:
                continue
            phrase = line[first.start:last.end]
            if phrase.lower() in STOP_PHRASES or phrase in STOP_PHRASES:
                continue
            if phrase.isdigit() or not phrase.strip():
                continue
            left = line[first.start - 1] if first.start > 0 else "^"
            right = line[last.end] if last.end < len(line) else "$"
            yield phrase, left, right, n


def analyse(text: str, cfg: Config) -> Corpus:
    docs = split_docs(text) or [text]
    c = Corpus(docs=len(docs))
    total: _Counter[str] = _Counter()
    prose: _Counter[str] = _Counter()
    df: _Counter[str] = _Counter()
    uni: _Counter[str] = _Counter()

    for doc in docs:
        seen: set[str] = set()
        for is_prot, seg in split_protected(doc):
            for line in seg.splitlines():
                for u in line_units(line):
                    uni[u.text] += 1
                for phrase, _l, _r, _n in _iter_ngrams(line, cfg):
                    total[phrase] += 1
                    if not is_prot:
                        prose[phrase] += 1
                    if phrase not in seen:
                        seen.add(phrase)
                        df[phrase] += 1
    c.total = dict(total)
    c.prose = dict(prose)
    c.df = dict(df)
    c.uni = dict(uni)
    c.units = sum(uni.values()) or 1
    return c


def _neighbors(text: str, cfg: Config, wanted: set[str]) -> tuple[dict, dict]:
    left: dict[str, _Counter] = {}
    right: dict[str, _Counter] = {}
    for doc in split_docs(text) or [text]:
        for _is_prot, seg in split_protected(doc):
            for line in seg.splitlines():
                for phrase, l, r, _n in _iter_ngrams(line, cfg):
                    if phrase in wanted:
                        left.setdefault(phrase, _Counter())[l] += 1
                        right.setdefault(phrase, _Counter())[r] += 1
    return left, right


def entropy(counter: _Counter | dict) -> float:
    n = sum(counter.values())
    if n <= 1:
        return 0.0
    return -sum((v / n) * math.log2(v / n) for v in counter.values() if v)


def cohesion(phrase: str, corpus: Corpus) -> float:
    """最弱切分点的凝聚度（点互信息的比值化版本，>1 表示结合紧密）。

    切分点取**词元边界**而不是字符边界——否则英文短语会被从单词中间切开。
    """
    us = line_units(phrase)
    if len(us) < 2:
        return 1.0
    total_phrase = corpus.total.get(phrase, 0) or 1
    best = float("inf")
    for k in range(1, len(us)):
        a = phrase[:us[k].start].strip()
        b = phrase[us[k].start:].strip()
        if not a or not b:
            continue
        ca = corpus.total.get(a) or corpus.uni.get(a) or 0
        cb = corpus.total.get(b) or corpus.uni.get(b) or 0
        if ca <= 0 or cb <= 0:
            continue
        best = min(best, total_phrase * corpus.units / (ca * cb))
    return 1.0 if best == float("inf") else best


# ---------------------------------------------------------------- 候选打分

@dataclass
class Candidate:
    phrase: str
    count: int = 0
    prose_count: int = 0
    cost: float = 0.0
    net: float = 0.0
    cls: str = ""
    glyph: str = ""
    gcost: float = 1.0
    entry_cost: float = 0.0
    freedom: float = 0.0
    cohesion: float = 1.0
    note: str = ""


def _quick_cost(phrase: str) -> float:
    c = 0.0
    for u in line_units(phrase):
        c += 1.0 if u.cjk else max(1.0, round(len(u.text) / 4.5, 2))
    for ch in phrase:
        if ch in " \t":
            c += 0.1
    return max(c, 1.0)


def mine(
    text: str,
    cfg: Config,
    counter: Counter | None = None,
    pool_cost: float = 1.0,
    top: int = 64,
    corpus: Corpus | None = None,
) -> list[Candidate]:
    counter = counter or Counter(cfg.encoding)
    corpus = corpus or analyse(text, cfg)

    # 第一轮：频次 + 单价粗筛
    rough: dict[str, Candidate] = {}
    for phrase, cnt in corpus.total.items():
        if cnt < cfg.min_freq:
            continue
        if cfg.docs_gate and corpus.docs >= 3 and corpus.df.get(phrase, 0) < 2:
            continue
        pc = _quick_cost(phrase)
        if pc < cfg.min_phrase_cost:
            continue
        bad = fragmentish(phrase)
        if bad and not (cfg.allow_digits and bad == "含数字（跨文档不稳定）"):
            continue
        pcnt = corpus.prose.get(phrase, 0)
        if cfg.skip_code_only and pcnt == 0:
            continue
        eff = pcnt + (cnt - pcnt) * 0.25
        if eff < cfg.min_freq:
            continue
        if eff * (pc - pool_cost) <= (pc + 4.0) * cfg.amortization:
            continue
        rough[phrase] = Candidate(phrase=phrase, count=cnt, prose_count=pcnt,
                                  cost=pc, note="code" if pcnt == 0 else "")

    if not rough:
        return []

    # 第二轮：只用候选集收集左右邻，做自由度/凝聚度过滤
    left, right = _neighbors(text, cfg, set(rough))
    kept: list[Candidate] = []
    for phrase, cand in rough.items():
        lf = entropy(left.get(phrase, {}))
        rf = entropy(right.get(phrase, {}))
        coh = cohesion(phrase, corpus)
        cand.freedom = min(lf, rf)
        cand.cohesion = round(coh, 2)
        if cand.freedom < cfg.min_freedom:
            continue
        if len(line_units(phrase)) >= 3 and coh < cfg.min_cohesion:
            continue
        kept.append(cand)

    # 第三轮：用真实分词器校正单价与净收益
    kept.sort(key=lambda c: -c.net)
    for c in kept[: max(top * 4, 200)]:
        real = counter.count(c.phrase)
        if real > 0:
            eff = c.prose_count + (c.count - c.prose_count) * 0.25
            c.cost = real
            c.entry_cost = real + 4.0
            c.net = eff * (real - pool_cost) - c.entry_cost * cfg.amortization
    kept.sort(key=lambda c: -c.net)

    # 第四轮：极大化（能取长的就不取碎片）+ 互不包含 + 取前 top
    head = kept[:600]
    chosen: list[Candidate] = []
    for c in kept:
        if len(chosen) >= top or c.net <= 0:
            break
        if any(c.phrase in s.phrase or s.phrase in c.phrase for s in chosen):
            continue
        # 若存在更长且频次相近的短语，说明当前这条是被截断的碎片
        superseded = any(
            (c.phrase in o.phrase and o.phrase != c.phrase
             and o.count >= c.count * 0.6)
            for o in head
        )
        if superseded:
            continue
        chosen.append(c)
    return chosen


def plan(
    text: str,
    cfg: Config,
    counter: Counter | None = None,
    pool_cost: float | None = None,
    top: int | None = None,
    conflicts: str = "",
    pool=None,
) -> list[Candidate]:
    """挖掘 + 语义分类 + 按短语价值配载体字，得到可落库的方案。

    关键：不是"每条短语配一个字"，而是**按短语值多少 token 配多贵的字**。
    配不上（净赚 < min_gain）的候选直接丢弃——这正是后期不膨胀的根源。
    """
    from .carriers import CarrierPool
    from .glyphs import classify

    counter = counter or Counter(cfg.encoding)
    if pool is None:
        pool = CarrierPool(cfg.carrier_pool, counter, conflicts, encoding=cfg.encoding)
    if pool_cost is None:
        pool_cost = pool.cost
    limit = top or cfg.max_entries
    chosen = mine(text, cfg, counter, pool_cost, max(limit * 2, limit + 16))

    out: list[Candidate] = []
    for c in chosen:
        if len(out) >= limit:
            break
        got = pool.pick(c.cost, cfg.min_gain)
        if not got:
            continue
        c.glyph, c.gcost = got
        c.cls = classify(c.phrase)
        eff = c.prose_count + (c.count - c.prose_count) * 0.25
        c.net = eff * (c.cost - c.gcost) - c.entry_cost * cfg.amortization
        if c.net <= 0:
            continue
        out.append(c)
    return out


__all__ = ["gather", "analyse", "mine", "plan", "Candidate", "Corpus",
           "split_protected", "split_docs", "line_units", "entropy", "cohesion"]
