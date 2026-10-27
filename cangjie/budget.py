"""预算控制：保证"任务越复杂，token 越不失控"。

三条规则：
1. **封顶**：字典条目数永不超过 ``max_entries``，字典注入成本因此有上界。
2. **淘汰**：超限时按 ``density``（净收益 / 字典成本）淘汰，先砍赔钱的条目。
3. **止损**：整体 ROI < 1 时自动缩表，避免"为了省 token 而花更多 token"。
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .state import Dictionary, Entry


@dataclass
class Verdict:
    keep: list[str]
    drop: list[str]
    reasons: dict[str, str]


def enforce(dic: Dictionary, cfg: Config) -> Verdict:
    """按预算规则裁剪字典（返回裁决，不改动入参）。"""
    reasons: dict[str, str] = {}
    alive: list[Entry] = []
    for e in dic.entries:
        if e.pinned:
            alive.append(e)          # 锁定项无条件保留
            continue
        if e.net <= 0 and e.hits > 0:
            reasons[e.g] = f"净收益为负（{e.net:.1f} tok）"
            continue
        if e.cost < cfg.min_phrase_cost:
            reasons[e.g] = "原短语太短，压缩无意义"
            continue
        alive.append(e)

    # 锁定项不吃名额（但若锁定项本身超过上限，只警告不驱逐——用户意图优先）
    pinned = [e for e in alive if e.pinned]
    rest = [e for e in alive if not e.pinned]
    slots = max(0, cfg.max_entries - len(pinned))
    if len(rest) > slots:
        rest.sort(key=lambda e: -e.density)
        for e in rest[slots:]:
            reasons[e.g] = f"超出字典上限，按收益密度淘汰（density={e.density:.2f}）"
        rest = rest[:slots]
    if len(pinned) > cfg.max_entries:
        reasons["__warn__"] = (f"锁定项 {len(pinned)} 条已超过上限 {cfg.max_entries}，"
                               f"建议减少 seed 或调高 max_entries")
    alive = pinned + rest

    keep = [e.g for e in alive]
    drop = [e.g for e in dic.entries if e.g not in set(keep)]
    return Verdict(keep=keep, drop=drop, reasons=reasons)


def apply(dic: Dictionary, verdict: Verdict) -> Dictionary:
    keep = set(verdict.keep)
    dic.entries = [e for e in dic.entries if e.g in keep]
    return dic


def audit(dic: Dictionary, cfg: Config) -> list[str]:
    """健康检查，给用户/模型的可执行建议。"""
    notes: list[str] = []
    s = dic.summary()
    if not dic.entries:
        return ["字典为空：先运行 `cangjie mine --apply` 挖一轮。"]
    if len(dic.entries) >= cfg.max_entries:
        notes.append(f"字典已满（{len(dic.entries)}/{cfg.max_entries}），新条目将触发淘汰。")
    dead = [e for e in dic.entries if e.hits == 0]
    if dead:
        notes.append(
            f"{len(dead)} 条从未命中（首次使用前正常；若跨多次会话仍为 0 建议 prune）："
            + "、".join(e.p for e in dead[:5])
        )
    if s["roi"] < 1.0 and s["hits"] > 0 and len(dic.entries) > 8:
        notes.append(
            f"ROI={s['roi']} < 1，字典成本高于收益，建议缩表：`cangjie prune --to "
            f"{max(8, len(dic.entries) // 2)}`"
        )
    if s["entry_cost"] > 600:
        notes.append(f"字典注入成本 {s['entry_cost']} tok，注意每轮都要付费，可考虑精简到 64 条。")
    over = [e for e in dic.entries if e.cost <= 2.5]
    if over:
        notes.append(f"{len(over)} 条原短语只有 {over[0].cost:.1f} tok，压缩收益很薄。")
    return notes


# ---------------------------------------------------------------- 增长模型

def simulate(
    turns: int = 30,
    per_turn_tokens: float = 1200,
    dict_cost: float = 300.0,
    ratio: float = 0.45,
) -> dict:
    """粗算"不压缩 vs 仓颉压缩"的上下文累计成本。

    未压缩时每轮都要把历史重新读一遍，累计成本 ∝ turns²；
    压缩后历史以 ratio 增长，且字典成本每轮固定 → 累计成本斜率显著变缓。
    """
    naive = sum(per_turn_tokens * (i + 1) for i in range(turns))
    cangjie = sum(dict_cost + per_turn_tokens * ratio * (i + 1) for i in range(turns))
    return {
        "turns": turns,
        "naive": round(naive),
        "cangjie": round(cangjie),
        "saved": round(naive - cangjie),
        "ratio": round(cangjie / naive, 3) if naive else 0.0,
        "turn_costs": [
            (round(per_turn_tokens * (i + 1)), round(dict_cost + per_turn_tokens * ratio * (i + 1)))
            for i in range(turns)
        ],
    }


__all__ = ["Verdict", "enforce", "apply", "audit", "simulate"]
