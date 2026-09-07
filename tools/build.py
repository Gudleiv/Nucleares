#!/usr/bin/env python3
"""Assemble the finished manual: cover, disclaimer, index, body.

The index has to quote page numbers of the reflowed body, and its own length
shifts those numbers, so the build iterates until the pagination is stable.

Usage:
  python3 tools/build.py work/flow.ru.json "NUCLEARES - Руководство пользователя.pdf" --field ru
"""
import argparse
import json
import os
import re
import sys

import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render as R                                     # noqa: E402

COVER_TEXT_RU = {
    "Simulator of": "Симулятор",
    "Nuclear Power Plant Operation": "эксплуатации атомной электростанции",
    "Game Version: V 2.2.24.191": "Версия игры: V 2.2.24.191",
    "Manual Version: 1.8": "Версия руководства: 1.8",
    "Last Manual Update: 11/06/2025": "Последнее обновление: 11.06.2025",
}
INDEX_TITLE = {"text": "Index", "ru": "Алфавитный указатель"}
HEADER_RIGHT = {"text": "USER MANUAL", "ru": "РУКОВОДСТВО ПОЛЬЗОВАТЕЛЯ"}

SORT_MAP = str.maketrans({"ё": "е", "Ё": "Е", "«": "", "»": "", '"': ""})


def sort_key(term):
    t = term.translate(SORT_MAP).lower().lstrip(" ([")
    cyrillic = bool(t) and "а" <= t[0] <= "я"
    return (0 if cyrillic else 1, t)


def normalise(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def build_anchors(flow):
    """Map an index term to the flow item that introduces it."""
    anchors = {}
    for e in flow:
        if e.get("section") != "body" or e["type"] not in ("heading", "body", "bullet"):
            continue
        if e["type"] == "heading":
            anchors.setdefault(normalise(e["text"]), e["id"])
        run0 = e["runs"][0]
        if run0.get("b"):
            lead = run0["text"].strip().rstrip(":").strip()
            if 3 < len(lead) < 80:
                anchors.setdefault(normalise(lead), e["id"])
    return anchors


def make_renderer(flow, args, field):
    r = R.Renderer(flow, args.src, field=field, family=args.family)
    r.header_right = HEADER_RIGHT[field]
    return r


def split_flow(flow):
    cover = [e for e in flow if e.get("section") == "cover"]
    front = [e for e in flow if e.get("section") == "body" and e["src_page"] <= 2]
    body = [e for e in flow if e.get("section") == "body" and e["src_page"] > 2]
    return cover, front, body


def index_entries(args, field, flow, placements, offset):
    entries = json.load(open(args.index, encoding="utf-8"))
    ru = {}
    if field == "ru" and os.path.exists(args.index_ru):
        ru = {e["term"]: e["ru"] for e in json.load(open(args.index_ru, encoding="utf-8"))}
    anchors = build_anchors(flow)
    first_of_page = {}
    for e in flow:
        if e.get("section") == "body" and e["id"] in placements:
            first_of_page.setdefault(e["src_page"], e["id"])

    out = []
    for entry in entries:
        term = ru.get(entry["term"], entry["term"]) if field == "ru" else entry["term"]
        item_id = anchors.get(normalise(entry["term"]))
        if item_id is None and entry["page"]:
            item_id = first_of_page.get(entry["page"])
        page = placements.get(item_id)
        if page is None:
            continue
        out.append((term, page + offset))
    out.sort(key=lambda t: sort_key(t[0]))
    return out


def build(args, field):
    data = json.load(open(args.flow, encoding="utf-8"))
    flow = data["flow"]
    cover, front, body = split_flow(flow)

    cover_doc = make_renderer(cover, args, field).render_cover(cover, COVER_TEXT_RU
                                                               if field == "ru" else {})
    front_r = make_renderer(front, args, field)
    front_doc = front_r.render()
    body_r = make_renderer(body, args, field)
    body_doc = body_r.render()

    n_index = 2
    for _ in range(6):
        offset = cover_doc.page_count + front_doc.page_count + n_index
        entries = index_entries(args, field, flow, body_r.placements, offset)
        index_r = make_renderer([], args, field)
        index_doc = index_r.render_index(INDEX_TITLE[field], entries)
        if index_doc.page_count == n_index:
            break
        n_index = index_doc.page_count

    out = pymupdf.open()
    for part in (cover_doc, front_doc, index_doc, body_doc):
        out.insert_pdf(part)
    out.set_metadata({
        "title": "NUCLEARES — Руководство пользователя" if field == "ru"
                 else "NUCLEARES — User Manual",
        "author": "Aerilian Games",
        "subject": "Симулятор эксплуатации атомной электростанции" if field == "ru"
                   else "Simulator of Nuclear Power Plant Operation",
        "keywords": "Nucleares, ядерный реактор, PWR, руководство",
    })
    toc = []
    for e in flow:
        if e["type"] == "heading" and e.get("section") == "body" \
                and e["id"] in body_r.placements:
            level = 1 if (e.get("deco") or {}).get("shade") else 2
            text = "".join(r.get(field, r["text"]) for r in
                           (e.get("ru_runs") if field == "ru" and e.get("ru_runs")
                            else e["runs"])).strip()
            page = body_r.placements[e["id"]] + cover_doc.page_count \
                + front_doc.page_count + index_doc.page_count
            toc.append([level, text, page])
    if toc:
        out.set_toc(toc)
    try:
        out.subset_fonts()
    except AttributeError:
        pass
    out.save(args.out, deflate=True, garbage=4)
    print(f"{args.out}: {out.page_count} pages "
          f"(cover {cover_doc.page_count} + front {front_doc.page_count} + "
          f"index {index_doc.page_count} + body {body_doc.page_count}), "
          f"{len(entries)} index entries, {len(toc)} bookmarks")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("flow")
    ap.add_argument("out")
    ap.add_argument("--field", default="ru")
    ap.add_argument("--family", default=R.FAMILY)
    ap.add_argument("--src", default="NUCLEARES - User Manual.pdf")
    ap.add_argument("--index", default="work/index.json")
    ap.add_argument("--index-ru", default="work/index.ru.json")
    args = ap.parse_args()
    build(args, args.field)


if __name__ == "__main__":
    main()
