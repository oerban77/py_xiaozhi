# -*- coding: utf-8 -*-
"""Extract unique maximal CJK runs across src/ (string literals + comments)."""
import ast
import io
import re
import sys
import tokenize
from collections import Counter
from pathlib import Path

ROOT = Path("src")
CJK = re.compile(r"[一-鿿]+")

runs = Counter()
segments = []  # (file, lineno, kind, text)

for py in sorted(ROOT.rglob("*.py")):
    src = py.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f"SYNTAX-ERR {py}: {e}", file=sys.stderr)
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and CJK.search(node.value):
            segments.append((str(py), node.lineno, "str", node.value))
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenError:
        continue
    for tok in toks:
        if tok.type == tokenize.COMMENT and CJK.search(tok.string):
            segments.append((str(py), tok.start[0], "comment", tok.string))

for _, _, kind, text in segments:
    for m in CJK.finditer(text):
        runs[m.group(0)] += 1

out = Path("_runs.txt")
with out.open("w", encoding="utf-8") as f:
    f.write(f"# unique CJK runs: {len(runs)}  (total occurrences: {sum(runs.values())})\n\n")
    for r, n in sorted(runs.items(), key=lambda kv: (-len(kv[0]), kv[0])):
        f.write(f"{r}\t[{n}]\n")

print(f"unique runs={len(runs)} occurrences={sum(runs.values())} -> {out}")
