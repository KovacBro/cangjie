"""命令行入口：cangjie <子命令>。

设计原则：所有输出都可管道化；压缩数据走 stdout，人类可读的统计走 stderr。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, budget, report
from .carriers import PRESETS, CarrierPool, FAMILIES, family_of, load_census
from .codec import Codec
from .config import Config, find_root, load_config, save_config, state_dir
from .glyphs import spec_for
from .mine import gather, plan
from .state import Dictionary, Entry, append_ledger, read_ledger
from .tokenizer import Counter, fmt_saved

WIDE = set("　-〿＀-￯")


def _w(s: str) -> int:
    """东亚宽度感知的显示宽度。"""
    return sum(2 if (ord(c) in range(0x1100, 0x1160) or ord(c) in range(0x2E80, 0xA4D0)
                     or ord(c) in range(0xAC00, 0xD7A0) or ord(c) in range(0xF900, 0xFB00)
                     or ord(c) in range(0xFF00, 0xFF60) or ord(c) in range(0x20000, 0x3FFFD))
               else 1 for c in s)


def _pad(s: str, n: int) -> str:
    return s + " " * max(0, n - _w(s))


def table(headers: list[str], rows: list[list[str]], align: str = "l") -> str:
    widths = [max(_w(str(headers[i])), *(_w(str(r[i])) for r in rows)) if rows
              else _w(str(headers[i])) for i in range(len(headers))]
    out = ["  ".join(_pad(str(h), widths[i]) for i, h in enumerate(headers))]
    out.append("  ".join("-" * w for w in widths))
    for r in rows:
        out.append("  ".join(_pad(str(c), widths[i]) for i, c in enumerate(r)))
    return "\n".join(out)


def _counter(cfg: Config) -> Counter:
    return Counter(cfg.encoding)


def _codec(root: Path, cfg: Config, dic: Dictionary | None = None) -> Codec:
    dic = dic or Dictionary.load(root)
    return Codec(dic.pairs(), protect_code=cfg.protect_code,
                 strict_escape=cfg.strict_escape)


def _read(path: str | None) -> str:
    if path in (None, "-"):
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _write(text: str, path: str | None) -> None:
    if path in (None, "-"):
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- 子命令

def cmd_init(args) -> int:
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    d = state_dir(root)
    cfg = load_config(root)
    if args.pool:
        cfg.carrier_pool = args.pool
    if args.max_entries:
        cfg.max_entries = args.max_entries
    save_config(cfg, root)
    dic = Dictionary.load(root)
    dic.pool = cfg.carrier_pool
    dic.save(root)
    print(f"✓ 初始化 {d}")
    print(f"  载体池 {cfg.carrier_pool} · 字典上限 {cfg.max_entries}")
    return 0


def cmd_scan(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    paths = args.paths or [str(root)]
    text, st = gather(paths, cfg)
    c = _counter(cfg)
    print(f"扫描：{st['files']} 个文件 / {st['bytes'] / 1024:.1f} KB / 跳过 {st['skipped']}")
    print(f"语料 token 量：{c.tokens(text)}（计量后端 {c.backend}）")
    return 0


def cmd_mine(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    c = _counter(cfg)
    paths = args.paths or [str(root)]
    text, st = gather(paths, cfg)
    if not text.strip():
        print("⚠ 语料为空，检查路径或 scan_exts。", file=sys.stderr)
        return 1
    dic = Dictionary.load(root)
    conflicts = "".join(set(text) | set(dic.chars()) | {e.g for e in dic.entries})
    pool = CarrierPool(cfg.carrier_pool, c, conflicts, encoding=cfg.encoding)
    top = args.top or max(0, cfg.max_entries - len(dic.entries))

    cands = plan(text, cfg, c, pool.cost, top=top, conflicts=conflicts, pool=pool)
    if not cands:
        print("没有找到值得造字的短语（提高语料量或降低 min_freq）。")
        return 0

    rows = [[x.glyph, x.phrase[:22], x.cls, str(x.count), f"{x.cost:.1f}",
             f"{x.gcost:.1f}", f"{x.net:.0f}"] for x in cands[: args.show]]
    print(f"语料：{st['files']} 文件 / {c.tokens(text)} tok · 载体 {pool.describe()}")
    print(table(["字", "原意", "义符", "次", "原价", "字价", "净收益"], rows))
    total = sum(x.net for x in cands)
    print(f"\n候选 {len(cands)} 条，预计净省 {total:.0f} tok / 语料周期")

    if args.apply:
        added = 0
        for x in cands:
            if dic.has(x.phrase):
                continue
            dic.add(Entry(g=x.glyph, p=x.phrase, n=x.count, cls=x.cls,
                          cost=x.cost, gcost=x.gcost))
            added += 1
        dic.pool = cfg.carrier_pool
        dic.save(root)
        append_ledger({"event": "mine", "added": added,
                       "entries": len(dic.entries)}, root)
        print(f"✓ 已入库 {added} 条，当前字典 {len(dic.entries)} 条 → .cangjie/dict.json")
    else:
        print("（试运行，未落库；加 --apply 写入字典）")
    return 0


def cmd_block(args) -> int:
    root = Path(args.root).resolve()
    dic = Dictionary.load(root)
    if not dic.entries:
        print("字典为空。", file=sys.stderr)
        return 1
    txt = dic.block(args.format, args.limit)
    _write(txt, args.out)
    if args.out:
        c = _counter(load_config(root))
        print(f"✓ 已写入 {args.out}（{c.tokens(txt)} tok，注入成本）", file=sys.stderr)
    return 0


def cmd_compress(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    dic = Dictionary.load(root)
    codec = _codec(root, cfg, dic)
    text = _read(args.input)
    out, st = codec.encode(text)
    _write(out, args.output)
    c = _counter(cfg)
    before, after = c.count(text), c.count(out)
    dic.touch(st.per_glyph)
    dic.save(root)
    append_ledger({"event": "compress", "chars": len(text), "hits": st.hits,
                   "tokens_before": round(before, 1), "tokens_after": round(after, 1)}, root)
    if not args.quiet:
        print(f"[仓颉] {fmt_saved(before, after)} · 命中 {st.hits} 处",
              file=sys.stderr)
    return 0


def cmd_expand(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    codec = _codec(root, cfg)
    text = _read(args.input)
    _write(codec.decode(text), args.output)
    return 0


def cmd_run(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    dic = Dictionary.load(root)
    codec = _codec(root, cfg, dic)
    if not args.cmd:
        print("用法：cangjie run -- <命令...>", file=sys.stderr)
        return 2
    proc = subprocess.run(args.cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    raw = (proc.stdout or "") + (proc.stderr or "")
    out, st = codec.encode(raw)
    sys.stdout.write(out)
    dic.touch(st.per_glyph)
    dic.save(root)
    c = _counter(cfg)
    before, after = c.count(raw), c.count(out)
    append_ledger({"event": "run", "hits": st.hits,
                   "tokens_before": round(before, 1), "tokens_after": round(after, 1)}, root)
    print(f"\n[仓颉] {args.cmd[0]} 输出 {fmt_saved(before, after)} · "
          f"还原：cangjie expand", file=sys.stderr)
    return proc.returncode


def cmd_digest(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    dic = Dictionary.load(root)
    codec = _codec(root, cfg, dic)
    c = _counter(cfg)
    paths = args.paths or [str(root)]
    text, st = gather(paths, cfg)
    out, s2 = codec.encode(text)
    dest = Path(args.out) if args.out else (root / ".cangjie" / "digest.txt")
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = out
    if args.with_block and dic.entries:
        payload = dic.block("compact") + "\n\n" + out
    dest.write_text(payload, encoding="utf-8")
    dic.touch(s2.per_glyph)
    dic.save(root)
    print(f"✓ 摘要写入 {dest}")
    print(f"  {st['files']} 文件 · 原文 {c.tokens(text)} tok → 摘要 {c.tokens(out)} tok "
          f"（{(1 - c.tokens(out) / max(1, c.tokens(text))) * 100:.1f}% 压缩）")
    return 0



def cmd_pack(args) -> int:
    """把「密码本 + 状态卡 + 滚动结论 + 摘要」拼成预算内的工作记忆包。"""
    from .memory import write_pack

    root = Path(args.root).resolve()
    cfg = load_config(root)
    dest = Path(args.out) if args.out else None
    path, detail = write_pack(
        root, dest, cfg=cfg, budget=args.budget,
        include_digest=not args.no_digest,
    )
    print(f"✓ 工作记忆包：{path}")
    parts = " + ".join(f"{k} {int(v)}" for k, v in detail.items()
                       if k not in ("total", "budget") and v)
    print(f"  {int(detail['total'])} tok / 预算 {int(detail['budget'])} tok（{parts}）")
    if detail["total"] > detail["budget"]:
        print("  ⚠ 超预算：密码本与状态卡不可压，请缩小 --budget 之外的段落或 prune 字典",
              file=sys.stderr)
    return 0


def cmd_trim(args) -> int:
    """裁剪命令输出。比字码高一个数量级的那一刀（有损）。"""
    from .trim import detect_profile, trim

    root = Path(args.root).resolve()
    cfg = load_config(root)
    c = _counter(cfg)
    cmd = [x for x in (args.cmd or []) if x != "--"]
    if cmd:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        raw = (proc.stdout or "") + (proc.stderr or "")
        profile = args.profile if args.profile != "auto" else detect_profile(cmd)
        code = proc.returncode
    else:
        raw = _read(args.input)
        profile = args.profile
        code = 0

    if args.full:
        sys.stdout.write(raw)
        return code

    out, st = trim(raw, profile=profile, max_lines=args.max_lines,
                   keep_head=args.head, keep_tail=args.tail)
    sys.stdout.write(out if out.endswith("\n") else out + "\n")
    before, after = c.count(raw), c.count(out)
    append_ledger({"event": "trim", "profile": st.profile,
                   "tokens_before": round(before, 1), "tokens_after": round(after, 1)}, root)
    print(f"[仓颉trim] {fmt_saved(before, after)} · {st.summary()}", file=sys.stderr)
    return code


def cmd_sift(args) -> int:
    """上下文安检：逐行判定有用/没用。默认只报告，--drop 才删（只删高置信噪音）。"""
    from .sift import report as sift_report, sift as _sift

    root = Path(args.root).resolve()
    cfg = load_config(root)
    c = _counter(cfg)
    cmd = [x for x in (args.cmd or []) if x != "--"]
    if cmd:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        raw = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
    else:
        raw = _read(args.input)
        code = 0

    dic = Dictionary.load(root)
    terms = tuple(e.p for e in dic.entries)
    if args.drop:
        out, st = _sift(raw, drop=True, terms=terms)
        _write(out, args.output)
        before, after = c.count(raw), c.count(out)
        append_ledger({"event": "sift", "lines": st.lines,
                       "tokens_before": round(before, 1),
                       "tokens_after": round(after, 1)}, root)
        print(f"[仓颉sift] {fmt_saved(before, after)} · {st.summary()}", file=sys.stderr)
    else:
        print(sift_report(raw, terms=terms, show=args.show))
    return code


def cmd_seed(args) -> int:
    """人工锁定术语：永不被淘汰，也不被重新分配载体字。"""
    root = Path(args.root).resolve()
    cfg = load_config(root)
    c = _counter(cfg)
    dic = Dictionary.load(root)

    if args.list or (not args.add and not args.remove):
        rows = [[e.g, e.p[:28], "锁定" if e.pinned else "-", str(e.hits)]
                for e in dic.entries if e.pinned]
        if rows:
            print(table(["字", "术语", "状态", "命中"], rows))
        else:
            print("暂无锁定术语。用 `cangjie seed --add 术语A 术语B` 添加。")
        return 0

    changed = 0
    if args.add:
        conflicts = "".join(set(dic.chars()))
        pool = CarrierPool(cfg.carrier_pool, c, conflicts, encoding=cfg.encoding)
        for phrase in args.add:
            phrase = phrase.strip()
            if not phrase:
                continue
            exist = next((e for e in dic.entries if e.p == phrase), None)
            if exist:
                exist.pinned = True
                changed += 1
                continue
            cost = c.count(phrase)
            got = pool.pick(max(cost, cfg.min_phrase_cost + 1), 0.5)
            if not got:
                print(f"⚠ 载体不足，跳过：{phrase}", file=sys.stderr)
                continue
            from .glyphs import classify
            dic.add(Entry(g=got[0], p=phrase, n=0, cls=classify(phrase),
                          cost=cost, gcost=got[1], pinned=True))
            changed += 1
    if args.remove:
        for phrase in args.remove:
            for e in list(dic.entries):
                if e.p == phrase:
                    if e.hits == 0:
                        dic.remove(e.g)
                    else:
                        e.pinned = False
                    changed += 1
    dic.save(root)
    append_ledger({"event": "seed", "changed": changed}, root)
    print(f"✓ 锁定表已更新，共 {sum(1 for e in dic.entries if e.pinned)} 条锁定术语")
    return 0


def cmd_state(args) -> int:
    """结构化状态卡：目标/阻塞/已决/下一步/事实。约 200 token 顶几千 token 历史。"""
    from .memory import State

    root = Path(args.root).resolve()
    st = State.load(root)
    touched = False
    for item in (args.set or []):
        if "=" in item:
            k, v = item.split("=", 1)
            st.set(k.strip(), v.strip())
            touched = True
    for item in (args.add or []):
        if "=" in item:
            k, v = item.split("=", 1)
            st.add(k.strip(), v.strip())
        else:
            st.add("decided", item)
        touched = True
    for item in (args.drop or []):
        st.drop(item.strip())
        touched = True
    if args.clear:
        st = State()
        touched = True
    if touched:
        st.save(root)
    if args.json:
        print(json.dumps(st.__dict__, ensure_ascii=False, indent=2))
        return 0
    txt = st.render()
    if not txt:
        print("状态卡为空。示例：\n"
              '  cangjie state --set goal="把检索延迟压到 200ms" \\n'
              '               --set blocker="reranker OOM" \\n'
              '               --add decided="改用 fp16" --add next="跑基准"')
    else:
        print(txt)
        c = _counter(load_config(root))
        print(f"\n[状态卡 {c.tokens(txt)} tok]", file=sys.stderr)
    return 0


def cmd_roll(args) -> int:
    """滚动结论：只追加，不重写历史。"""
    from .memory import Roll

    root = Path(args.root).resolve()
    cfg = load_config(root)
    roll = Roll(root)
    if args.clear:
        n = roll.clear()
        print(f"✓ 已清空 {n} 条结论")
        return 0
    if args.add:
        text = " ".join(args.add)
        if text == "-":
            text = sys.stdin.read()
        chunk = roll.add(text, args.tag)
        c = _counter(cfg)
        print(f"✓ 已追加（{len(roll.chunks)} 条，{c.count(chunk.text)} tok）")
        return 0
    body = roll.render(args.budget or None, _counter(cfg))
    print(body or "暂无滚动结论。用 `cangjie roll --add \"本轮结论\" --tag turn-3` 添加。")
    return 0


def cmd_stats(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    dic = Dictionary.load(root)
    s = dic.summary()
    print(f"字典 v{dic.version} · 池 {dic.pool} · {s['entries']}/{cfg.max_entries} 条")
    print(f"命中 {s['hits']} · 字典成本 {s['entry_cost']} · 净收益 {s['net_saved']} tok · ROI {s['roi']}")
    ledger = read_ledger(root)
    if ledger:
        comp = [e for e in ledger if e.get("event") == "compress"]
        b = sum(e.get("tokens_before", 0) for e in comp)
        a = sum(e.get("tokens_after", 0) for e in comp)
        if b:
            print(f"实测：{int(b)} → {int(a)} tok（{(1 - a / b) * 100:.1f}% 节省，{len(comp)} 次压缩）")
    if dic.entries:
        print()
        print(table(["字", "原意", "原价", "字价", "命中", "净"], [
            [e.g, e.p[:20], f"{e.cost:.1f}", f"{e.gcost:.1f}", str(e.hits), f"{e.net:.0f}"]
            for e in sorted(dic.entries, key=lambda x: -x.net)[: args.show]
        ]))
    return 0


def cmd_report(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    dic = Dictionary.load(root)
    out = Path(args.out) if args.out else None
    p = report.write(dic, cfg, root, out)
    print(f"✓ 报表：{p}")
    return 0


def cmd_prune(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    if args.to:
        cfg.max_entries = args.to
        save_config(cfg, root)
    dic = Dictionary.load(root)
    v = budget.enforce(dic, cfg)
    if not v.drop:
        print("无需裁剪。")
        return 0
    budget.apply(dic, v)
    dic.save(root)
    append_ledger({"event": "prune", "dropped": len(v.drop)}, root)
    print(f"✓ 裁剪 {len(v.drop)} 条，剩余 {len(dic.entries)} 条")
    for g, r in list(v.reasons.items())[:10]:
        print(f"  - {g}: {r}")
    return 0


def cmd_bench(args) -> int:
    cfg = load_config(find_root())
    c = _counter(cfg)
    print(f"分词器后端：{c.backend}")
    census = load_census(cfg.encoding)
    print(f"单 token 字符普查：{len(census)} 个可用字符"
          f"（{'实测缓存' if census else '缺失，将按区段估算'}）")
    rows = []
    for name in PRESETS:
        if name == "auto":
            continue
        p = CarrierPool(name, c, encoding=cfg.encoding)
        dist = p.head_distribution(128)
        rows.append([name, str(p.size),
                     f"{min(dist) if dist else 0:.0f}",
                     "、".join(f"{int(k)}:{v}" for k, v in dist.items())])
    print(table(["预设", "可用字数", "前128最低价", "前128价格分布"], rows))
    print()
    fams = {}
    for ch in census:
        fams.setdefault(family_of(ch), 0)
        fams[family_of(ch)] += 1
    print("实测单 token 字符最多的族：" + "、".join(
        f"{k} {v}" for k, v in sorted(fams.items(), key=lambda x: -x[1])[:8]))
    print()
    for s_ in ["数据库连接池", "向量检索与重排序", "async def fetch_data(url):",
               "the quick brown fox", "大模型生成回答时要带上引用编号"]:
        print(f"  {s_!r}\t{c.count(s_):.0f} tok / {len(s_)} 字")
    print("\n结论：压缩一条短语必须满足 短语单价 - 载体单价 >= min_gain(默认 1.5)，"
          "否则宁可不收录。")
    return 0



def cmd_calibrate(args) -> int:
    """逐字实测 BMP 单价，产出 carriers-<encoding>.json 普查表。"""
    from .carriers import calibrate, save_census

    cfg = load_config(find_root())
    c = _counter(cfg)
    if not c.backend.startswith("tiktoken"):
        print("✗ 需要真实分词器才能校准（先 pip install tiktoken 并联网一次）。",
              file=sys.stderr)
        return 1
    print(f"正在逐字实测 BMP（0x20–0xFFFF）… 后端 {c.backend}")
    data = calibrate(c, cfg.encoding)
    path = save_census(data, cfg.encoding)
    print(f"✓ {len(data)} 个单 token 字符 → {path}")
    from collections import Counter as _C

    from .carriers import family_of
    fam = _C(family_of(ch) for ch in data)
    print("  族分布：" + "、".join(f"{k} {v}" for k, v in fam.most_common(10)))
    return 0


def cmd_forge(args) -> int:
    from .glyphforge import ForgeError, font_preview_html, forge

    root = Path(args.root).resolve()
    dic = Dictionary.load(root)
    if not dic.entries:
        print("字典为空，先 mine --apply。", file=sys.stderr)
        return 1
    entries = [{"g": e.g, "p": e.p, "cls": e.cls} for e in dic.entries]
    out_dir = root / "assets"
    try:
        res = forge(entries, out_dir, args.font, args.family)
    except ForgeError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    prev = out_dir / "glyphs-preview.html"
    prev.write_text(
        font_preview_html(entries, res["family"], Path(res["ttf"]).name),
        encoding="utf-8",
    )
    print(f"✓ 字体：{res['ttf']}")
    print(f"✓ 预览：{prev}")
    if res["missed"]:
        print(f"⚠ {len(res['missed'])} 条源字体缺字，已跳过："
              f"{'、'.join(res['missed'][:5])}")
    print("  安装字体后，私有区码位（U+E000 起）即可正常显示；"
          "未安装时请勿在对外输出中显示这些字。")
    return 0


def cmd_doctor(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    dic = Dictionary.load(root)
    codec = _codec(root, cfg, dic)
    c = _counter(cfg)
    ok = True
    print(f"仓颉 v{__version__} · 工程根 {root}")
    print(f"  Python      {sys.version.split()[0]}")
    print(f"  分词后端    {c.backend}", "✓" if c.backend.startswith("tiktoken")
          else "（启发式，建议 pip install tiktoken）")
    print(f"  载体池      {CarrierPool(cfg.carrier_pool, c).describe()}")
    print(f"  字典        {len(dic.entries)} 条")
    samples = [
        "数据库连接池的配置项集中在 config.yaml 里。",
        "```python\npath = 'C:\\\\Users\\\\db'\nprint('数据库连接池')\n```",
        "请把 token 预算控制在 8000 以内，超了就触发压缩。",
        "混合 mixed 文本 with English words 与数字 12345。",
    ]
    for s in samples:
        if not codec.roundtrip_ok(s):
            ok = False
            print("  ✗ 可逆性失败：", s[:40])
    print(f"  可逆性      4/4 {'✓' if ok else '✗'}")
    dup = len(dic.entries) - len({e.g for e in dic.entries})
    if dup:
        ok = False
        print(f"  ✗ 字典存在 {dup} 个重复载体字")
    for e in dic.entries:
        if len(e.g) != 1:
            ok = False
            print(f"  ✗ 载体字不是单个字符：{e.p} → {e.g!r}")
    print(f"  完整性      {'✓' if ok else '✗'}")
    for note in budget.audit(dic, cfg):
        print(f"  · {note}")
    return 0 if ok else 1


def cmd_demo(args) -> int:
    """端到端自演示：临时工程里跑一遍 采集→造字→压缩→报表。"""
    import tempfile

    corpus = (
        "本项目负责把用户上传的文档做向量检索与重排序，然后把检索结果交给大模型生成回答。\n"
        "向量检索的延迟必须控制在 200 毫秒以内，重排序阶段允许额外 80 毫秒。\n"
        "数据库连接池的最大连接数默认 20，超过阈值会触发告警，告警走企业微信机器人。\n"
        "如果向量检索没有召回任何文档，就退化成关键词检索，避免用户拿到空回答。\n"
        "文档解析支持 pdf、docx、markdown；解析失败的文档进入死信队列，每天重试一次。\n"
        "大模型生成回答时要带上引用编号，引用编号对应检索结果的原始段落位置。\n"
        "用户上传的文档会先做敏感词过滤，命中敏感词的文档直接拒绝并记录审计日志。\n"
        "检索结果的重排序模型用 bge-reranker，向量模型用 text-embedding-3-large。\n"
        "整个链路的超时时间设置为 30 秒，超时后返回降级回答并记录错误码。\n"
        "文档解析、向量检索、重排序、大模型生成这四步都要打点，打点数据进 ClickHouse。\n"
    ) * 3
    tmp = Path(tempfile.mkdtemp(prefix="cangjie-demo-"))
    (tmp / "corpus.md").write_text(corpus, encoding="utf-8")
    state_dir(tmp)
    cfg = Config(max_entries=32)
    save_config(cfg, tmp)
    c = _counter(cfg)
    conflicts = "".join(set(corpus))
    pool = CarrierPool(cfg.carrier_pool, c, conflicts, encoding=cfg.encoding)
    cands = plan(corpus, cfg, c, pool.cost, top=12, conflicts=conflicts, pool=pool)
    dic = Dictionary.load(tmp)
    for x in cands:
        dic.add(Entry(g=x.glyph, p=x.phrase, n=x.count, cls=x.cls,
                      cost=x.cost, gcost=x.gcost))
    dic.pool = cfg.carrier_pool
    dic.save(tmp)
    codec = Codec(dic.pairs(), cfg.protect_code, cfg.strict_escape)
    enc, st = codec.encode(corpus)
    dic.touch(st.per_glyph)
    dic.save(tmp)
    assert codec.decode(enc) == corpus, "演示可逆性失败"
    print(f"演示工程：{tmp}")
    print(pool.describe())
    print(table(["字", "原意", "义符", "次", "原价", "字价", "净收益"],
                [[x.glyph, x.phrase, x.cls, str(x.count), f"{x.cost:.1f}",
                  f"{x.gcost:.1f}", f"{x.net:.0f}"] for x in cands]))
    print()
    print(f"压缩前 {c.tokens(corpus)} tok → 压缩后 {c.tokens(enc)} tok "
          f"（{(1 - c.tokens(enc) / c.tokens(corpus)) * 100:.1f}% 节省，"
          f"命中 {st.hits} 处）")
    print(f"可逆校验：{'✓ 无损' if codec.decode(enc) == corpus else '✗'}")
    print("\n压缩样例（前 160 字）：")
    print(enc[:160])
    print("\n还原样例：")
    print(codec.decode(enc[:160]))
    return 0


AGENTS_BLOCK = """<!-- cangjie:begin (managed by `cangjie install --target agents`) -->
## 仓颉 / Cangjie — 省 token 的上下文治理

本项目装了仓颉。它把高频短语折叠成单字（无损可逆），并提供定长工作记忆包。

```bash
cangjie trim -- pytest -q          # 裁掉 90% 噪音输出（先用这个）
cangjie run -- <cmd>               # 跑命令，输出先压一遍再进上下文
cangjie compress -i in.md -o out.md
cangjie expand   -i out.md         # 对外输出前必须还原
cangjie state --set goal="..." --set blocker="..." --add decided="..."
cangjie roll  --add "本轮结论" --tag turn-3
cangjie pack  --budget 2000        # 重建工作记忆包，替换冗长历史
```

规则：
1. 字码只用于内部（思考/草稿/工具输出/记忆包）；给用户的回答与交付文件必须 `expand`。
2. 代码块、URL、路径默认不压缩。
3. 上下文结构 = 系统提示 + 密码本(`cangjie block`) + 工作记忆包 + 最近 k 轮 + 本轮输入。
4. 每 5–8 轮重建一次 `pack`，不要每轮重建（会打掉 prompt cache）。
<!-- cangjie:end -->
"""


def _copy_tree(src: Path, dest: Path, name: str = "") -> Path:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".cangjie", "*.ttf",
                                    ".git", "dist", "build", "*.egg-info")
    if name:
        dest = dest / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, ignore=ignore)
    return dest


def _write_agents_md(path: Path) -> Path:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    begin, end = "<!-- cangjie:begin", "<!-- cangjie:end -->"
    if begin in old and end in old:
        head = old[: old.index(begin)]
        tail = old[old.index(end) + len(end):]
        old = head + tail
    text = old.rstrip() + "\n\n" + AGENTS_BLOCK
    path.write_text(text.lstrip("\n"), encoding="utf-8")
    return path


def cmd_install(args) -> int:
    """安装到任意 Agent 宿主：WorkBuddy / Claude Code / AGENTS.md / 自定义目录。"""
    src = Path(__file__).resolve().parent.parent
    name = args.name or "仓颉"
    target = args.target
    if target == "auto":
        if (Path.home() / ".workbuddy").is_dir():
            target = "workbuddy"
        elif (Path.home() / ".claude").is_dir():
            target = "claude"
        else:
            target = "dir"

    if target in ("workbuddy", "claude"):
        base = {"workbuddy": Path.home() / ".workbuddy" / "skills",
                "claude": Path.home() / ".claude" / "skills"}[target]
        dest = Path(args.dest) / name if args.dest else base / name
    elif target == "dir":
        if not args.dest:
            print("✗ --target dir 需要 --dest 指定目标目录", file=sys.stderr)
            return 2
        dest = Path(args.dest) if args.dest.endswith(name) else Path(args.dest) / name
    elif target == "agents":
        out = _write_agents_md(Path(args.dest or "AGENTS.md"))
        print(f"✓ 已写入 {out.resolve()}")
        print("  任何读 AGENTS.md 的宿主（Codex/Cursor/Cline/自研）都能用。")
        return 0
    else:
        print(f"✗ 未知 target：{target}", file=sys.stderr)
        return 2

    if dest.exists() and not args.force:
        print(f"目标已存在：{dest}（加 --force 覆盖）", file=sys.stderr)
        return 1
    dest = _copy_tree(src, dest)
    print(f"✓ 已安装（{target}）：{dest}")
    if target == "workbuddy":
        print("  重启会话后，技能列表会出现「仓颉」。")
    elif target == "claude":
        print("  Claude Code 会自动发现 ~/.claude/skills 下的 SKILL.md。")
    else:
        print("  把该目录加入宿主的 skills 路径即可。")
    return 0


def cmd_mcp(args) -> int:
    from .mcp_server import main as mcp_main

    return mcp_main()


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cangjie",
        description="仓颉 —— 把高频长词/长句折叠成一个字，让长任务不再越跑越贵。",
    )
    p.add_argument("--version", action="version", version=f"cangjie {__version__}")
    p.add_argument("--root", default=".", help="工程根目录（默认当前目录）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="初始化工程")
    s.add_argument("--pool", choices=list(PRESETS))
    s.add_argument("--max-entries", type=int)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("scan", help="扫描语料并统计 token 量")
    s.add_argument("paths", nargs="*")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("mine", help="挖掘高频短语并造字")
    s.add_argument("paths", nargs="*")
    s.add_argument("--top", type=int)
    s.add_argument("--show", type=int, default=24)
    s.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_mine)

    s = sub.add_parser("block", help="输出可注入上下文的密码本")
    s.add_argument("--format", choices=["compact", "md", "json"], default="compact")
    s.add_argument("--limit", type=int)
    s.add_argument("-o", "--out")
    s.set_defaults(func=cmd_block)

    s = sub.add_parser("compress", help="压缩文本")
    s.add_argument("-i", "--input")
    s.add_argument("-o", "--output")
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(func=cmd_compress)

    s = sub.add_parser("expand", help="还原文本")
    s.add_argument("-i", "--input")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_expand)

    s = sub.add_parser("run", help="运行命令并压缩其输出（上下文节食）")
    s.add_argument("cmd", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("digest", help="把目录压成摘要文件")
    s.add_argument("paths", nargs="*")
    s.add_argument("-o", "--out")
    s.add_argument("--with-block", action="store_true",
                   help="把密码本块一同写入，生成自包含摘要")
    s.set_defaults(func=cmd_digest)

    s = sub.add_parser("pack", help="打包定长工作记忆（密码本+状态卡+滚动结论+摘要）")
    s.add_argument("-o", "--out")
    s.add_argument("--budget", type=int, default=2000)
    s.add_argument("--no-digest", action="store_true")
    s.set_defaults(func=cmd_pack)

    s = sub.add_parser("trim", help="裁剪命令输出（有损，省最多）")
    s.add_argument("-i", "--input")
    s.add_argument("--profile", default="auto",
                   choices=["auto", "pytest", "jest", "npm", "git-diff",
                            "docker", "ls", "build", "generic"])
    s.add_argument("--max-lines", type=int, default=80)
    s.add_argument("--head", type=int, default=6)
    s.add_argument("--tail", type=int, default=12)
    s.add_argument("--full", action="store_true", help="不裁剪，原样输出")
    s.add_argument("cmd", nargs=argparse.REMAINDER, help="-- 之后的命令")
    s.set_defaults(func=cmd_trim)

    s = sub.add_parser("sift", help="上下文安检：检验有用/没用，只删高置信噪音")
    s.add_argument("-i", "--input")
    s.add_argument("--drop", action="store_true", help="生成筛后文本（默认只报告，原文不动）")
    s.add_argument("-o", "--output", help="筛后文本写入文件（配合 --drop）")
    s.add_argument("--show", type=int, default=20, help="报告显示的噪音行数")
    s.add_argument("cmd", nargs=argparse.REMAINDER, help="-- 之后的命令")
    s.set_defaults(func=cmd_sift)

    s = sub.add_parser("seed", help="锁定术语（永不淘汰）")
    s.add_argument("--add", nargs="+")
    s.add_argument("--remove", nargs="+")
    s.add_argument("--list", action="store_true")
    s.set_defaults(func=cmd_seed)

    s = sub.add_parser("state", help="结构化状态卡（目标/阻塞/已决/下一步）")
    s.add_argument("--set", action="append", metavar="K=V")
    s.add_argument("--add", action="append", metavar="K=V|文本")
    s.add_argument("--drop", action="append", metavar="K")
    s.add_argument("--clear", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_state)

    s = sub.add_parser("roll", help="滚动结论（只追加，不重写历史）")
    s.add_argument("--add", nargs="+", metavar="文本")
    s.add_argument("--tag")
    s.add_argument("--budget", type=int)
    s.add_argument("--clear", action="store_true")
    s.set_defaults(func=cmd_roll)

    s = sub.add_parser("stats", help="查看字典与收益")
    s.add_argument("--show", type=int, default=12)
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("report", help="生成 Markdown 报表")
    s.add_argument("-o", "--out")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("prune", help="按预算裁剪字典")
    s.add_argument("--to", type=int)
    s.set_defaults(func=cmd_prune)

    s = sub.add_parser("bench", help="实测各载体池 token 单价")
    s.set_defaults(func=cmd_bench)

    s = sub.add_parser("calibrate", help="逐字实测 BMP 单价，重建载体普查表")
    s.set_defaults(func=cmd_calibrate)

    s = sub.add_parser("forge", help="生成自造字字体（需 fontTools）")
    s.add_argument("--font")
    s.add_argument("--family", default="CangjieCompress")
    s.set_defaults(func=cmd_forge)

    s = sub.add_parser("doctor", help="自检：环境 + 可逆性 + 字典完整性")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("demo", help="内置语料端到端演示")
    s.set_defaults(func=cmd_demo)

    s = sub.add_parser("install", help="安装到任意 Agent 宿主")
    s.add_argument("--target", default="auto",
                   choices=["auto", "workbuddy", "claude", "agents", "dir"])
    s.add_argument("--dest")
    s.add_argument("--name", default="仓颉")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("mcp", help="以 MCP Server 方式运行（stdio，供任意客户端调用）")
    s.set_defaults(func=cmd_mcp)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    if getattr(args, "root", None):
        # init 用字面路径（允许在已有父级工程的子目录里另开一档），其余命令向上找
        args.root = (Path(args.root).resolve() if args.cmd == "init"
                     else find_root(Path(args.root)))
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
