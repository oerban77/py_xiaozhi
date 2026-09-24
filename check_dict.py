# -*- coding: utf-8 -*-
"""Check which TRANSLATIONS keys actually appear in src/ files."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from apply_translations import TRANSLATIONS  # noqa: E402

ROOT = Path(__file__).parent
SRC = ROOT / "src"

all_text = {}
for f in sorted(SRC.rglob("*.py")):
    all_text[str(f)] = io.open(str(f), encoding="utf-8").read()

blob = "\n".join(all_text.values())

hit = 0
miss = []
for cn, en in TRANSLATIONS.items():
    if cn in blob:
        hit += 1
    else:
        miss.append(cn)

print(f"Dictionary entries: {len(TRANSLATIONS)}")
print(f"Found in source:    {hit}")
print(f"NOT found in source:{len(miss)}")
print("\nSample of NOT-found entries:")
for m in miss[:40]:
    print(f"  {m!r}")
