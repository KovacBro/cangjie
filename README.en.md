# Cangjie (仓颉) — Context Budgeting & Semantic Compression for Long LLM Tasks

> A framework-agnostic Agent Skill. One `SKILL.md` + one dependency-free Python package.
> Works as a CLI, a pip package, a skill in any host, or an MCP server.

The name is a pun: 仓颉 (Cāngjié) is the legendary inventor of Chinese characters.
This tool does the reverse — it collapses recurring phrases *into* single characters.

---

## What it actually saves (measured, don't get this backwards)

| Technique | Saving | Command |
|---|---|---|
| Trim command output noise | **70–95%** | `cangjie trim -- <cmd>` |
| Structured state card replacing chat history | **90%+** of that history | `cangjie state …` |
| Fixed-budget working-memory pack | turns O(n²) into O(1) | `cangjie pack --budget 2000` |
| Phrase→character codec (lossless) | **1–4%** | `cangjie mine --apply` |

The codec is the *baseline*, not the main weapon. Trimming and budgeting are where the tokens are.

---

## Quick start

```bash
pip install -e ".[all]"      # optional extras: tiktoken (exact counting), fonttools (font forging)
cd <your-project>
cangjie init
cangjie trim -- pytest -q                 # kill 90% of the noise
cangjie mine . --apply                    # build the codebook
cangjie block                             # paste into the system prompt
cangjie state --set goal="cut p99 to 200ms" --set blocker="reranker OOM"
cangjie roll  --add "fp16 works, VRAM 3.2G" --tag turn-3
cangjie pack  --budget 2000               # rebuild the working memory pack
```

Context layout:

```
system prompt + codebook (stable) + working-memory pack (changes every 5–8 turns)
              + last k turns (raw) + current input
```

## Why the codebook is lossless but still worth it

```bash
echo "向量检索之后再重排序" | cangjie compress    # → ▲之后再█   (26→20 tokens)
echo "▲之后再█"        | cangjie expand      # → 向量检索之后再重排序
```

`decode(encode(x)) == x`, byte for byte — including fenced code blocks, Windows paths
and regexes (they are protected by default and never rewritten).

The point is not the ratio. It is that a compressed memory can still be **restored exactly**,
so proper nouns, numbers and identifiers survive any amount of summarization.

## Install into any host

```bash
cangjie install --target workbuddy     # ~/.workbuddy/skills/仓颉
cangjie install --target claude        # ~/.claude/skills/仓颉
cangjie install --target agents        # writes a managed block into AGENTS.md
cangjie install --target dir --dest /path/to/skills
cangjie mcp                            # MCP server over stdio (6 tools)
```

See `docs/安装与集成.md` for MCP client JSON, library usage, CI and team setup.

## The one design decision worth knowing

A rare CJK ideograph (Extension A) costs **3 tokens** in `o200k_base` — *more than the phrase
you were compressing*. Private-use glyphs average 2.38. Most common Hanzi cost 2.
The genuinely cheap carriers are single-token symbols (geometric, math, Greek, Cyrillic,
Latin-Extended): 5211 of them across the BMP, shipped as `cangjie/data/carriers-o200k.json`.

So carriers are **measured first, then assigned by phrase value**: a 4-token phrase gets a
1-token mark, a 2-token phrase gets nothing at all. That single rule is why the dictionary
stops growing while the project keeps growing.

## Test

```bash
python tests/test_cangjie.py     # 29 tests, no pytest required
cangjie doctor                   # reversibility + dictionary integrity
cangjie demo                     # end-to-end demo on a built-in corpus
python tests/bench.py <path>     # compression ratio on your own project
```

## Docs

- `docs/仓颉-1.0-开发文档.md` — full design + measurements (Chinese)
- `docs/优化建议.md` — roadmap and anti-patterns (Chinese)
- `docs/安装与集成.md` — per-host integration
- `CHANGELOG.md` — version history
- `references/字码协议.md` — codebook format and output gate rules

MIT.
