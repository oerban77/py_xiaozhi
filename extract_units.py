# -*- coding: utf-8 -*-
"""Extract complete comments and string literals containing CJK from src/.

These are the natural translation units (whole comment / whole string),
which is far more reliable than translating maximal CJK runs.
"""
import io
import json
import re
import tokenize
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "src"

cjk_re = re.compile(r"[一-鿿]")

units = Counter()  # text -> count
kinds = {}  # text -> "comment" | "string"


def add(text, kind):
    text = text.strip()
    if not text or not cjk_re.search(text):
        return
    units[text] += 1
    kinds[text] = kind


for f in sorted(SRC.rglob("*.py")):
    raw = io.open(str(f), encoding="utf-8").read()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(raw).readline))
    except tokenize.TokenError:
        continue
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            # strip leading "# " and "#"
            body = tok.string.lstrip("#").strip()
            add(body, "comment")
        elif tok.type == tokenize.STRING:
            # decode string literal body
            s = tok.string
            try:
                val = eval(s)  # noqa: S307 - parse str literal
            except Exception:
                # f-strings / bytes fall back to manual strip
                val = None
            if isinstance(val, str):
                add(val, "string")

print(f"Unique translation units: {len(units)}")
print(f"  comments: {sum(1 for t in units if kinds[t] == 'comment')}")
print(f"  strings:  {sum(1 for t in units if kinds[t] == 'string')}")

items = sorted(units.items(), key=lambda x: (-x[1], -len(x[0])))
with open("_units.txt", "w", encoding="utf-8") as f:
    for text, count in items:
        f.write(f"[{count}|{kinds[text]}] {text}\n")

# also emit a JSON for programmatic consumption
with open("_units.json", "w", encoding="utf-8") as f:
    json.dump(
        [{"text": t, "count": c, "kind": kinds[t]} for t, c in items],
        f,
        ensure_ascii=False,
        indent=1,
    )
print("Wrote _units.txt and _units.json")
