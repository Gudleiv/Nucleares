#!/usr/bin/env python3
"""Merge the translated chunks back into the document flow.

Each translated paragraph arrives as a marked string ``{0|...}{1|...}``.  The
marker numbers map back onto the original runs, so bold lead-ins, coloured
notes and underlines keep their styling wherever the Russian word order put
them.  Approved heading translations override whatever a chunk contains.

Usage: python3 tools/merge.py work/flow.json work/chunks work/flow.ru.json
"""
import glob
import json
import os
import re
import sys

MARKER = re.compile(r"\{(\d+)\|(.*?)\}", re.S)


def marked_of(item):
    return "".join("{%d|%s}" % (i, r["text"].strip(" "))
                   for i, r in enumerate(item["runs"]) if r["text"].strip())


def parse_marked(s):
    return [(int(m.group(1)), m.group(2)) for m in MARKER.finditer(s)]


def key_of(marked):
    """Match translations by their English source, not by position.

    Re-extracting the source PDF can renumber the flow, so the English text of
    a paragraph is the stable key.
    """
    text = " ".join(t for _, t in parse_marked(marked))
    return re.sub(r"\s+", " ", text).strip().lower()


def load_translations(chunk_dir):
    """-> {english key: russian marked string}, plus the id map as a fallback."""
    by_text, by_id = {}, {}
    for path in sorted(glob.glob(os.path.join(chunk_dir, "chunk_*.ru.json"))):
        src = path.replace(".ru.json", ".json")
        try:
            data = json.load(open(path, encoding="utf-8"))
            source = json.load(open(src, encoding="utf-8"))
        except Exception as exc:                       # noqa: BLE001
            print(f"  !! {os.path.basename(path)}: {exc}")
            continue
        source_by_id = {int(i["id"]): i["s"] for i in source}
        for item in data:
            iid = int(item["id"])
            by_id[iid] = item["s"]
            if iid in source_by_id:
                by_text[key_of(source_by_id[iid])] = item["s"]
    return by_text, by_id


def apply_to_item(item, marked, report):
    """Fill run["ru"] from a marked translation string."""
    pieces = parse_marked(marked)
    runs = item["runs"]
    keep = [i for i, r in enumerate(runs) if r["text"].strip()]
    if not pieces:
        report["empty"] += 1
        return False

    seen = {}
    for idx, text in pieces:
        if idx >= len(runs):
            report["bad_index"] += 1
            continue
        seen.setdefault(idx, []).append(text.strip())

    missing = [i for i in keep if i not in seen]
    if missing:
        report["missing_runs"] += 1

    order = [idx for idx, _ in pieces if idx < len(runs)]
    # runs are emitted in the order the translator used, so the paragraph reads
    # correctly; styling follows the marker number
    item["ru_runs"] = []
    for idx in dict.fromkeys(order):
        run = dict(runs[idx])
        run["ru"] = " ".join(seen[idx]).strip()
        if run["ru"]:
            item["ru_runs"].append(run)
    for i in missing:                                  # untranslated leftovers
        run = dict(runs[i])
        run["ru"] = run["text"].strip()
        item["ru_runs"].append(run)
    return True


def main(flow_path, chunk_dir, dst):
    data = json.load(open(flow_path, encoding="utf-8"))
    flow = data["flow"]
    by_text, by_id = load_translations(chunk_dir)
    headings = {}
    hpath = os.path.join(os.path.dirname(chunk_dir), "headings.ru.json")
    if os.path.exists(hpath):
        raw = {int(k): v for k, v in json.load(open(hpath, encoding="utf-8")).items()}
        by_flow_id = {e["id"]: e for e in flow}
        for iid, ru in raw.items():
            item = by_flow_id.get(iid)
            if item is not None and item["type"] == "heading":
                headings[re.sub(r"\s+", " ", item["text"]).strip().lower()] = ru

    report = {"empty": 0, "bad_index": 0, "missing_runs": 0}
    translated = 0
    untranslated = []
    for item in flow:
        if item["type"] not in ("heading", "body", "bullet"):
            continue
        if item["section"] != "body":
            continue
        head_key = re.sub(r"\s+", " ", item["text"]).strip().lower()
        if item["type"] == "heading" and head_key in headings:
            run = dict(item["runs"][0])
            run["ru"] = headings[head_key]
            item["ru_runs"] = [run]
            translated += 1
            continue
        s = by_text.get(key_of(marked_of(item))) or by_id.get(item["id"])
        if s and apply_to_item(item, s, report):
            translated += 1
        else:
            untranslated.append(item["id"])
            item["ru_runs"] = [dict(r, ru=r["text"].strip()) for r in item["runs"]
                               if r["text"].strip()]

    json.dump(data, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = translated + len(untranslated)
    print(f"translated {translated}/{total} paragraphs -> {dst}")
    if report["missing_runs"]:
        print(f"  paragraphs with a dropped run: {report['missing_runs']}")
    if report["bad_index"]:
        print(f"  markers pointing at a missing run: {report['bad_index']}")
    if untranslated:
        print(f"  still English: {len(untranslated)} -> ids {untranslated[:15]}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
