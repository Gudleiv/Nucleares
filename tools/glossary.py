#!/usr/bin/env python3
"""Build an EN->RU glossary from the game's own localisation files.

EN.dat / RU.dat are "ID;text" pairs and PrincipalEN.xml / PrincipalRU.xml are
the assistant's dialogue lines keyed by <id>.  Both give authoritative Russian
wording for the in-game terminology, which the manual must match exactly.

Only terms that actually occur in the manual are kept, so translators get a
short, relevant list instead of five thousand strings.

Usage: python3 tools/glossary.py work/flow.json work/glossary.json
"""
import json
import re
import sys
import xml.etree.ElementTree as ET

MAX_WORDS = 7
NOISE = re.compile(r"[@<>\[\]{}|]|\bhttp")


def load_dat(path):
    out = {}
    with open(path, encoding="utf-8-sig") as fh:
        for i, line in enumerate(fh):
            line = line.rstrip("\n")
            if i == 0 or ";" not in line:
                continue
            key, value = line.split(";", 1)
            out[key.strip()] = value.strip()
    return out


def load_xml(path):
    out = {}
    root = ET.parse(path).getroot()
    for msg in root.iter("mensaje"):
        mid = msg.findtext("id")
        text = msg.findtext("texto")
        if mid and text:
            out[mid.strip()] = text.strip()
    return out


def clean(text):
    text = re.sub(r"@\d+@\d+$", "", text)
    text = text.replace(" ", " ").replace("<br>", " ")
    return re.sub(r"\s+", " ", text).strip()


def manual_text(flow_path):
    flow = json.load(open(flow_path, encoding="utf-8"))["flow"]
    return " ".join(e.get("text", "") for e in flow)


def occurs(term, body_lower):
    """Whole-word match, so "CAN" does not hit "cannot"."""
    return re.search(r"(?<![A-Za-z])" + re.escape(term.lower()) + r"(?![A-Za-z])",
                     body_lower) is not None


def main(flow_path, dst):
    en = load_dat("EN.dat")
    ru = load_dat("RU.dat")
    en.update(load_xml("PrincipalEN.xml"))
    ru.update(load_xml("PrincipalRU.xml"))

    body = manual_text(flow_path).lower()
    pairs = {}
    for key in en.keys() & ru.keys():
        a, b = clean(en[key]), clean(ru[key])
        if not a or not b or NOISE.search(a):
            continue
        if len(a.split()) > MAX_WORDS or len(a) < 5:
            continue
        if not re.search(r"[A-Za-z]{3}", a):
            continue
        if not occurs(a, body):
            continue
        pairs.setdefault(a, set()).add(b)

    glossary = []
    for a in sorted(pairs, key=lambda s: (-len(s), s.lower())):
        variants = sorted(pairs[a])
        glossary.append({"en": a, "ru": variants[0],
                         **({"alt": variants[1:]} if len(variants) > 1 else {})})

    json.dump(glossary, open(dst, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"{len(glossary)} glossary terms found in the manual -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
