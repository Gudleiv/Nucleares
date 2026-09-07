#!/usr/bin/env python3
"""Turn the per-page model into one continuous document flow.

Paragraphs that the original split across a page boundary are re-joined, and
places where the source forced a new page (a section start) are marked so the
Russian rendering keeps the same section-per-page structure.

Usage: python3 tools/flatten.py work/model.json work/flow.json
"""
import json
import sys

FULL_LINE = 504.7
BOTTOM = 761.0           # last usable text baseline area on a page

# indent -> list level
LEVELS = [(85.1, 0), (103.1, 1), (121.1, 1), (138.1, 2), (139.1, 2), (142.0, 2),
          (156.1, 2), (157.1, 2), (174.1, 3), (175.1, 3), (192.1, 4), (193.1, 4),
          (210.1, 5), (211.1, 5), (229.1, 5)]


def level_of(indent):
    best, lvl = 1e9, 0
    for x, l in LEVELS:
        if abs(indent - x) < best:
            best, lvl = abs(indent - x), l
    if best > 12:                       # unusual indent (centred text, tables)
        return None
    return lvl


def classify(item):
    runs = [r for r in item["runs"] if r["text"].strip()]
    if not runs:
        return "spacer"
    if item["bullet"] and not "".join(r["text"] for r in item["runs"]).strip():
        return "spacer"
    if item["bullet"]:
        return "bullet"
    sizes = {r["size"] for r in runs}
    if item["indent"] < 90 and all(r["b"] for r in runs) and item["lines"] <= 2 \
            and max(sizes) >= 12 and not item["justified"]:
        return "heading"
    return "body"


def main(src, dst):
    model = json.load(open(src, encoding="utf-8"))
    cover = model["cover_page"]
    index_pages = set(model["index_pages"])

    def section_of(pno):
        if pno == cover:
            return "cover"
        if pno in index_pages:
            return "index"
        return "body"

    flow = []
    prev_page_open = False       # previous page ended mid-paragraph
    prev_bottom = None

    for pg in model["pages"]:
        pno = pg["page"]
        items = pg["items"]
        body = [it for it in items if it["kind"] != "image" or True]
        # a forced page break: previous page stopped well before the bottom
        forced = prev_bottom is not None and prev_bottom < BOTTOM - 55
        first_real = True

        for it in items:
            if it["kind"] == "image":
                flow.append({
                    "type": "image", "bbox": it["bbox"],
                    "parts": it["parts"], "src_page": pno,
                    "float": it.get("float", False),
                    "wrap_x1": it.get("wrap_x1"),
                    "section": section_of(pno),
                    "y0": it["bbox"][1], "y1": it["bbox"][3],
                })
                first_real = False
                continue

            role = classify(it)
            if role == "spacer":
                size = it["runs"][0]["size"] if it["runs"] else 13.56
                flow.append({"type": "spacer", "size": size, "src_page": pno,
                             "section": section_of(pno),
                             "y0": it["y0"], "y1": it["y1"]})
                continue

            entry = {
                "type": role,
                "level": level_of(it["indent"]),
                "indent": it["indent"],
                "bullet": it["bullet"],
                "runs": it["runs"],
                "src_page": pno,
                "justified": it["justified"],
                "deco": it.get("deco") or {},
                "y0": it["y0"], "y1": it["y1"],
                "x1": it.get("max_x1", it["indent"]),
                "section": section_of(pno),
            }

            # continuation of a paragraph broken by the page boundary
            if first_real and prev_page_open and role in ("body", "bullet") \
                    and not it["bullet"]:
                for prev in reversed(flow):
                    if prev["type"] in ("body", "bullet"):
                        break
                else:
                    prev = None
                if prev is not None and abs(prev["indent"] - it["indent"]) < 3:
                    prev["runs"] = prev["runs"] + it["runs"]
                    prev["justified"] = True
                    first_real = False
                    continue

            if first_real and forced:
                entry["page_break"] = True
            flow.append(entry)
            first_real = False

        last = None
        for it in items:
            if it["kind"] == "para" and it["runs"] and \
                    "".join(r["text"] for r in it["runs"]).strip():
                last = it
        if last is not None:
            prev_bottom = last["y1"]
            prev_page_open = last.get("last_x1", 0) >= FULL_LINE
        else:
            prev_bottom, prev_page_open = None, False

    # merge adjacent runs with identical styling after the joins above
    for e in flow:
        if "runs" not in e:
            continue
        merged = []
        for r in e["runs"]:
            if merged and all(merged[-1].get(k) == r.get(k)
                              for k in ("size", "b", "i", "u", "color")):
                merged[-1]["text"] += r["text"]
            else:
                merged.append(dict(r))
        e["runs"] = merged
        e["text"] = "".join(r["text"] for r in merged)

    # vertical gap the original left before each item (same page only)
    prev = None
    for e in flow:
        if prev is not None and prev["src_page"] == e["src_page"]:
            e["gap"] = round(e["y0"] - prev["y1"], 1)
        prev = e

    for i, e in enumerate(flow):
        e["id"] = i

    json.dump({"geometry": model["geometry"], "flow": flow},
              open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    from collections import Counter
    c = Counter(e["type"] for e in flow)
    words = sum(len(e.get("text", "").split()) for e in flow)
    print(f"{len(flow)} items {dict(c)} words={words} "
          f"breaks={sum(1 for e in flow if e.get('page_break'))}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
