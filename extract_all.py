# -*- coding: utf-8 -*-
"""Extract every Chinese-containing string literal + comment/docstring segment."""
import ast
import io
import re
import sys
import tokenize
from collections import Counter
from pathlib import Path

ROOT = Path("src")
CJK = re.compile(r"[一-鿿]")

strings = []   # (file, lineno, kind, text)
comments = []  # (file, lineno, text)

for py in sorted(ROOT.rglob("*.py")):
    src = py.read_text(encoding="utf-8")
    # --- string literals via ast ---
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f"SYNTAX-ERR {py}: {e}", file=sys.stderr)
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if CJK.search(node.value):
                strings.append((str(py), node.lineno, "str", node.value))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bytes):
            pass
    # --- comments + inline via tokenize ---
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenError:
        continue
    for tok in toks:
        if tok.type == tokenize.COMMENT and CJK.search(tok.string):
            comments.append((str(py), tok.start[0], tok.string))

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

uniq = Counter()
for _, _, _, t in strings:
    uniq[norm(t)] += 1
for _, _, t in comments:
    uniq[norm(t)] += 1

out = Path("_extract_all.txt")
with out.open("w", encoding="utf-8") as f:
    f.write(f"# total unique segments: {len(uniq)}  (strings={len(strings)} comments={len(comments)})\n")
    f.write(f"# files: {len(set(x[0] for x in strings)) + len(set(x[0] for x in comments))}\n\n")
    for seg, n in sorted(uniq.items(), key=lambda kv: (-kv[1], kv[0])):
        f.write(f"[{n}] {seg}\n")

print(f"strings={len(strings)} comments={len(comments)} unique={len(uniq)} -> {out}")
