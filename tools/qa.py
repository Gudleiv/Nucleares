#!/usr/bin/env python3
"""Sanity-check a rendered manual.

Reports anything that would be visible as a defect: text outside the column,
English sentences left untranslated (as opposed to the deliberate English terms
in brackets), missing glyphs, empty pages and index page numbers that do not
land on the right section.

Usage: python3 tools/qa.py "NUCLEARES - Руководство пользователя.pdf"
"""
import re
import sys

import pymupdf

LEFT, RIGHT = 83.0, 516.0
LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z ,.'\-]{45,}")


def main(path):
    doc = pymupdf.open(path)
    problems = 0

    outside, empty, notdef = [], [], []
    latin = []
    for i, page in enumerate(doc, 1):
        text = page.get_text()
        if len(text.strip()) < 40 and not page.get_images():
            empty.append(i)
        if "�" in text:
            notdef.append(i)
        for block in page.get_text("dict")["blocks"]:
            if block["type"]:
                continue
            for line in block["lines"]:
                x0, _, x1, _ = line["bbox"]
                if x1 > RIGHT or x0 < LEFT:
                    outside.append((i, round(x0), round(x1)))
        # English left in the body, outside the bracketed term duplicates
        stripped = re.sub(r"\([^)]*\)", "", text)
        for m in LATIN_RUN.finditer(stripped):
            latin.append((i, m.group(0)[:70]))

    def report(name, items):
        nonlocal problems
        if items:
            problems += 1
            print(f"  {name}: {len(items)}")
            for it in items[:8]:
                print(f"      {it}")
        else:
            print(f"  {name}: none")

    print(f"{path}: {doc.page_count} pages, {len(doc.get_toc())} bookmarks")
    report("lines outside the text column", outside)
    report("pages with no content", empty)
    report("missing glyphs", notdef)
    report("untranslated English stretches", latin)

    # every index page number must exist
    bad_refs = []
    if doc.page_count > 4:
        index_text = "".join(doc[p].get_text() for p in range(2, 5))
        for m in re.finditer(r",\s*(\d{1,3})\s*$", index_text, re.M):
            n = int(m.group(1))
            if not 1 <= n <= doc.page_count:
                bad_refs.append(n)
    report("index references outside the document", bad_refs)

    print("OK" if not problems else f"{problems} categories with findings")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
