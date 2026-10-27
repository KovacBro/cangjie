"""造字层：语义分类 + 合体字规格 + 可视图样。

仓颉只做两件事：
1. **选形**——按短语语义挑一个"义符"（左偏旁）与一个"声符"（右部件），
   拼出"形声字"式的合体字，让压缩后的文本仍然"看起来是字、看得出意思"。
2. **出图**——不依赖任何字体文件也能渲染 SVG 预览（预览页 / 报告插图）；
   需要真正可输入的字符时交给 ``glyphforge`` 生成 TTF 并占用私有区码位。
"""
from __future__ import annotations

import html
from dataclasses import dataclass

# 12 个义符（形旁）——覆盖工程语料的主要语义场
CLASS_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("数", ("数据", "统计", "指标", "数量", "计数", "比例", "概率", "评分", "token",
            "data", "count", "metric", "score", "ratio", "index")),
    ("错", ("错误", "异常", "失败", "告警", "调试", "排查", "崩溃", "报错",
            "error", "exception", "fail", "warn", "debug", "crash", "bug")),
    ("令", ("规则", "策略", "指令", "命令", "约束", "校验", "协议", "规范", "标准",
            "rule", "policy", "command", "constraint", "valid", "protocol", "spec")),
    ("器", ("组件", "框架", "引擎", "模块", "工具", "系统", "插件", "服务", "库",
            "component", "framework", "engine", "module", "tool", "system", "plugin", "sdk")),
    ("网", ("网络", "接口", "请求", "响应", "链接", "端口", "路由", "网关", "同步",
            "api", "http", "request", "response", "url", "route", "socket", "sync")),
    ("人", ("用户", "角色", "权限", "团队", "客户", "成员", "账号", "登录",
            "user", "role", "permission", "team", "account", "login", "auth")),
    ("时", ("时间", "日期", "周期", "频率", "并发", "延迟", "超时", "调度", "轮次",
            "time", "date", "period", "interval", "timeout", "delay", "schedule", "tick")),
    ("值", ("配置", "参数", "变量", "常量", "阈值", "设置", "默认", "开关", "选项",
            "config", "param", "arg", "const", "threshold", "option", "setting", "flag")),
    ("图", ("图形", "界面", "页面", "样式", "布局", "颜色", "渲染", "画布", "动画",
            "ui", "ux", "page", "style", "layout", "color", "render", "canvas", "svg")),
    ("文", ("文件", "文档", "文本", "路径", "目录", "存储", "仓库", "资源", "缓存",
            "file", "doc", "text", "path", "dir", "storage", "repo", "asset", "cache")),
    ("言", ("提示", "消息", "注释", "对话", "语言", "说明", "日志", "字符串",
            "prompt", "message", "comment", "log", "string")),
    ("事", ("任务", "流程", "步骤", "事件", "计划", "阶段", "方案", "需求", "功能",
            "task", "flow", "step", "event", "plan", "stage", "feature", "requirement")),
]

DEFAULT_CLASS = "事"


def classify(phrase: str) -> str:
    """按关键词为短语指派义符。

    打分 = 命中关键词的字符总长（越长越具体），同分时按表内顺序取先者——
    表内顺序即"具体领域优先"的优先级，泛语言类（言）与兜底（事）排最后。
    """
    low = phrase.lower()
    best, best_score = DEFAULT_CLASS, 0
    for cls, words in CLASS_KEYWORDS:
        score = sum(len(w) for w in words if w in low)
        if score > best_score:
            best, best_score = cls, score
    return best


def radical_of(phrase: str) -> str:
    return classify(phrase)


def phon_of(phrase: str) -> str:
    """声符：取短语首个"有意义"的字符。"""
    for ch in phrase:
        if ch.strip() and not ch.isdigit():
            return ch
    return phrase[:1] or "〇"


@dataclass
class GlyphSpec:
    glyph: str
    phrase: str
    cls: str
    phon: str
    layout: str = "lr"  # lr=左右结构, tb=上下结构

    @property
    def codepoint(self) -> str:
        return f"U+{ord(self.glyph):04X}"


def spec_for(glyph: str, phrase: str, cls: str = "") -> GlyphSpec:
    cls = cls or classify(phrase)
    phon = phon_of(phrase)
    layout = "tb" if _is_wide(phon) else "lr"
    return GlyphSpec(glyph=glyph, phrase=phrase, cls=cls, phon=phon, layout=layout)


def _is_wide(ch: str) -> bool:
    """笔画宽的字（如 一/大/日）适合上下结构，简单启发。"""
    return ch in "一丁七大日目田口山王正天地人入八刀力方"


# ---------------------------------------------------------------- SVG 图样

def svg_glyph(g: GlyphSpec, size: int = 96, show_meta: bool = True) -> str:
    """把合体字画成 SVG：左义符 + 右声符，形如形声字。"""
    s = size
    pad = s * 0.06
    half = (s - 2 * pad) / 2
    if g.layout == "tb":
        lx, ly, rx, ry = pad, pad, pad, pad + half
        lw, lh = s - 2 * pad, half
        rw, rh = s - 2 * pad, half
    else:
        lx, ly = pad, pad
        rx, ry = pad + half, pad
        lw, lh = half, s - 2 * pad
        rw, rh = half, s - 2 * pad
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" viewBox="0 0 {s} {s}" role="img" aria-label="{html.escape(g.glyph)}">
  <rect x="1" y="1" width="{s-2}" height="{s-2}" rx="{s*0.08}" fill="none" stroke="currentColor" stroke-opacity="0.25"/>
  <text x="{lx}" y="{ly}" font-family="'KaiTi','STKaiti','SimSun',serif" font-size="{lh*0.96}"
        fill="currentColor" fill-opacity="0.55" textLength="{lw*0.92}" lengthAdjust="spacingAndGlyphs"
        dominant-baseline="text-before-edge">{html.escape(g.cls)}</text>
  <text x="{rx}" y="{ry}" font-family="'KaiTi','STKaiti','SimSun',serif" font-size="{rh*0.96}"
        fill="currentColor" textLength="{rw*0.92}" lengthAdjust="spacingAndGlyphs"
        dominant-baseline="text-before-edge">{html.escape(g.phon)}</text>
</svg>"""


def preview_html(entries: list[dict], title: str = "仓颉字表 v1.0", theme: str = "light") -> str:
    """生成造字预览页（可直接双击打开）。entries: [{glyph, phrase, cls, cost, count}]"""
    dark = theme == "dark"
    bg = "#0f1419" if dark else "#fbfaf7"
    fg = "#e8e6e1" if dark else "#1f2328"
    card = "#161c22" if dark else "#ffffff"
    line = "#2a333c" if dark else "#e6e2d9"
    rows = []
    for raw in entries:
        e = {"glyph": raw.get("glyph") or raw.get("g", ""),
             "phrase": raw.get("phrase") or raw.get("p", ""),
             "cls": raw.get("cls", ""),
             "count": raw.get("count", raw.get("n", "-")),
             "cost": raw.get("cost", "-")}
        sp = spec_for(e["glyph"], e["phrase"], e["cls"])
        rows.append(f"""  <div class="card">
    <div class="fig" style="color:{fg}">{svg_glyph(sp, 72, False)}</div>
    <div class="body">
      <code class="cp">{sp.codepoint}</code>
      <div class="ph">{html.escape(e['phrase'])}</div>
      <div class="meta">义符 {html.escape(sp.cls)} · 声符 {html.escape(sp.phon)} · {e.get('count','-')} 次 · 原价 {e.get('cost','-')} tok → 1 tok</div>
    </div>
  </div>""")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: {'dark' if dark else 'light'}; }}
  body {{ margin:0; padding:32px; background:{bg}; color:{fg};
         font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif; }}
  h1 {{ font-size:20px; font-weight:600; letter-spacing:.04em; margin:0 0 6px; }}
  .sub {{ opacity:.6; font-size:13px; margin-bottom:24px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:12px; }}
  .card {{ display:flex; gap:14px; align-items:center; background:{card};
           border:1px solid {line}; border-radius:14px; padding:12px 14px; }}
  .fig {{ flex:0 0 72px; }}
  .cp {{ font-size:11px; opacity:.5; font-family:ui-monospace,Consolas,monospace; }}
  .ph {{ font-size:15px; font-weight:600; margin:2px 0 4px; word-break:break-all; }}
  .meta {{ font-size:11.5px; opacity:.6; }}
</style></head>
<body>
  <h1>{html.escape(title)}</h1>
  <div class="sub">左义符 + 右声符 = 合体字。字表本身也计入 token 成本，所以宁少勿滥。</div>
  <div class="grid">
{chr(10).join(rows)}
  </div>
</body></html>"""


__all__ = ["classify", "radical_of", "phon_of", "GlyphSpec", "spec_for",
           "svg_glyph", "preview_html", "CLASS_KEYWORDS"]
