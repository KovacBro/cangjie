"""报表：把"省了多少"讲清楚。"""
from __future__ import annotations

from pathlib import Path

from . import budget
from .config import Config
from .state import Dictionary, read_ledger


def _sparkline(values: list[float], width: int = 48,
               bounds: tuple[float, float] | None = None) -> str:
    """迷你折线。两条曲线对比时必须传同一组 bounds，否则各自归一化会看起来一模一样。"""
    if not values:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    step = max(1, len(values) // width)
    sampled = values[::step][:width]
    lo, hi = bounds if bounds else (min(sampled), max(sampled))
    if hi - lo < 1e-9:
        return blocks[0] * len(sampled)
    return "".join(blocks[min(7, int((v - lo) / (hi - lo) * 7))] for v in sampled)


def build(dic: Dictionary, cfg: Config, root: Path) -> str:
    s = dic.summary()
    ledger = read_ledger(root)
    comp_events = [e for e in ledger if e.get("event") == "compress"]
    total_before = sum(e.get("tokens_before", 0) for e in comp_events)
    total_after = sum(e.get("tokens_after", 0) for e in comp_events)

    lines: list[str] = []
    lines.append("# 仓颉压缩报表\n")
    lines.append(f"- 工程根：`{root}`")
    lines.append(f"- 字典：v{dic.version} · 载体池 `{dic.pool}` · {s['entries']} 条 · "
                 f"平均原短语 {s['avg_phrase_cost']} tok")
    lines.append(f"- 累计命中：{s['hits']} 次；字典成本 {s['entry_cost']} tok；"
                 f"净收益 **{s['net_saved']} tok**；ROI = {s['roi']}")
    if total_before:
        lines.append(f"- 实测压缩：{int(total_before)} → {int(total_after)} tok "
                     f"（{(1 - total_after / total_before) * 100:.1f}% 节省）")
    lines.append("")

    # 增长模型
    sim = budget.simulate(
        turns=30,
        per_turn_tokens=1200,
        dict_cost=max(s["entry_cost"], 1.0),
        ratio=0.55,
    )
    lines.append("## 上下文增长模型（30 轮，每轮 1200 tok 新增）\n")
    lines.append(f"- 不压缩累计：{sim['naive']} tok")
    lines.append(f"- 仓颉累计：{sim['cangjie']} tok（节省 {sim['saved']} tok，"
                 f"成本降至 {sim['ratio'] * 100:.0f}%）")
    naive_curve = [c[0] for c in sim["turn_costs"]]
    cj_curve = [c[1] for c in sim["turn_costs"]]
    vmax = max(naive_curve + cj_curve) or 1.0
    lines.append(f"- 每轮成本曲线 不压缩 `{_sparkline(naive_curve, bounds=(0, vmax))}`")
    lines.append(f"- 每轮成本曲线 仓颉   `{_sparkline(cj_curve, bounds=(0, vmax))}`")
    lines.append("")

    # 条目表
    if dic.entries:
        lines.append("## 字表（按净收益）\n")
        lines.append("| 字 | 原意 | 义符 | 次 | 单价 | 命中 | 净收益 |")
        lines.append("|---|---|---|---|---|---|---|")
        for e in sorted(dic.entries, key=lambda x: -x.net)[:30]:
            lines.append(f"| {e.g} | {e.p} | {e.cls} | {e.n} | {e.cost:.1f} | "
                         f"{e.hits} | {e.net:.1f} |")
        lines.append("")

    notes = budget.audit(dic, cfg)
    lines.append("## 健康检查\n")
    lines.extend(f"- {n}" for n in notes) if notes else lines.append("- 一切正常。")
    lines.append("")
    lines.append("## 复制即用\n")
    lines.append("```")
    lines.append(dic.block("compact"))
    lines.append("```")
    return "\n".join(lines)


def write(dic: Dictionary, cfg: Config, root: Path, out: Path | None = None) -> Path:
    out = out or (root / ".cangjie" / "report.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(dic, cfg, root), encoding="utf-8")
    return out


__all__ = ["build", "write"]
