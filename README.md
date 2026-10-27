# 仓颉 · 上下文治理引擎（cangjie 1.0.0）

> 省 token 的正确顺序：**先裁剪，再定长，最后才压字**。
> 版本 1.0.0 ｜ MIT（见仓库根 `LICENSE`）｜ 零必需依赖
> 一个 `SKILL.md` + 一个零依赖 Python 包，可 CLI、可技能、可 MCP、可当库 —— 不绑定任何 Agent 框架。

```bash
pip install -e ".[all]"          # 可选增强：tiktoken 精确计量、fonttools 造字
cd <你的工程>
cangjie init
cangjie trim -- pytest -q        # ① 砍掉 90% 噪音（每次都做的第一件事）
cangjie mine . --apply           # ② 挖词造字（一次性）
cangjie block                    # ③ 密码本，贴进系统提示
cangjie state --set goal="把 p99 压到 200ms" --set blocker="reranker OOM"
cangjie roll  --add "fp16 可上，显存 3.2G" --tag turn-3
cangjie pack  --budget 2000      # ④ 每 5–8 轮重建定长工作记忆包
```

> 不安装也能跑：`python scripts/cj.py xxx`。

---

## 省 token 的量级（实测，别搞反）

| 手段 | 省多少 | 命令 |
|---|---|---|
| 裁掉命令输出噪音 | **70–95%** | `cangjie trim -- <cmd>` |
| 状态卡顶替对话历史 | **90%+**（对那部分历史） | `cangjie state …` |
| 定长工作记忆包 | 把 O(n²) 变 O(1) | `cangjie pack --budget 2000` |
| 字码短语压缩（无损） | **1–4%** | `cangjie mine --apply` |

字码是**打底**——它保证"压完还能一字不差地还原"，专名与数字不丢。
但真正把账单打下来的是前三条。只做字码不做裁剪，等于白干。

实测示例：408 行 pytest 输出 → 26 行，**省 94.5%**，失败行与 traceback 全部保留。

---

## 它到底怎么保证"后期不越来越贵"

```
每轮上下文 = 系统提示 + 密码本(稳定) + 工作记忆包(≤2000 tok, 每 5–8 轮换)
           + 最近 k 轮原始对话 + 本轮输入
```

三段都由常数封顶：字典 ≤128 条、包 ≤ 预算、窗口 = k。于是**每轮成本与任务总轮数无关**。

```bash
cangjie pack --budget 2000     # 密码本 + 状态卡 + 滚动结论 + 摘要，硬预算内
cangjie state --set goal=…     # 68 tok 的状态卡，顶替几千 tok 的历史叙述
cangjie roll  --add "…"        # 只追加不重写，O(本轮) 而非 O(全历史)
```

---

## 装到哪个宿主

```bash
cangjie install --target workbuddy   # ~/.workbuddy/skills/仓颉
cangjie install --target claude      # ~/.claude/skills/仓颉
cangjie install --target agents      # 幂等写入 AGENTS.md（Codex/Cursor/Cline）
cangjie install --target dir --dest /path/to/skills
cangjie mcp                          # MCP stdio server，6 个工具
```

MCP 客户端配置：

```json
{"mcpServers": {"cangjie": {"command": "cangjie", "args": ["mcp"]}}}
```

详见 `docs/安装与集成.md`（含 CI、库调用、团队协作、卸载）。

---

## 目录结构

```
cangjie/
├── SKILL.md                     # 技能入口（Agent Skills 开放格式，任何宿主都读这个）
├── AGENTS.md                    # 只读项目级指令的宿主用（Codex/Cursor/Cline）
├── README.md / README.en.md
├── CHANGELOG.md                 # ★ 版本记录（1.0.0 起）
├── pyproject.toml               # pip 安装 + console_scripts: cangjie
├── requirements.txt             # 可选增强：tiktoken / fonttools
├── assets/icon.svg
├── docs/
│   ├── 仓颉-1.0-开发文档.md      # ★ 设计 + 实测（主体文档）
│   ├── 安装与集成.md             # ★ CLI/WorkBuddy/Claude/AGENTS/MCP/库/CI
│   └── 优化建议.md               # ★ 路线图与反面清单
├── references/{字码协议,造字规范}.md
├── templates/会话协议.md         # 可直接粘进上下文的协议模板
├── cangjie/                     # 零依赖 Python 包（13 模块）
│   ├── trim.py        输出裁剪（有损，省最多）
│   ├── memory.py      状态卡 / 滚动结论 / 定长包
│   ├── codec.py       可逆编解码
│   ├── mine.py        新词发现（频次/邻接熵/凝聚度/极大化）
│   ├── carriers.py    单 token 载体普查与按价值配字
│   ├── state.py       字典与账本
│   ├── budget.py      封顶 / 淘汰 / ROI / 增长模型
│   ├── glyphs.py + glyphforge.py   造字（SVG / PUA TTF）
│   ├── mcp_server.py  MCP Server（手写 JSON-RPC，零依赖）
│   ├── report.py / config.py / tokenizer.py / cli.py
│   └── data/carriers-o200k.json     5211 个单 token 字符（随包）
├── scripts/cj.py                # 免安装入口
├── tests/{test_cangjie.py,bench.py}
└── examples/demo/               # 跑通的样例（4 份文档 + AGENTS.md 接入示例）
```

## 自检

```bash
python tests/test_cangjie.py     # 29 项，应全绿
cangjie doctor                   # 环境 + 可逆性 + 字典完整性
cangjie demo                     # 内置语料端到端演示
python tests/bench.py <路径>     # 你的工程能压多少
```

## 一个必须知道的结论

生僻字不能当压缩载体。实测 `o200k_base`：CJK 扩展 A 区 **3 token/字**、私有区 2.38、
常用汉字 80% 是 2 token；真正便宜的是几何/数学/希腊/西里尔/拉丁扩展里的
**5211 个单 token 字符**（已随包附带普查表）。

所以仓颉的做法是「先量单价，再按短语价值配载体」：值 4 token 的短语配 1 token 的符号，
只值 2 token 的短语**干脆不压**。这条规则就是"字典不膨胀"的根源。

MIT。
