#!/usr/bin/env python3
"""Extract a structured document model from the Nucleares user manual PDF.

The manual is a Word document printed to PDF, so its layout is extremely
regular: one justified text column between x=85.1 and x=513.6, body text at
13.6pt, bullet lists at a handful of fixed indents, inline and right-floating
images, shaded headings and ruled "In summary" call-outs.

The model produced here is a flat flow of paragraphs and images.  Every
paragraph keeps the styling of its runs (bold / italic / underline / colour /
size) plus any decoration drawn around it, so the renderer can reproduce the
original appearance with a Cyrillic-capable font.

Usage:  python3 tools/extract.py "NUCLEARES - User Manual.pdf" work/model.json
"""
import json
import sys

import pymupdf

HEADER_Y = 66.0          # everything above this is the running header
FOOTER_Y = 786.0         # everything below this is the running footer
LEFT = 85.1              # text column left edge
RIGHT = 513.7            # text column right edge
PAGE_W = 595.32
PAGE_H = 841.92

COVER_PAGE = 0           # 0-based
INDEX_PAGES = (2, 3)     # 0-based: the alphabetical index

BULLET_CHARS = set("●○•▪■⮞⮚-–—◦")

# A line whose right edge reaches this far is a justified, non-final line.
FULL_LINE = RIGHT - 9.0


def span_style(s):
    return {
        "size": round(s["size"], 2),
        "b": bool(s["flags"] & 2 ** 4),
        "i": bool(s["flags"] & 2 ** 1),
        "color": s["color"],
    }


def same_style(a, b):
    return all(a.get(k) == b.get(k) for k in ("size", "b", "i", "u", "color"))


def is_bullet_span(s):
    t = s["text"].strip()
    return len(t) <= 2 and t != "" and all(c in BULLET_CHARS for c in t)


# --------------------------------------------------------------------------
# vector decorations


def collect_rects(page):
    """Filled rectangles and rules drawn in the body area."""
    out = []
    for dr in page.get_drawings():
        fill = dr.get("fill")
        color = dr.get("color")
        for item in dr["items"]:
            if item[0] == "re":
                r = item[1]
                paint = fill if fill is not None else color
            elif item[0] == "l":
                p1, p2 = item[1], item[2]
                r = pymupdf.Rect(min(p1.x, p2.x), min(p1.y, p2.y),
                                 max(p1.x, p2.x), max(p1.y, p2.y))
                paint = color if color is not None else fill
            else:
                continue
            if paint is None:
                continue
            if r.y1 <= HEADER_Y or r.y0 >= FOOTER_Y:
                continue
            if r.width < 3:
                continue
            out.append({"rect": [r.x0, r.y0, r.x1, r.y1],
                        "color": [round(c, 4) for c in paint],
                        "h": r.height, "w": r.width})
    return out


INDEX_COL_SPLIT = 305.0  # the index is the only two-column part of the manual


def collect_lines(page, two_column=False):
    """Body-area text lines, sorted by vertical position.

    MuPDF breaks a heavily justified line into several "lines" whenever the
    inter-word gaps get wide, which happens all through this manual next to
    floating figures.  Fragments sharing a baseline are stitched back together
    here so paragraph segmentation sees real lines.
    """
    frags = []
    for block in page.get_text("rawdict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            x0, y0, x1, y1 = line["bbox"]
            if y1 <= HEADER_Y or y0 >= FOOTER_Y:
                continue
            spans = []
            for sp in line["spans"]:
                sp = dict(sp)
                sp["text"] = "".join(c["c"] for c in sp["chars"])
                if sp["text"]:
                    spans.append(sp)
            if not spans:
                continue
            base = round(spans[0]["origin"][1], 1)
            frags.append({"base": base, "bbox": [x0, y0, x1, y1], "spans": spans})

    frags.sort(key=lambda f: (f["base"], f["bbox"][0]))
    lines = []
    for f in frags:
        prev = lines[-1] if lines else None
        crosses_columns = two_column and prev is not None \
            and prev["bbox"][2] < INDEX_COL_SPLIT <= f["bbox"][0]
        if prev is not None and abs(prev["base"] - f["base"]) < 1.2 \
                and not crosses_columns:
            if not prev["spans"][-1]["text"].endswith(" ") \
                    and not f["spans"][0]["text"].startswith(" "):
                pad = dict(prev["spans"][-1])
                pad["text"] = " "
                prev["spans"].append(pad)
            prev["spans"] += f["spans"]
            prev["bbox"] = [prev["bbox"][0], min(prev["bbox"][1], f["bbox"][1]),
                            f["bbox"][2], max(prev["bbox"][3], f["bbox"][3])]
            continue
        lines.append({"base": f["base"], "bbox": list(f["bbox"]),
                      "spans": list(f["spans"])})

    for l in lines:
        l["text"] = "".join(s["text"] for s in l["spans"])
        l["first_word_w"] = first_word_width(l)
    lines.sort(key=lambda l: (round(l["bbox"][1], 1), l["bbox"][0]))
    return lines


def first_word_width(line):
    """Width of the first word of a line, measured from its glyph boxes."""
    x0 = None
    x1 = None
    for span in line["spans"]:
        for ch in span.get("chars", []):
            if ch["c"].isspace():
                if x1 is not None:
                    return x1 - x0
                continue
            if x0 is None:
                x0 = ch["bbox"][0]
            x1 = ch["bbox"][2]
    return (x1 - x0) if x0 is not None else 0.0


def apply_underlines(lines, rects):
    """Mark spans that sit directly above a thin rule as underlined."""
    used = []
    for r in rects:
        if r["h"] > 2.0 or r["w"] > 340:
            continue
        rx0, ry0, rx1, ry1 = r["rect"]
        hit = False
        for line in lines:
            lx0, ly0, lx1, ly1 = line["bbox"]
            if not (ly1 - 5.0 <= ry0 <= ly1 + 3.5):
                continue
            for s in line["spans"]:
                sx0, sx1 = s["bbox"][0], s["bbox"][2]
                overlap = min(sx1, rx1) - max(sx0, rx0)
                if overlap > 0.5 * min(sx1 - sx0, rx1 - rx0):
                    s["_underline"] = True
                    hit = True
        if hit:
            used.append(id(r))
    return {u for u in used}


def collect_images(page):
    """Body-area images with the xref needed to re-embed them unchanged."""
    out = []
    seen = set()
    for info in page.get_image_info(xrefs=True):
        x0, y0, x1, y1 = info["bbox"]
        if y1 <= HEADER_Y or y0 >= FOOTER_Y:
            continue
        key = (round(x0, 1), round(y0, 1), info["xref"])
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "kind": "image",
            "bbox": [x0, y0, x1, y1],
            "xref": info["xref"],
            "px": [info["width"], info["height"]],
            "z": info["number"],
        })
    return out


def drop_backdrops(parts):
    """Word bakes a picture's drop shadow into raster tiles laid behind it.

    Those tiles are flattened snapshots of the page, so they carry English text
    of whatever sat behind the picture; harmless while the layout is unchanged,
    but visible once the text reflows.  A tile that the foreground picture
    covers by more than half is dropped, while genuinely stacked artwork stays.
    """
    if len(parts) < 2:
        return parts
    top = parts[-1]
    tx0, ty0, tx1, ty1 = top["bbox"]
    keep = []
    for part in parts[:-1]:
        x0, y0, x1, y1 = part["bbox"]
        area = max((x1 - x0) * (y1 - y0), 1e-6)
        ov = max(0.0, min(x1, tx1) - max(x0, tx0)) * \
            max(0.0, min(y1, ty1) - max(y0, ty0))
        if ov / area < 0.5:
            keep.append(part)
    return keep + [top]


def group_images(images):
    """Word draws drop shadows as separate bitmaps; keep overlapping
    bitmaps together as one figure so they stay registered after reflow."""
    groups = []
    for img in sorted(images, key=lambda i: (i["bbox"][1], i["bbox"][0])):
        x0, y0, x1, y1 = img["bbox"]
        for g in groups:
            gx0, gy0, gx1, gy1 = g["bbox"]
            if x0 < gx1 - 12 and gx0 < x1 - 12 and y0 < gy1 - 12 and gy0 < y1 - 12:
                g["bbox"] = [min(gx0, x0), min(gy0, y0), max(gx1, x1), max(gy1, y1)]
                g["parts"].append(img)
                break
        else:
            groups.append({"kind": "image", "bbox": list(img["bbox"]),
                           "parts": [img]})
    for g in groups:
        # the bbox keeps every bitmap, including the shadow tiles: Word wrapped
        # the text around that full rectangle and the reflow must match
        g["parts"].sort(key=lambda p: p["z"])     # content is drawn last
        g["parts"] = drop_backdrops(g["parts"])
        gx0, gy0 = g["bbox"][0], g["bbox"][1]
        g["parts"] = [{
            "xref": p["xref"],
            "px": p["px"],
            "rel": [round(p["bbox"][0] - gx0, 2), round(p["bbox"][1] - gy0, 2),
                    round(p["bbox"][2] - gx0, 2), round(p["bbox"][3] - gy0, 2)],
        } for p in g["parts"]]
    return groups


def line_indents(line):
    """(bullet, bullet_x, text_x) for a line."""
    spans = line["spans"]
    if is_bullet_span(spans[0]):
        bullet = spans[0]["text"].strip()
        bullet_x = line["bbox"][0]
        text_x = None
        for s in spans[1:]:
            if s["text"].strip():
                text_x = s["bbox"][0]
                break
        return bullet, bullet_x, text_x if text_x is not None else bullet_x + 18.0
    return None, None, line["bbox"][0]


def runs_of(line, skip_bullet):
    runs = []
    spans = line["spans"]
    start = 0
    if skip_bullet:
        start = 1
        while start < len(spans) and not spans[start]["text"].strip():
            start += 1
    for s in spans[start:]:
        st = span_style(s)
        st["u"] = bool(s.get("_underline"))
        if runs and same_style(runs[-1], st):
            runs[-1]["text"] += s["text"]
        else:
            r = dict(st)
            r["text"] = s["text"]
            runs.append(r)
    return runs


def merge_runs(runs):
    out = []
    for r in runs:
        if out and same_style(out[-1], r):
            out[-1]["text"] += r["text"]
        else:
            out.append(dict(r))
    return out


def mark_floats(images, lines):
    """An image is a float when text runs beside it, not just above/below."""
    for img in images:
        ix0, iy0, ix1, iy1 = img["bbox"]
        beside = 0
        col_x1 = 0.0
        for line in lines:
            lx0, ly0, lx1, ly1 = line["bbox"]
            if not line["text"].strip():
                continue
            if ly1 <= iy0 + 3 or ly0 >= iy1 - 3:
                continue
            if lx1 <= ix0 + 2:
                beside += 1
                col_x1 = max(col_x1, lx1)
            elif lx0 >= ix1 - 2:
                beside += 1
        img["float"] = beside >= 2
        if img["float"]:
            img["wrap_x1"] = round(col_x1 or ix0 - 8.0, 1)
    return images


def attach_decorations(paras, rects):
    """Heading shading, rules under headings and call-out borders."""
    for p in paras:
        p["deco"] = {}
    for r in rects:
        rx0, ry0, rx1, ry1 = r["rect"]
        if r["w"] < 340:
            continue
        if r["h"] >= 8:                          # shaded band behind a heading
            for p in paras:
                if p["y0"] - 2 <= ry0 + 3 and p["y1"] >= ry0 + 3:
                    p["deco"]["shade"] = r["color"]
                    break
        else:                                    # thin rule above or below text
            below = None
            above = None
            for p in paras:
                if 0 <= ry0 - p["y1"] <= 12:
                    below = p
                if 0 <= p["y0"] - ry1 <= 14:
                    above = above or p
            if below is not None:
                below["deco"].setdefault("rule_below", r["color"])
            elif above is not None:
                above["deco"].setdefault("rule_above", r["color"])
    return paras


def right_limit(line, images):
    """Where a line must reach to count as a justified, continuing line.

    Next to a floating figure the text column is narrower than the page, so the
    plain right margin cannot be used to detect the end of a paragraph.
    """
    limit = RIGHT
    _, ly0, _, ly1 = line["bbox"]
    for img in images:
        ix0, iy0, ix1, iy1 = img["bbox"]
        if ly1 > iy0 + 2 and ly0 < iy1 - 2 and ix0 > LEFT + 40:
            limit = min(limit, ix0)
    return limit


def paragraphs_of_page(page, two_column=False):
    """Segment one page into paragraphs and images, in reading order."""
    lines = collect_lines(page, two_column)
    rects = collect_rects(page)
    apply_underlines(lines, rects)
    images = mark_floats(group_images(collect_images(page)), lines)
    floats = [i for i in images if i.get("float")]

    items = []
    prev = None
    para = None

    def flush():
        nonlocal para
        if para is not None:
            para["runs"] = merge_runs(para["runs"])
            text = "".join(r["text"] for r in para["runs"])
            para["text"] = text
            para["empty"] = not text.strip() and para["bullet"] is None
            items.append(para)
            para = None

    for line in lines:
        bullet, bullet_x, text_x = line_indents(line)
        x0, y0, x1, y1 = line["bbox"]
        new_para = False
        if para is None:
            new_para = True
        elif bullet is not None:
            new_para = True
        elif prev["bbox"][2] + 4.0 + line["first_word_w"] < right_limit(prev, floats):
            new_para = True
        elif y0 - prev["bbox"][1] > 21.0:
            new_para = True
        elif abs(text_x - para["indent"]) > 2.0:
            new_para = True

        if new_para:
            flush()
            para = {
                "kind": "para",
                "bullet": bullet,
                "bullet_x": round(bullet_x, 1) if bullet_x is not None else None,
                "indent": round(text_x, 1),
                "first_x": round(x0, 1),
                "y0": round(y0, 1),
                "runs": [],
                "lines": 0,
                "justified": False,
            }
        para["runs"] += runs_of(line, skip_bullet=bullet is not None)
        para["lines"] += 1
        para["y1"] = round(y1, 1)
        if x1 >= right_limit(line, floats) - 9.0:
            para["justified"] = True
        para["last_x1"] = round(x1, 1)
        para["max_x1"] = max(para.get("max_x1", 0), round(x1, 1))
        prev = line

    flush()
    attach_decorations([p for p in items if p["kind"] == "para"], rects)

    merged = sorted(items + images,
                    key=lambda it: (it.get("y0", it.get("bbox", [0, 0])[1]),
                                    it.get("first_x", it.get("bbox", [0])[0])))
    return merged


def main(src, dst):
    doc = pymupdf.open(src)
    pages = []
    for pno, page in enumerate(doc):
        pages.append({
            "page": pno + 1,
            "items": paragraphs_of_page(page, pno in INDEX_PAGES),
        })
    model = {
        "source": src,
        "page_count": doc.page_count,
        "geometry": {
            "page_w": PAGE_W, "page_h": PAGE_H,
            "left": LEFT, "right": RIGHT,
            "top": 70.8, "bottom": FOOTER_Y - 25.0,
        },
        "cover_page": COVER_PAGE + 1,
        "index_pages": [p + 1 for p in INDEX_PAGES],
        "pages": pages,
    }
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(model, fh, ensure_ascii=False, indent=1)

    floats = sum(1 for p in pages for i in p["items"]
                 if i["kind"] == "image" and i.get("float"))
    shades = sum(1 for p in pages for i in p["items"]
                 if i["kind"] == "para" and i.get("deco", {}).get("shade"))
    unders = sum(1 for p in pages for i in p["items"] if i["kind"] == "para"
                 for r in i["runs"] if r.get("u"))
    print(f"pages={doc.page_count} floats={floats} shaded_headings={shades} "
          f"underlined_runs={unders} -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
