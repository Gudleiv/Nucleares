#!/usr/bin/env python3
"""Render the document flow back into a PDF, reflowing the text.

The renderer reproduces the layout rules of the original manual: one justified
text column with the same margins and line pitch, the same bullet indents,
shaded section headings with their hairline rule, ruled "In summary" call-outs,
right-floating images with text wrapped beside them, and inline figures.

Text is drawn with a Cyrillic-capable font family, so the same code renders the
English original (for fidelity checking) and the Russian translation.

Usage:
  python3 tools/render.py work/flow.json out.pdf [--field text|ru]
"""
import argparse
import json
import os

import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(ROOT, "fonts")
FAMILY = "PTSans"

PAGE_W, PAGE_H = 595.32, 841.92
LEFT, RIGHT = 85.1, 513.7
TOP = 70.8                 # top of the first text line box on a page
BOTTOM = 771.0             # text must not cross this
LINE_RATIO = 1.19          # line pitch / font size, as in the original
LEADING = 1.1              # slack already contained in LINE_RATIO
ASCENT = 0.80              # baseline offset inside the line box

HEADER_LEFT = "AERILIAN GAMES | NUCLEARES"
HEADER_RIGHT = "USER MANUAL"
HEADER_COLOR = 0xBFBFBF
HEADER_SIZE = 11.0
HEADER_BASE = 45.9

SHADE_X0, SHADE_X1 = 83.6, 511.7


class Fonts:
    """The four faces used by the manual, plus width measurement."""

    def __init__(self, family=FAMILY):
        self.files = {
            (False, False): f"{FONT_DIR}/{family}-Regular.ttf",
            (True, False): f"{FONT_DIR}/{family}-Bold.ttf",
            (False, True): f"{FONT_DIR}/{family}-RegularItalic.ttf",
            (True, True): f"{FONT_DIR}/{family}-BoldItalic.ttf",
        }
        self.names = {
            (False, False): "f-r", (True, False): "f-b",
            (False, True): "f-i", (True, True): "f-bi",
        }
        self.fonts = {k: pymupdf.Font(fontfile=v) for k, v in self.files.items()}

    def key(self, run):
        return (bool(run.get("b")), bool(run.get("i")))

    def width(self, text, run):
        return self.fonts[self.key(run)].text_length(text, run["size"])

    def install(self, page):
        for k, name in self.names.items():
            page.insert_font(fontname=name, fontfile=self.files[k])


def rgb(color):
    return ((color >> 16 & 255) / 255, (color >> 8 & 255) / 255, (color & 255) / 255)


NO_SPACE_BEFORE = ",.;:!?»)]}%"


def tokenize(runs, field):
    """Split styled runs into (word, run, space_before) tokens.

    Runs holding nothing but punctuation are common in the source (a stray
    full stop left in its own run), so a token that starts with closing
    punctuation is glued to the previous word.
    """
    out = []
    for run in runs:
        text = run["text"] if field == "text" else run.get(field, run["text"])
        for word in text.split():
            out.append((word, run, word[0] not in NO_SPACE_BEFORE))
    if out:
        out[0] = (out[0][0], out[0][1], True)
    return out


class Renderer:
    def __init__(self, flow, src_pdf, field="text", family=FAMILY):
        self.flow = flow
        self.field = field
        self.fonts = Fonts(family)
        self.src = pymupdf.open(src_pdf)
        self.doc = pymupdf.open()
        self.page = None
        self.y = TOP
        self.header_right = HEADER_RIGHT
        self.floats = []          # active exclusion zones on the current page
        self.placements = {}      # item id -> output page, for the index
        self.footer_art = []
        for info in self.src[0].get_image_info(xrefs=True):
            if info["bbox"][1] > 786:
                self.footer_art.append((info["xref"], info["bbox"]))

    # ---------- page furniture ----------
    def new_page(self):
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.fonts.install(self.page)
        self.draw_furniture()
        self.y = TOP
        self.floats = []
        return self.page

    def draw_furniture(self):
        col = rgb(HEADER_COLOR)
        self.page.insert_text((LEFT, HEADER_BASE), HEADER_LEFT, fontname="f-b",
                              fontsize=HEADER_SIZE, color=col)
        w = self.fonts.fonts[(True, False)].text_length(self.header_right, HEADER_SIZE)
        self.page.insert_text((512.7 - w, HEADER_BASE), self.header_right,
                              fontname="f-b", fontsize=HEADER_SIZE, color=col)
        for xref, rect in self.footer_art:
            img = self.src.extract_image(xref)
            self.page.insert_image(pymupdf.Rect(*rect), stream=img["image"])

    # ---------- geometry ----------
    def right_edge(self, y_top, y_bot):
        edge = RIGHT
        for f in self.floats:
            if y_bot > f["y0"] and y_top < f["y1"]:
                edge = min(edge, f["x1"])
        return edge

    def float_bottom(self):
        return max([f["y1"] for f in self.floats], default=TOP)

    def ensure_space(self, need):
        if self.y + need > BOTTOM:
            self.new_page()

    # ---------- text ----------
    def wrap(self, tokens, x, size_hint):
        """Wrap tokens into lines, honouring float exclusion zones."""
        lines, cur, cur_w = [], [], 0.0
        y = self.y
        lh = size_hint * LINE_RATIO
        edge = self.right_edge(y, y + lh)
        for tok in tokens:
            w = self.fonts.width(tok[0], tok[1])
            sp = 0.0 if len(tok) > 2 and not tok[2] else self.fonts.width(" ", tok[1])
            add = w if not cur else cur_w + sp + w
            if cur and x + add > edge:
                lines.append((cur, cur_w, edge))
                cur, cur_w = [tok], w
                y += lh
                if y + lh > BOTTOM:
                    y = TOP           # continues on the next page
                edge = self.right_edge(y, y + lh)
            else:
                cur, cur_w = cur + [tok], add
        if cur:
            lines.append((cur, cur_w, edge))
        return lines

    def draw_line(self, tokens, x, y_top, justify, edge):
        """Draw one line.  A token may carry a third element set to False to
        say it follows the previous word without a space (index page numbers)."""
        size = max(t[1]["size"] for t in tokens)
        base = y_top + size * ASCENT
        natural = sum(self.fonts.width(t[0], t[1]) for t in tokens)
        gaps = sum(1 for i, t in enumerate(tokens)
                   if i and (len(t) < 3 or t[2]))
        space_w = self.fonts.width(" ", tokens[0][1])
        if justify and gaps > 0:
            wide = (edge - x - natural) / gaps
            space_w = min(max(wide, space_w * 0.6), space_w * 3.2)
        cx = x
        for i, tok in enumerate(tokens):
            word, run = tok[0], tok[1]
            if i and (len(tok) < 3 or tok[2]):
                cx += space_w
            name = self.fonts.names[self.fonts.key(run)]
            self.page.insert_text((cx, base), word, fontname=name,
                                  fontsize=run["size"], color=rgb(run["color"]))
            w = self.fonts.width(word, run)
            if run.get("u"):
                uy = base + run["size"] * 0.11
                self.page.draw_line((cx, uy), (cx + w, uy),
                                    color=rgb(run["color"]), width=0.6)
            cx += w

    def draw_bullet(self, glyph, x, y_top, size, color):
        """Draw a list marker.

        The source uses Symbol/Wingdings bullets that no text font carries, so
        the round and square markers are drawn as vector shapes at the size the
        original used; the dash bullets are ordinary characters.
        """
        cy = y_top + size * 0.55
        if glyph in ("●", "\uf0b7", "•", "○"):
            r = size * 0.185
            self.page.draw_circle((x + r + 0.6, cy), r, color=None if glyph != "○" else color,
                                  fill=None if glyph == "○" else color, width=0.8)
            if glyph == "○":
                self.page.draw_circle((x + r + 0.6, cy), r, color=color, fill=None, width=0.8)
        elif glyph in ("▪", "■"):
            h = size * 0.30
            self.page.draw_rect(pymupdf.Rect(x + 0.6, cy - h / 2, x + 0.6 + h, cy + h / 2),
                                color=None, fill=color)
        elif glyph in ("⮚", "⮞", "➢"):
            w, h = size * 0.36, size * 0.36
            self.page.draw_polyline([(x + 0.6, cy - h / 2), (x + 0.6 + w, cy),
                                     (x + 0.6, cy + h / 2), (x + 0.6, cy - h / 2)],
                                    color=None, fill=color)
        else:
            self.page.insert_text((x, y_top + size * ASCENT), glyph,
                                  fontname="f-r", fontsize=size, color=color)

    # ---------- flow ----------
    def render(self):
        self.new_page()
        for item in self.flow:
            t = item["type"]
            if t == "spacer":
                self.advance_spacer(item)
            elif t == "image":
                self.place_image(item)
            else:
                self.place_para(item)
            self.placements[item.get("id")] = self.doc.page_count
        return self.doc

    # ---------- cover ----------
    def render_cover(self, items, texts):
        """The cover keeps the original absolute layout: a banner image with
        the title over it, then centred and left-aligned lines."""
        self.new_page()
        for item in items:
            if item["type"] == "image":
                x0, y0, _, _ = item["bbox"]
                self.draw_figure(item, x0, y0)
        for item in items:
            if item["type"] != "image":
                self.draw_cover_line(item, texts)
        return self.doc

    def draw_cover_line(self, item, texts):
        runs = item.get("runs") or []
        source = "".join(r["text"] for r in runs).strip()
        if not source:
            return
        text = texts.get(source, source)
        run = max(runs, key=lambda r: r["size"])
        size = run["size"]
        x0, x1 = item.get("indent", LEFT), item.get("x1", RIGHT)
        centred = abs((x0 + x1) / 2 - (LEFT + RIGHT) / 2) < 60 and x0 > LEFT + 8
        w = self.fonts.width(text, run)
        if centred:
            x = (x0 + x1) / 2 - w / 2
        else:
            x = x0
        while w > RIGHT - LEFT and size > 8:           # keep the title on one line
            size -= 1
            run = dict(run, size=size)
            w = self.fonts.width(text, run)
            x = (x0 + x1) / 2 - w / 2 if centred else x0
        base = item["y0"] + size * ASCENT
        self.page.insert_text((x, base), text,
                              fontname=self.fonts.names[self.fonts.key(run)],
                              fontsize=size, color=rgb(run["color"]))

    # ---------- index ----------
    def render_index(self, title, entries):
        """Two-column alphabetical index, as in the original."""
        col_x = (LEFT, 315.6)
        col_w = RIGHT - 315.6          # the narrower of the two columns
        size = 9.0
        lh = size * 1.32
        hang = 11.0
        self.new_page()
        head = {"size": 13.56, "b": True, "i": False, "color": 0}
        self.page.insert_text((LEFT, TOP + 13.56 * ASCENT), title,
                              fontname="f-b", fontsize=13.56, color=(0, 0, 0))
        top = TOP + 13.56 * LINE_RATIO + 12.0
        y = top
        col = 0
        term_run = {"size": size, "b": True, "i": False, "color": 0}
        page_run = {"size": size, "b": False, "i": False, "color": 0}

        for term, page in entries:
            tokens = [(w, term_run) for w in term.split()]
            tokens.append((f", {page}", page_run, False))
            lines, cur, cur_w = [], [], 0.0
            width = col_w
            for tok in tokens:
                tw = self.fonts.width(tok[0], tok[1])
                sp = 0.0 if len(tok) > 2 and not tok[2] else self.fonts.width(" ", tok[1])
                add = tw if not cur else cur_w + sp + tw
                limit = width if not lines else width - hang
                if cur and add > limit:
                    lines.append(cur)
                    cur, cur_w = [tok], tw
                else:
                    cur, cur_w = cur + [tok], add
            if cur:
                lines.append(cur)
            # the page number must never be stranded on a line of its own
            if len(lines) > 1 and len(lines[-1]) == 1:
                lines[-1].insert(0, lines[-2].pop())
                if not lines[-2]:
                    lines.pop(-2)

            if y + lh * len(lines) > BOTTOM:
                col += 1
                if col > 1:
                    self.new_page()
                    col = 0
                    y = TOP
                else:
                    y = top
            for i, toks in enumerate(lines):
                x = col_x[col] + (hang if i else 0)
                self.draw_line(toks, x, y, False, col_x[col] + col_w)
                y += lh
            y += lh * 0.35
        return self.doc

    def advance_spacer(self, item):
        step = item["size"] * LINE_RATIO
        if self.y + step > BOTTOM:
            return                     # never carry blank lines onto a new page
        self.y += step

    def runs_for(self, item):
        if self.field != "text" and item.get("ru_runs"):
            return item["ru_runs"]
        return item["runs"]

    def place_para(self, item):
        if item.get("page_break") and self.y > TOP + 1:
            self.new_page()
        runs = self.runs_for(item)
        tokens = tokenize(runs, self.field)
        if not tokens:
            return
        indent = LEFT if item["type"] == "heading" else item.get("indent", LEFT)
        deco = item.get("deco") or {}
        size_hint = max(r["size"] for r in runs)
        justify = item.get("justified", False) and item["type"] != "heading"

        gap = item.get("gap")
        gap = 0.0 if gap is None else min(max(gap - LEADING, 0.0), 18.0)
        if item["type"] == "heading" and gap < 6.0 and self.y > TOP + 1:
            gap = 8.0                  # headings always get their air back
        self.y += gap

        lh = size_hint * LINE_RATIO
        need = lh + (6.0 if deco.get("shade") else 0.0)
        # a heading must not be left stranded at the foot of a page
        if item["type"] == "heading":
            need = lh * 2.6
        self.ensure_space(need)

        lines = self.wrap(tokens, indent, size_hint)

        if deco.get("shade"):
            h = lh * len(lines) + 1.0
            self.page.draw_rect(pymupdf.Rect(SHADE_X0, self.y - 1.2, SHADE_X1,
                                             self.y - 1.2 + h),
                                color=None, fill=deco["shade"])
        if deco.get("rule_above"):
            self.page.draw_line((SHADE_X0, self.y - 4.5), (SHADE_X1, self.y - 4.5),
                                color=deco["rule_above"], width=0.7)

        for i, (toks, nat, edge) in enumerate(lines):
            size = max(t[1]["size"] for t in toks)
            lh = size * LINE_RATIO
            if self.y + lh > BOTTOM:
                self.new_page()
                edge = self.right_edge(self.y, self.y + lh)
            last = (i == len(lines) - 1)
            if i == 0 and item.get("bullet"):
                self.draw_bullet(item["bullet"], indent - 18.0, self.y, size,
                                 rgb(runs[0]["color"]))
            self.draw_line(toks, indent, self.y, justify and not last, edge)
            self.y += lh

        if deco.get("shade"):
            self.page.draw_line((SHADE_X0, self.y + 0.2), (SHADE_X1, self.y + 0.2),
                                color=(0, 0, 0), width=0.5)
        if deco.get("rule_below"):
            self.page.draw_line((SHADE_X0, self.y + 3.0), (SHADE_X1, self.y + 3.0),
                                color=deco["rule_below"], width=0.7)

    def draw_figure(self, item, ox, oy, scale=1.0):
        """Draw every bitmap of a figure, keeping their relative registration."""
        for part in item["parts"]:
            rx0, ry0, rx1, ry1 = part["rel"]
            rect = pymupdf.Rect(ox + rx0 * scale, oy + ry0 * scale,
                                ox + rx1 * scale, oy + ry1 * scale)
            img = self.src.extract_image(part["xref"])
            self.page.insert_image(rect, stream=img["image"])

    def place_image(self, item):
        x0, y0, x1, y1 = item["bbox"]
        w, h = x1 - x0, y1 - y0

        if item.get("float"):
            # keep the original horizontal position; wrap text beside it
            if self.y + h > BOTTOM and h < BOTTOM - TOP:
                self.new_page()
            top = max(self.y, self.float_bottom())
            if top + h > BOTTOM and h < BOTTOM - TOP:
                self.new_page()
                top = self.y
            self.draw_figure(item, x0, top)
            wrap_x1 = item.get("wrap_x1") or (x0 - 8.0)
            if x0 > LEFT + 60:
                self.floats.append({"y0": top - 2, "y1": top + h + 4,
                                    "x1": min(wrap_x1, x0 - 6.0)})
            else:
                self.y = top + h + 6.0
            return

        gap = 8.0
        scale = 1.0
        if h > BOTTOM - TOP:                          # taller than a page
            scale = (BOTTOM - TOP) / h
            w, h = w * scale, h * scale
        if self.y + gap + h > BOTTOM:
            self.new_page()
        else:
            self.y += gap
        self.y = max(self.y, self.float_bottom())
        cx = max(LEFT, (PAGE_W - w) / 2)
        self.draw_figure(item, cx, self.y, scale)
        self.y += h + gap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("flow")
    ap.add_argument("out")
    ap.add_argument("--field", default="text")
    ap.add_argument("--family", default=FAMILY)
    ap.add_argument("--src", default="NUCLEARES - User Manual.pdf")
    ap.add_argument("--header-right", default=HEADER_RIGHT)
    args = ap.parse_args()

    data = json.load(open(args.flow, encoding="utf-8"))
    r = Renderer(data["flow"], args.src, field=args.field, family=args.family)
    r.header_right = args.header_right
    doc = r.render()
    doc.save(args.out, deflate=True, garbage=3)
    print(f"{args.out}: {doc.page_count} pages")
    return r


if __name__ == "__main__":
    main()
