#!/usr/bin/env python3
"""Parse the manual's alphabetical index into (term, page) entries.

The printed index is a two-column list of "Term, page" lines at 9pt, with the
term in bold.  Entries wrap onto a second line with a hanging indent.

Usage: python3 tools/index_data.py work/model.json work/index.json
"""
import json
import re
import sys

COL_SPLIT = 305.0
ENTRY_RE = re.compile(r"^(?P<term>.+?),\s*(?P<page>\d+)\s*$")


def parse(model):
    entries = []
    for pg in model["pages"]:
        if pg["page"] not in model["index_pages"]:
            continue
        cols = {0: [], 1: []}
        for it in pg["items"]:
            if it["kind"] != "para" or not it.get("text", "").strip():
                continue
            if it["text"].strip() == "Index":
                continue
            cols[0 if it["indent"] < COL_SPLIT else 1].append(it)
        for col in (0, 1):
            buf = ""
            for it in sorted(cols[col], key=lambda i: i["y0"]):
                part = it["text"].strip()
                buf = (part if not buf else
                       buf + part if buf.endswith("-") else buf + " " + part)
                m = ENTRY_RE.match(buf)
                if m:
                    term = re.sub(r"\s+", " ", m.group("term")).strip(" ,")
                    entries.append({"term": term, "page": int(m.group("page"))})
                    buf = ""
            if buf.strip():
                entries.append({"term": re.sub(r"\s+", " ", buf).strip(), "page": None})
    return entries


def main(src, dst):
    model = json.load(open(src, encoding="utf-8"))
    entries = parse(model)
    json.dump(entries, open(dst, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    missing = [e for e in entries if e["page"] is None]
    print(f"{len(entries)} index entries -> {dst}"
          + (f" ({len(missing)} without a page)" if missing else ""))
    for e in missing:
        print("   unparsed:", e["term"][:70])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
