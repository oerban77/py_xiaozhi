import io
import pathlib
import re

cjk_re = re.compile(r'[\u4e00-\u9fff]')
files = list(pathlib.Path('src').rglob('*.py'))
result = []
for f in files:
    text = io.open(str(f), encoding='utf-8').read()
    count = len(cjk_re.findall(text))
    if count > 0:
        result.append((str(f.relative_to('.')), count, text))
result.sort(key=lambda x: -x[1])
for r in result[:30]:
    print(f"{r[1]:5d}  {r[0]}")
print(f"\nTotal files with CJK: {len(result)}")
print(f"Total CJK chars: {sum(r[1] for r in result)}")
