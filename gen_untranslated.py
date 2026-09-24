# -*- coding: utf-8 -*-
"""Find all CJK runs in src/ that are NOT yet in apply_translations.TRANSLATIONS."""
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from apply_translations import SKIP_STRINGS, TRANSLATIONS  # noqa: E402

ROOT = Path(__file__).parent
SRC = ROOT / "src"

cjk_re = re.compile(r"[一-鿿]+")

# Collect all maximal CJK runs from every .py file
runs = {}
for f in sorted(SRC.rglob("*.py")):
    text = io.open(str(f), encoding="utf-8").read()
    for m in cjk_re.finditer(text):
        run = m.group(0)
        # skip protected strings
        if any(skip in run for skip in SKIP_STRINGS if skip.strip('"')):
            continue
        runs[run] = runs.get(run, 0) + 1

# Which are missing from the dictionary?
missing = {k: v for k, v in runs.items() if k not in TRANSLATIONS}
print(f"Total unique CJK runs in src/: {len(runs)}")
print(f"Already in dictionary:         {len(runs) - len(missing)}")
print(f"MISSING from dictionary:       {len(missing)}")

# Sort by frequency desc, then length
items = sorted(missing.items(), key=lambda x: (-x[1], -len(x[0])))
with open("_missing.txt", "w", encoding="utf-8") as f:
    for text, count in items:
        f.write(f"[{count}] {text}\n")
print("Wrote _missing.txt")
