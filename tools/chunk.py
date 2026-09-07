#!/usr/bin/env python3
"""Split the document flow into translation chunks.

Each translatable paragraph is serialised as a marked string in which every
styled run is wrapped in ``{n|...}``.  Translators keep the wrappers (moving
them as Russian word order requires) so the styling survives the round trip.

Chunks break on section boundaries wherever possible, so a translator always
sees a whole topic.

Usage: python3 tools/chunk.py work/flow.json work/chunks [words_per_chunk]
"""
import json
import os
import re
import sys

TRANSLATABLE = ("heading", "body", "bullet")


def marked(runs):
    return "".join("{%d|%s}" % (i, r["text"].strip(" ")) for i, r in enumerate(runs)
                   if r["text"].strip())


def parse_marked(s):
    """Inverse of marked(): -> [(run_index, text), ...] in output order."""
    out = []
    for m in re.finditer(r"\{(\d+)\|(.*?)\}", s, re.S):
        out.append((int(m.group(1)), m.group(2)))
    return out


def main(flow_path, out_dir, target_words=2200):
    flow = json.load(open(flow_path, encoding="utf-8"))["flow"]
    os.makedirs(out_dir, exist_ok=True)

    chunks, cur, words, section = [], [], 0, ""
    for e in flow:
        if e["type"] not in TRANSLATABLE or e["section"] != "body":
            continue
        if e["type"] == "heading":
            starts_section = bool(e.get("page_break") or (e.get("deco") or {}).get("shade"))
            if starts_section and words >= target_words:
                chunks.append(cur)
                cur, words = [], 0
            if starts_section:
                section = e["text"].strip()
        cur.append({
            "id": e["id"],
            "type": e["type"],
            "section": section,
            "s": marked(e["runs"]),
        })
        words += len(e.get("text", "").split())
    if cur:
        chunks.append(cur)

    for i, ch in enumerate(chunks, 1):
        path = os.path.join(out_dir, f"chunk_{i:02d}.json")
        json.dump(ch, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    total = sum(len(c) for c in chunks)
    print(f"{len(chunks)} chunks, {total} paragraphs -> {out_dir}")
    for i, ch in enumerate(chunks, 1):
        w = sum(len(re.sub(r"\{\d+\|", "", it["s"]).split()) for it in ch)
        print(f"  chunk_{i:02d}: {len(ch):4} paragraphs, ~{w:5} words"
              f"  [{ch[0]['section'][:48]}]")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2],
         int(sys.argv[3]) if len(sys.argv) > 3 else 2200)
