# -*- coding: utf-8 -*-
"""Extract unique LINES containing CJK from comments and string literals.

Splitting multi-line docstrings into lines dramatically increases reuse
and shrinks the translation dictionary.
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

lines_counter = Counter()


def add(text):
    text = text.strip()
    if text and cjk_re.search(text):
        lines_counter[text] += 1


for f in sorted(SRC.rglob("*.py")):
    raw = io.open(str(f), encoding="utf-8").read()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(raw).readline))
    except tokenize.TokenError:
        continue
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            add(tok.string.lstrip("#").strip())
        elif tok.type == tokenize.STRING:
            s = tok.string
            try:
                val = eval(s)  # noqa: S307
            except Exception:
                continue
            if isinstance(val, str):
                for ln in val.split("\n"):
                    add(ln)

print(f"Unique CJK lines: {len(lines_counter)}")
items = sorted(lines_counter.items(), key=lambda x: (-x[1], -len(x[0])))
with open("_lines.txt", "w", encoding="utf-8") as f:
    for text, count in items:
        f.write(f"[{count}] {text}\n")
with open("_lines.json", "w", encoding="utf-8") as f:
    json.dump([{"text": t, "count": c} for t, c in items], f, ensure_ascii=False, indent=1)
print("Wrote _lines.txt / _lines.json")

# length histogram
lens = [len(t) for t in lines_counter]
print(f"total chars: {sum(lens)}, avg len: {sum(lens)/len(lens):.1f}")
