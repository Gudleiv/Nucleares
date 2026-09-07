#!/usr/bin/env python3
"""Enforce the game's own Russian terminology across the whole translation.

Each translator chunk is produced independently, so wording drifts: the same
component ends up as both «управляющие стержни» and «регулирующие стержни».
The game's shipped localisation (RU.dat) is the authority, and this pass
rewrites every deviation to match it, preserving grammatical case and the
original capitalisation.

Run over any JSON file: every string value is normalised in place.

Usage: python3 tools/normalize.py work/flow.ru.json work/headings.ru.json ...
"""
import json
import re
import sys

# (pattern, replacement) applied to Russian text.  Patterns are stems so that
# every grammatical case is caught; the required context keeps them honest.
RULES = [
    # control rods: the game says «регулирующие стержни»
    (r"управляющ(?=\w*[\s,]{1,3}(?:\w+[\s,]{1,3}){0,2}стерж)", "регулирующ"),
    (r"стержн(\w*)\s+управлени[яю]", r"регулирующ\1 стержн"),
    # freight pump: the game says «грузовой насос»
    (r"заправочный(?=[\s,]{1,3}(?:\w+[\s,]{1,3}){0,3}насос)", "грузовой"),
    (r"заправочн(?=\w*[\s,]{1,3}(?:\w+[\s,]{1,3}){0,3}насос)", "грузов"),
    (r"питающий(?=\s+насос)", "грузовой"),
    # fuel bay
    (r"топливн(ый|ого|ому|ым|ом)\s+отсек(\w*)", r"топливн\1 контейнер\2"),
    (r"топливные\s+отсеки", "топливные контейнеры"),
    (r"топливных\s+отсеков", "топливных контейнеров"),
    # misc house style
    (r"\bсинхроскоп", "синхроноскоп"),
    (r"\bсинхронооскоп", "синхроноскоп"),
    (r"(?<=\d)\s?%", " %"),
    (r"\s+([,.;:!?])", r"\1"),
    (r"«\s+", "«"),
    (r"\s+»", "»"),
    (r"\s{2,}", " "),
]

COMPILED = [(re.compile(p, re.IGNORECASE), r) for p, r in RULES]


def match_case(src, dst):
    """Keep the capitalisation pattern of the text being replaced."""
    if src.isupper():
        return dst.upper()
    if src[:1].isupper():
        return dst[:1].upper() + dst[1:]
    return dst


def fix(text):
    for rx, repl in COMPILED:
        def sub(m):
            out = m.expand(repl) if "\\" in repl else repl
            return match_case(m.group(0), out)
        text = rx.sub(sub, text)
    return text


def walk(node, stats):
    if isinstance(node, str):
        new = fix(node)
        if new != node:
            stats["changed"] += 1
        return new
    if isinstance(node, list):
        return [walk(v, stats) for v in node]
    if isinstance(node, dict):
        return {k: (walk(v, stats) if k in ("ru", "s", "text_ru") or
                    (isinstance(v, (list, dict))) else v)
                for k, v in node.items()}
    return node


def main(paths):
    for path in paths:
        data = json.load(open(path, encoding="utf-8"))
        stats = {"changed": 0}
        if isinstance(data, dict) and all(k.isdigit() for k in data):
            data = {k: walk(v, stats) for k, v in data.items()}
        else:
            data = walk(data, stats)
        json.dump(data, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"{path}: {stats['changed']} strings normalised")


if __name__ == "__main__":
    main(sys.argv[1:])
