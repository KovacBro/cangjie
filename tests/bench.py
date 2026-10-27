#!/usr/bin/env python
"""压缩率基准：给定语料路径，打印不同字典上限下的实测压缩率。

用法：
    python tests/bench.py                      # 用 E:/skills/仓颉 自身源码
    python tests/bench.py <路径> [<路径>...]    # 用指定工程
    python tests/bench.py <路径> --cap 64      # 只测一个上限
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cangjie.carriers import CarrierPool  # noqa: E402
from cangjie.codec import Codec  # noqa: E402
from cangjie.config import Config  # noqa: E402
from cangjie.mine import gather, plan  # noqa: E402
from cangjie.tokenizer import Counter  # noqa: E402


def run(paths: list[str], caps: list[int], reads: tuple[int, ...] = (1, 5, 20)) -> None:
    c = Counter("o200k_base")
    print(f"分词后端：{c.backend}\n")
    cfg0 = Config()
    text, st = gather(paths, cfg0)
    if not text.strip():
        print("语料为空。")
        return
    print(f"语料：{st['files']} 文件 / {len(text)} 字符 / {c.count(text):.0f} token\n")
    header = (f"{'上限':>4} {'条目':>4} {'压缩前':>8} {'压缩后':>8} {'单次':>6} "
              f"{'字典成本':>8} {'净省':>7} " + " ".join(f"读{r}次" for r in reads))
    print(header)
    print("-" * len(header))
    for cap in caps:
        cfg = Config(max_entries=cap)
        pool = CarrierPool(cfg.carrier_pool, c, "".join(set(text)),
                           encoding=cfg.encoding)
        cands = plan(text, cfg, c, pool.cost, top=cap,
                     conflicts="".join(set(text)), pool=pool)
        codec = Codec({x.phrase: x.glyph for x in cands}, cfg.protect_code,
                      cfg.strict_escape)
        enc, _ = codec.encode(text)
        assert codec.decode(enc) == text, "可逆性失败"
        before, after = c.count(text), c.count(enc)
        dict_cost = c.count("\n".join(f"{x.glyph}={x.phrase}" for x in cands))
        row = (f"{cap:>4} {len(cands):>4} {before:>8.0f} {after:>8.0f} "
               f"{(1 - after / before) * 100:>5.1f}% {dict_cost:>8.0f} "
               f"{before - after - dict_cost:>7.0f} ")
        for r in reads:
            saved = 1 - (after * r + dict_cost) / (before * r)
            row += f"{saved * 100:>6.1f}%"
        print(row)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    caps = [32, 64, 128, 256]
    for a in sys.argv[1:]:
        if a.startswith("--cap"):
            caps = [int(a.split("=")[1])] if "=" in a else caps
    if not args:
        args = [str(Path(__file__).resolve().parent.parent)]
        print("（未指定路径，用仓颉自身源码）\n")
    run(args, caps)


if __name__ == "__main__":
    main()
