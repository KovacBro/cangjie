---
name: 仓颉
description: 省 token 的上下文治理引擎（框架无关）。三件事：① trim 裁掉命令输出的 90% 噪音；② 用「状态卡 + 滚动结论 + 定长包」把每轮成本钉成常数；③ 把高频词/长句折叠成单字（无损可逆，专名不丢）。支持中/英/混排，代码块与路径默认不压，可 CLI、可 MCP、可当库调用。当用户提到"省 token""上下文太长/快满了""怎么压缩提示词""长任务越跑越贵""工具输出太多""做个工作记忆/上下文节食"，或要跨几十轮维护项目状态、反复读同一批文件，或想检验一段上下文里什么有用、什么没用（上下文安检 sift）时使用。
agent_created: true
---

# 仓颉 · 先裁剪，再压缩，最后定长

**省 token 的量级排序**（实测，别搞反）：

| 手段 | 省多少 | 命令 |
|---|---|---|
| 裁掉命令输出噪音 | **70–95%** | `cangjie trim -- <cmd>` |
| 状态卡顶替对话历史 | **90%+**（对那部分历史） | `cangjie state …` |
| 定长工作记忆包 | 把 O(n²) 变 O(1) | `cangjie pack --budget 2000` |
| 字码短语压缩 | **1–4%**（无损打底） | `cangjie mine --apply` |

字码是**打底**，不是主力。只做字码、不做裁剪与定长，等于白干。

## 三条铁律

1. **对外输出必须还原**。字码只活在思考、草稿、工具输出、记忆包里；
   给用户看的回答、交付文件、commit message 一律 `cangjie expand`。
2. **代码块不压**。围栏代码、行内代码、URL、路径默认保护——改坏了比省 token 贵得多。
3. **不要每轮重建包**。密码本与包要逐字节稳定，每轮重建会打掉 prompt cache，省 4% 赔 90%。

## 标准流程

```bash
cangjie init                                    # 建 .cangjie/
cangjie trim -- pytest -q                       # ① 先裁噪音（每次都做）
cangjie mine . --apply                          # ② 挖词造字（一次性）
cangjie block                                   # ③ 密码本，贴进系统提示
cangjie state --set goal="…" --set blocker="…"  # ④ 状态卡（随时更新）
cangjie roll  --add "本轮结论" --tag turn-3      # ⑤ 滚动结论（每轮一行）
cangjie pack  --budget 2000                     # ⑥ 每 5–8 轮重建工作记忆包
```

上下文结构：

```
系统提示 + 密码本(稳定) + 工作记忆包(每 5–8 轮换) + 最近 k 轮 + 本轮输入
```

## 命令速查

| 想做的事 | 命令 |
|---|---|
| 裁输出（有损，最省） | `cangjie trim -- <cmd>` / `cangjie trim -i log.txt --profile pytest` |
| 上下文安检（检验有用/没用） | `cangjie sift -i ctx.txt` / `cangjie sift --drop -- <cmd>` |
| 跑命令并压缩输出 | `cangjie run -- <cmd>` |
| 状态卡 | `cangjie state --set goal=… --add decided=… --add next=… --set fact.k=v` |
| 滚动结论 | `cangjie roll --add "…" --tag turn-3` / `cangjie roll --budget 300` |
| 定长包 | `cangjie pack --budget 2000 [--no-digest]` |
| 锁定术语（永不淘汰） | `cangjie seed --add 术语A 术语B [--list]` |
| 压缩/还原 | `cangjie compress -i a.md` / `cangjie expand -i b.md` |
| 挖词 | `cangjie mine . --apply [--top 64]` |
| 体检 | `cangjie doctor` |
| 载体单价 | `cangjie bench` |
| 报表 | `cangjie report` |
| 自测 | `python tests/test_cangjie.py` |

## 装到哪个宿主

```bash
cangjie install --target workbuddy   # ~/.workbuddy/skills/仓颉
cangjie install --target claude      # ~/.claude/skills/仓颉
cangjie install --target agents      # 写入 AGENTS.md（Codex/Cursor/Cline 通用）
cangjie install --target dir --dest /path/to/skills
cangjie mcp                          # 以 MCP Server 运行（任意 MCP 客户端）
```

本 skill 不含任何宿主专有逻辑：一个 `SKILL.md` + 一个零依赖 Python 包，
`python scripts/cj.py` 可直接跑，`import cangjie` 可当库用。

## 判断该不该用

| 情况 | 用不用 |
|---|---|
| 跑测试/构建/日志，输出很长 | ✅ 只用 `trim` 就够 |
| 跨 10 轮以上、反复交代同一套背景 | ✅ 全套 |
| 上下文快满、开始被截断 | ✅ 立刻 `state` + `pack` |
| 一次性长文读一遍就丢 | ❌ 别挖词，只 `trim` |

## 边界

- 别压用户的原始输入（那是证据）。
- 别把字码留在对外文本或共享文档里。
- 别为了压缩率调低 `min_gain`（<1.0 会让字典爆炸，净收益转负）。
- 别用生僻字当载体：实测 CJK 扩展 A 区是 **3 token/字**，比被压的短语还贵。

深读：`docs/仓颉-1.0-开发文档.md`（设计与实测）、`CHANGELOG.md`（版本与实测数据）、
`docs/安装与集成.md`（各宿主）、`references/字码协议.md`、`docs/优化建议.md`。
