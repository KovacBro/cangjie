#!/usr/bin/env python
"""仓颉 1.0.1 自测：编解码 / 挖掘 / 预算 / 载体 / 造字 / 裁剪 / 筛选 / 记忆 / MCP。

运行：python tests/test_cangjie.py      （零依赖，不需要 pytest）
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cangjie import budget
from cangjie.carriers import CarrierPool, estimate_cost, family_of, load_census
from cangjie.codec import Codec
from cangjie.config import Config, save_config
from cangjie.glyphs import classify, preview_html, spec_for, svg_glyph
from cangjie.memory import Roll, State, build_pack
from cangjie.mine import fragmentish, mine, plan, split_protected
from cangjie.state import Dictionary, Entry
from cangjie.tokenizer import Counter
from cangjie.trim import detect_profile, trim
from cangjie.sift import sift, verdicts


class TestCodec(unittest.TestCase):
    def setUp(self):
        self.pairs = {"数据库连接池": "▀", "向量检索": "▄", "重排序": "█"}
        self.codec = Codec(self.pairs, protect_code=True, strict_escape=True)

    def test_roundtrip_plain(self):
        s = "数据库连接池的最大连接数是 20，向量检索之后再做重排序。"
        enc, st = self.codec.encode(s)
        self.assertLess(len(enc), len(s))
        self.assertEqual(self.codec.decode(enc), s)
        self.assertEqual(st.hits, 3)

    def test_roundtrip_multiline_and_code(self):
        s = (
            "先做向量检索。\n\n"
            "```python\npath = 'C:\\\\Users\\\\db'\nprint('数据库连接池')\n```\n"
            "再做重排序。\n"
        )
        enc, _ = self.codec.encode(s)
        self.assertIn("print('数据库连接池')", enc)
        self.assertIn("C:\\\\Users\\\\db", enc)
        self.assertEqual(self.codec.decode(enc), s)

    def test_literal_carrier_escape(self):
        s = "这是一段含 ▀ 字面量的文字，还有 ▄ 和 █。"
        enc, _ = self.codec.encode(s)
        self.assertEqual(self.codec.decode(enc), s)

    def test_longest_match(self):
        codec = Codec({"向量": "▲", "向量检索": "△"})
        self.assertEqual(codec.encode("向量检索")[0], "△")

    def test_empty_dict(self):
        codec = Codec({})
        self.assertEqual(codec.encode("abc")[0], "abc")
        self.assertEqual(codec.decode("abc"), "abc")


class TestMine(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(min_freq=3)
        self.text = (
            "向量检索的延迟必须控制在 200 毫秒以内。\n"
            "数据库连接池默认 20 个连接。\n"
            "向量检索之后要重排序，重排序用 bge-reranker。\n"
            "如果向量检索为空就退化。\n"
            "数据库连接池要打点。\n"
            "再跑一次向量检索。\n"
        )

    def test_finds_repeated_phrase(self):
        c = Counter("o200k_base")
        got = {x.phrase for x in mine(self.text, self.cfg, c, 1.0, top=20)}
        self.assertIn("向量检索", got)

    def test_rejects_fragment(self):
        self.assertTrue(fragmentish("的了着"))
        self.assertTrue(fragmentish("压到 0.30 AU"))
        self.assertEqual(fragmentish("向量检索"), "")

    def test_protected_spans(self):
        parts = split_protected("a `code` b https://x.y/z c")
        self.assertTrue(any(flag for flag, _ in parts))
        self.assertEqual("".join(s for _, s in parts), "a `code` b https://x.y/z c")

    def test_plan_assigns_glyph(self):
        c = Counter("o200k_base")
        got = plan(self.text, self.cfg, c, top=5, conflicts="".join(set(self.text)))
        self.assertTrue(got)
        for x in got:
            self.assertEqual(len(x.glyph), 1)
            self.assertNotIn(x.glyph, self.text)
            self.assertGreater(x.net, 0)


class TestBudget(unittest.TestCase):
    def test_cap_and_evict(self):
        dic = Dictionary()
        for i in range(20):
            dic.add(Entry(g=chr(0x25A0 + i), p=f"短语{i}", cost=4.0, gcost=1.0,
                          hits=10 if i < 5 else 6))
        cfg = Config(max_entries=8)
        v = budget.enforce(dic, cfg)
        self.assertEqual(len(v.keep), 8)
        self.assertIn(dic.entries[0].g, v.keep)

    def test_pinned_never_dropped(self):
        dic = Dictionary()
        for i in range(6):
            dic.add(Entry(g=chr(0x25A0 + i), p=f"短语{i}", cost=4.0, gcost=1.0,
                          hits=0, pinned=(i == 5)))
        cfg = Config(max_entries=2)
        v = budget.enforce(dic, cfg)
        self.assertIn(dic.entries[5].g, v.keep)

    def test_simulate_monotonic(self):
        s = budget.simulate(turns=20, per_turn_tokens=1000, dict_cost=300, ratio=0.5)
        self.assertLess(s["cangjie"], s["naive"])


class TestCarriers(unittest.TestCase):
    def test_census_loaded(self):
        self.assertGreater(len(load_census("o200k_base")), 1000)

    def test_pick_respects_min_gain(self):
        pool = CarrierPool("auto", Counter("o200k_base"), "", max_cost=1.0)
        self.assertIsNotNone(pool.pick(5.0, min_gain=1.5))
        pool2 = CarrierPool("auto", Counter("o200k_base"), "", max_cost=1.0)
        self.assertIsNone(pool2.pick(2.0, min_gain=1.5))

    def test_unique_assignment(self):
        pool = CarrierPool("auto", Counter("o200k_base"), "")
        got = pool.assign(30, [6.0] * 30)
        self.assertEqual(len(got), len(set(got)))

    def test_family(self):
        self.assertEqual(family_of("▲"), "几何")
        self.assertEqual(family_of("数"), "汉字")
        self.assertGreaterEqual(estimate_cost("数"), 1.0)


class TestGlyphs(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(classify("数据库连接池"), "数")
        self.assertEqual(classify("错误日志"), "错")

    def test_svg(self):
        svg = svg_glyph(spec_for("▲", "向量检索"))
        self.assertIn("<svg", svg)
        self.assertIn("向量检索", preview_html([{"glyph": "▲", "phrase": "向量检索"}]))


class TestTokenizer(unittest.TestCase):
    def test_monotonic(self):
        c = Counter("o200k_base")
        self.assertGreater(c.count("数据库连接池数据库连接池"), c.count("数据库连接池"))

    def test_heuristic_backend(self):
        c = Counter("o200k_base")
        c._enc = None
        self.assertGreater(c.count("hello world 你好"), 0)


class TestTrim(unittest.TestCase):
    def _fake_pytest(self, n=400):
        lines = [f"tests/t_{i // 20}.py::case_{i} PASSED   [ {i % 100:3d}%]" for i in range(n)]
        lines.insert(120, "tests/test_retrieval.py::test_timeout FAILED   [ 30%]")
        lines.insert(121, "FAILED tests/test_retrieval.py::test_timeout - AssertionError: 250 > 200")
        lines += ["Traceback (most recent call last):",
                  '  File "tests/test_retrieval.py", line 42, in test_timeout',
                  "AssertionError: 250 > 200",
                  "====== 1 failed, 399 passed in 32.11s ======"]
        return "\n".join(lines)

    def test_detect_profile(self):
        self.assertEqual(detect_profile(["pytest", "-q"]), "pytest")
        self.assertEqual(detect_profile("npm install"), "npm")
        self.assertEqual(detect_profile("git diff HEAD"), "git-diff")
        self.assertEqual(detect_profile("cat x"), "generic")

    def test_keeps_errors_saves_a_lot(self):
        raw = self._fake_pytest()
        out, st = trim(raw, profile="pytest", max_lines=24)
        self.assertIn("FAILED tests/test_retrieval.py", out)
        self.assertIn("Traceback", out)
        self.assertIn("省略", out)
        self.assertGreater(st.ratio, 0.8)
        self.assertLessEqual(len(out.split("\n")), 32)

    def test_short_text_untouched(self):
        raw = "line1\nline2"
        out, st = trim(raw, profile="generic", max_lines=80)
        self.assertEqual(out, raw)
        self.assertEqual(st.ratio, 0.0)

    def test_collapse_repeats(self):
        raw = "\n".join(["same line"] * 40)
        out, _ = trim(raw, profile="generic", max_lines=10)
        self.assertIn("×", out)
        self.assertLess(len(out), len(raw))


class TestSift(unittest.TestCase):
    RAW = "\n".join([
        "Collecting packages...  45%",
        "================",
        "PASSED tests/test_a.py::t1",
        "ERROR tests/test_b.py::test_x - AssertionError: p99=380ms > 200ms",
        "Collecting more wheels...  78%",
        "已决定：重排序改用 fp16，风险是显存 3.2G",
        "详细配置见 src/config.yaml 与 https://docs.example.com/api",
    ])

    def test_evidence_lines_kept(self):
        vs = {v.text: v for v in verdicts(self.RAW)}
        err = vs["ERROR tests/test_b.py::test_x - AssertionError: p99=380ms > 200ms"]
        self.assertIn("错误", err.reason)
        self.assertIn("指标", err.reason)
        self.assertIn("决策", vs["已决定：重排序改用 fp16，风险是显存 3.2G"].reason)
        self.assertIn("路径",
                      vs["详细配置见 src/config.yaml 与 https://docs.example.com/api"].reason)
        self.assertIn("术语命中", verdicts(self.RAW, terms=("重排序",))[5].reason)
        self.assertEqual(vs["Collecting packages...  45%"].tag, "noise")
        self.assertEqual(vs["================"].tag, "noise")

    def test_drop_never_loses_useful(self):
        out, st = sift(self.RAW, drop=True, terms=("重排序",))
        for useful in ("ERROR tests/test_b.py", "已决定", "https://docs.example.com",
                       "src/config.yaml", "PASSED tests/test_a.py"):
            self.assertIn(useful, out)
        self.assertNotIn("Collecting", out)
        self.assertIn("略去", out)
        self.assertLess(st.chars_after, st.chars_before)

    def test_neutral_lines_survive(self):
        raw = "这是普通的叙述行一。\n这是普通的叙述行二。"
        vs = verdicts(raw)
        self.assertTrue(all(v.tag == "neutral" for v in vs))
        out, st = sift(raw, drop=True)
        self.assertEqual(out, raw)
        self.assertEqual(st.noise, 0)

    def test_code_fence_protected(self):
        raw = "```log\n================\nstep 1 ok\n```"
        vs = verdicts(raw)
        self.assertTrue(all(v.reason == "代码块" for v in vs))
        out, _ = sift(raw, drop=True)
        self.assertEqual(out, raw)

    def test_dup_collapsed(self):
        raw = "\n".join(["p99 = 380ms"] * 6)
        vs = verdicts(raw)
        self.assertEqual(sum(1 for v in vs if v.tag == "dup"), 5)
        out, st = sift(raw, drop=True)
        self.assertEqual(out, "p99 = 380ms")
        self.assertEqual(st.dup, 5)


class TestMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cj-mem-"))
        (self.tmp / ".cangjie").mkdir()

    def _seed_dict(self):
        dic = Dictionary()
        dic.add(Entry(g="▀", p="向量检索", cost=4.0, gcost=1.0, hits=5))
        dic.save(self.tmp)
        return dic

    def test_state_roundtrip(self):
        st = State()
        st.set("goal", "把延迟压到 200ms")
        st.set("blocker", "reranker OOM")
        st.add("decided", "改用 fp16")
        st.add("next", "跑压测")
        st.add("facts", "队列=dead-letter")
        st.save(self.tmp)
        got = State.load(self.tmp)
        self.assertEqual(got.goal, "把延迟压到 200ms")
        self.assertIn("改用 fp16", got.decided)
        self.assertEqual(got.facts["队列"], "dead-letter")
        txt = got.render()
        self.assertIn("目标", txt)
        self.assertIn("下一步", txt)

    def test_state_bounded(self):
        st = State()
        for i in range(30):
            st.add("decided", f"决策{i}")
        self.assertLessEqual(len(st.decided), 8)

    def test_roll_append_and_render(self):
        roll = Roll(self.tmp)
        for i in range(5):
            roll.add(f"结论{i}", tag=f"t{i}")
        again = Roll(self.tmp)
        self.assertEqual(len(again.chunks), 5)
        self.assertIn("结论4", again.render())

    def test_pack_within_budget(self):
        self._seed_dict()
        cfg = Config()
        save_config(cfg, self.tmp)
        st = State()
        st.set("goal", "把向量检索延迟压到 200ms")
        st.save(self.tmp)
        Roll(self.tmp).add("确认 fp16 可上，显存 3.2G", tag="turn-3")
        (self.tmp / ".cangjie" / "digest.txt").write_text(
            "这是一段很长的项目摘要。" * 200, encoding="utf-8")
        c = Counter(cfg.encoding)
        for budget_tok in (300, 800, 2000):
            pack, _ = build_pack(self.tmp, cfg, counter=c, budget=budget_tok)
            got = c.count(pack)
            self.assertLessEqual(got, budget_tok + 12, f"budget={budget_tok} got={got}")
            self.assertIn("<仓颉v1", pack)
            self.assertIn("目标", pack)


class TestMcp(unittest.TestCase):
    def test_handshake_and_tools(self):
        root = Path(__file__).resolve().parent.parent
        tmp = Path(tempfile.mkdtemp(prefix="cj-mcp-"))
        (tmp / ".cangjie").mkdir()
        dic = Dictionary()
        dic.add(Entry(g="▀", p="向量检索", cost=4.0, gcost=1.0))
        dic.save(tmp)
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "cangjie_compress",
                        "arguments": {"text": "向量检索延迟", "root": str(tmp)}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "cangjie_expand",
                        "arguments": {"text": "▀延迟", "root": str(tmp)}}},
        ]
        payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in reqs) + "\n"
        proc = subprocess.run(
            [sys.executable, "-m", "cangjie.mcp_server"],
            input=payload, capture_output=True, text=True, encoding="utf-8",
            cwd=str(root), timeout=90,
        )
        out = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "cangjie")
        names = [t["name"] for t in out[1]["result"]["tools"]]
        self.assertIn("cangjie_compress", names)
        self.assertIn("cangjie_sift", names)
        self.assertIn("向量检索延迟", out[3]["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
