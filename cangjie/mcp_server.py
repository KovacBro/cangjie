"""MCP Server（零依赖）：把仓颉暴露给任何支持 MCP 的客户端。

手写 JSON-RPC 2.0 over stdio（换行分帧），不需要 mcp-sdk。

启动：
    cangjie mcp
    python -m cangjie.mcp_server

客户端配置（Claude Desktop / Cursor / Cline / 任意 MCP Host）：
    {
      "mcpServers": {
        "cangjie": {"command": "cangjie", "args": ["mcp"]}
      }
    }

暴露 6 个工具：compress / expand / block / stats / trim / pack。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__
from .codec import Codec
from .config import find_root, load_config
from .memory import write_pack
from .state import Dictionary, append_ledger
from .tokenizer import Counter
from .trim import detect_profile, trim

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "cangjie_compress",
        "description": "用项目字表把文本压缩成字码（逐字节可逆，代码块/URL/路径不压）。"
                       "返回压缩文本与 token 统计。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待压缩文本"},
                "root": {"type": "string", "description": "工程根（默认自动向上查找）"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "cangjie_expand",
        "description": "把字码还原成人类可读原文。对外输出前必须调用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "root": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "cangjie_block",
        "description": "输出可直接注入系统提示的密码本块（<仓颉vN> … </仓颉>）。",
        "inputSchema": {"type": "object", "properties": {"root": {"type": "string"}}},
    },
    {
        "name": "cangjie_stats",
        "description": "字典规模、命中数、ROI、净收益。",
        "inputSchema": {"type": "object", "properties": {"root": {"type": "string"}}},
    },
    {
        "name": "cangjie_trim",
        "description": "裁剪冗长的命令输出（pytest/npm/git-diff/docker/build/generic），"
                       "通常省 70–95%。有损。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "profile": {"type": "string", "default": "auto"},
                "max_lines": {"type": "integer", "default": 80},
            },
            "required": ["text"],
        },
    },
    {
        "name": "cangjie_pack",
        "description": "生成定长工作记忆包（密码本 + 状态卡 + 滚动结论 + 摘要）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string"},
                "budget": {"type": "integer", "default": 2000},
            },
        },
    },
]


def _root(args: dict) -> Path:
    return Path(args["root"]).resolve() if args.get("root") else find_root()


def _counter(root: Path) -> Counter:
    return Counter(load_config(root).encoding)


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


# ---------------------------------------------------------------- 工具实现

def tool_compress(args: dict) -> dict:
    root = _root(args)
    cfg = load_config(root)
    dic = Dictionary.load(root)
    codec = Codec(dic.pairs(), cfg.protect_code, cfg.strict_escape)
    text = args.get("text", "")
    out, st = codec.encode(text)
    dic.touch(st.per_glyph)
    dic.save(root)
    c = _counter(root)
    before, after = c.count(text), c.count(out)
    append_ledger({"event": "compress", "via": "mcp", "hits": st.hits,
                   "tokens_before": round(before, 1), "tokens_after": round(after, 1)}, root)
    note = f"\n\n[仓颉] {int(before)}→{int(after)} tok · 命中 {st.hits} 处 · 对外输出前请 expand"
    return _ok(out + note)


def tool_expand(args: dict) -> dict:
    root = _root(args)
    cfg = load_config(root)
    codec = Codec(Dictionary.load(root).pairs(), cfg.protect_code, cfg.strict_escape)
    return _ok(codec.decode(args.get("text", "")))


def tool_block(args: dict) -> dict:
    root = _root(args)
    dic = Dictionary.load(root)
    if not dic.entries:
        return _err("字典为空：先运行 `cangjie mine --apply`。")
    return _ok(dic.block("compact"))


def tool_stats(args: dict) -> dict:
    root = _root(args)
    dic = Dictionary.load(root)
    s = dic.summary()
    lines = [
        f"字典 v{dic.version} · 池 {dic.pool} · {s['entries']} 条（锁定 {s['pinned']}）",
        f"命中 {s['hits']} · 字典成本 {s['entry_cost']} tok · 净收益 {s['net_saved']} tok "
        f"· ROI {s['roi']}",
    ]
    for e in sorted(dic.entries, key=lambda x: -x.net)[:12]:
        lines.append(f"  {e.g} = {e.p}  ({e.cost:.1f}→{e.gcost:.1f} tok, 命中 {e.hits})")
    return _ok("\n".join(lines))


def tool_trim(args: dict) -> dict:
    text = args.get("text", "")
    profile = args.get("profile", "auto")
    profile = detect_profile(profile) if profile == "auto" else profile
    out, st = trim(text, profile=profile, max_lines=int(args.get("max_lines", 80)))
    return _ok(out + f"\n\n[仓颉trim] {st.summary()}")


def tool_pack(args: dict) -> dict:
    root = _root(args)
    path, detail = write_pack(root, budget=int(args.get("budget", 2000)))
    body = path.read_text(encoding="utf-8")
    return _ok(f"{body}\n\n[仓颉] {int(detail['total'])} tok / 预算 "
               f"{int(detail['budget'])} tok")


HANDLERS = {
    "cangjie_compress": tool_compress,
    "cangjie_expand": tool_expand,
    "cangjie_block": tool_block,
    "cangjie_stats": tool_stats,
    "cangjie_trim": tool_trim,
    "cangjie_pack": tool_pack,
}


# ---------------------------------------------------------------- JSON-RPC

def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "cangjie", "version": __version__},
            },
        }
    if method in ("notifications/initialized", "initialized", "exit"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "shutdown":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        fn = HANDLERS.get(name)
        if not fn:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": f"未知工具 {name}"}}
        try:
            return {"jsonrpc": "2.0", "id": mid,
                    "result": fn(params.get("arguments") or {})}
        except Exception as exc:  # 工具内部错误按 MCP 约定走 isError，不断连接
            return {"jsonrpc": "2.0", "id": mid,
                    "result": _err(f"{type(exc).__name__}: {exc}")}
    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        return {"jsonrpc": "2.0", "id": mid, "result": {key: []}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"不支持的方法 {method}"}}


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
