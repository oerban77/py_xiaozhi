# -*- coding: utf-8 -*-
"""Extract CJK text from f-strings (JoinedStr) which eval() cannot handle."""
import ast
import io
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "src"
cjk_re = re.compile(r"[一-鿿]")

counter = Counter()


def pieces(node):
    out = []
    for v in node.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            out.append(v.value)
        else:
            out.append("\x00")
    return "".join(out)


for f in sorted(SRC.rglob("*.py")):
    raw = io.open(str(f), encoding="utf-8").read()
    try:
        tree = ast.parse(raw)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            text = pieces(node)
            for ln in text.split("\n"):
                ln = ln.strip()
                if ln and cjk_re.search(ln):
                    counter[ln] += 1

import sys

sys.stdout.reconfigure(encoding="utf-8")

with open("_fstrings.txt", "w", encoding="utf-8") as fh:
    for text, n in counter.most_common():
        fh.write(f"[{n}] {text}\n")
    fh.write(f"total {sum(counter.values())} unique {len(counter)}\n")
print("total", sum(counter.values()), "unique", len(counter))
