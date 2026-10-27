"""造字炉：把"义符 + 声符"真的烧成一个可输入的字（PUA + TTF）。

只有在需要"字是自己造的、别人字典里没有"时才用本模块；
日常省 token 用扩展 A 区真汉字就够（无需装字体，任何设备都能显示）。

用法：
    cangjie forge            # 为当前字典生成 assets/Cangjie.ttf + 预览页
    cangjie forge --pool pua # 把字典迁到私有区（会重写 dict.json）

依赖：fontTools（`pip install fonttools`）+ 系统 CJK 字体（Windows 自带）。
"""
from __future__ import annotations

from pathlib import Path

from .glyphs import spec_for

FONT_CANDIDATES = [
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/simsun.ttc", 0),
    ("C:/Windows/Fonts/simhei.ttf", 0),
    ("C:/Windows/Fonts/Deng.ttf", 0),
    ("/System/Library/Fonts/PingFang.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
]

PUA_BASE = 0xE000


class ForgeError(RuntimeError):
    pass


def _require_fonttools():
    try:
        import fontTools  # noqa: F401
        return True
    except ImportError as exc:  # pragma: no cover
        raise ForgeError(
            "缺少 fontTools：请运行 `pip install fonttools` 后重试。"
            "（不影响日常压缩，仅影响 TTF 造字）"
        ) from exc


def _find_source_font():
    for path, idx in FONT_CANDIDATES:
        if Path(path).exists():
            return path, idx
    raise ForgeError("未找到可用的 CJK 源字体，请用 --font 指定一个 .ttf/.ttc 路径。")


def _bounds(glyphset, name):
    from fontTools.pens.boundsPen import BoundsPen

    bp = BoundsPen(glyphset)
    try:
        glyphset[name].draw(bp)
    except Exception:
        return None
    return bp.bounds


def _draw_fitted(glyphset, name, box, pen):
    """把组件缩放平移到指定盒子内。"""
    from fontTools.misc.transform import Transform
    from fontTools.pens.transformPen import TransformPen

    b = _bounds(glyphset, name)
    if not b:
        return False
    xMin, yMin, xMax, yMax = b
    w = max(1.0, xMax - xMin)
    h = max(1.0, yMax - yMin)
    bx, by, bw, bh = box
    s = min(bw / w, bh / h)
    dx = bx + (bw - w * s) / 2 - xMin * s
    dy = by + (bh - h * s) / 2 - yMin * s
    glyphset[name].draw(TransformPen(pen, Transform(s, 0, 0, s, dx, dy)))
    return True


def forge(
    entries: list[dict],
    out_dir: Path,
    font_path: str | None = None,
    family: str = "CangjieCompress",
) -> dict:
    """为 entries（[{g,p,cls}]）生成 PUA 字形与 TTF。

    返回 {"ttf": path, "mapping": {glyph: phrase}, "missed": [...], "pua": {glyph: cp}}
    """
    _require_fonttools()
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import TTFont

    src_path, idx = (font_path, 0) if font_path else _find_source_font()
    src = TTFont(src_path, fontNumber=idx, lazy=True)
    gs = src.getGlyphSet()
    cmap = src.getBestCmap()
    upem = src["head"].unitsPerEm or 1000
    ascent = getattr(src["hhea"], "ascent", int(upem * 0.88))
    descent = getattr(src["hhea"], "descent", -int(upem * 0.12))

    glyph_order = [".notdef"]
    glyphs = {".notdef": TTGlyphPen(None).glyph()}
    metrics = {".notdef": (int(upem * 0.5), 0)}
    charmap: dict[int, str] = {}
    pua: dict[str, int] = {}
    missed: list[str] = []
    pad = upem * 0.06
    inner = upem - 2 * pad
    half = inner / 2

    for i, e in enumerate(entries):
        sp = spec_for(e["g"], e["p"], e.get("cls", ""))
        lg = cmap.get(ord(sp.cls))
        rg = cmap.get(ord(sp.phon))
        if not lg or not rg:
            missed.append(e["p"])
            continue
        pen = TTGlyphPen(None)
        if sp.layout == "tb":
            ok1 = _draw_fitted(gs, lg, (pad, pad + half, inner, half), pen)
            ok2 = _draw_fitted(gs, rg, (pad, pad, inner, half), pen)
        else:
            ok1 = _draw_fitted(gs, lg, (pad, pad, half, inner), pen)
            ok2 = _draw_fitted(gs, rg, (pad + half, pad, half, inner), pen)
        if not (ok1 and ok2):
            missed.append(e["p"])
            continue
        name = f"cj{i:04d}"
        glyphs[name] = pen.glyph()
        metrics[name] = (upem, int(pad))
        cp = PUA_BASE + len(pua)
        charmap[cp] = name
        pua[e["g"]] = cp
        glyph_order.append(name)

    if not glyph_order[1:]:
        raise ForgeError("没有任何条目能造字（源字体缺字？）")

    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(charmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
    fb.setupNameTable({
        "familyName": family,
        "styleName": "Regular",
        "uniqueFontIdentifier": f"{family};1.0",
        "fullName": f"{family} Regular",
        "psName": family.replace(" ", "") + "-Regular",
        "version": "Version 1.0",
    })
    fb.setupOS2(sTypoAscender=ascent, sTypoDescender=descent,
                usWinAscent=ascent, usWinDescent=abs(descent))
    fb.setupPost()
    fb.setupMaxp()

    out_dir.mkdir(parents=True, exist_ok=True)
    ttf = out_dir / f"{family}.ttf"
    fb.save(str(ttf))
    return {"ttf": str(ttf), "pua": pua, "missed": missed, "family": family}


def font_preview_html(entries: list[dict], family: str, ttf_name: str) -> str:
    """用 @font-face 直接加载生成的字体，看真实字形。"""
    from .glyphs import preview_html

    body = preview_html(entries, title="仓颉自造字预览（真实字体渲染）")
    style = f"""<style>
@font-face {{ font-family: '{family}'; src: url('{ttf_name}') format('truetype'); }}
.ph, .fig, .cp {{ font-family: '{family}', 'KaiTi', serif; }}
</style>"""
    return body.replace("</head>", style + "</head>")


__all__ = ["forge", "ForgeError", "font_preview_html", "FONT_CANDIDATES"]
