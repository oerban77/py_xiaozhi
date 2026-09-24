# -*- coding: utf-8 -*-
import re
from typing import List

from .base import KeywordConverter

# Initials list (sorted by length descending so longer initials match first)
INITIALS = [
    "zh", "ch", "sh",  # Retroflex sound (2 characters, prioritized matching)
    "b", "p", "m", "f",  # labial sound
    "d", "t", "n", "l",  # tip-of-tongue sound
    "g", "k", "h",  # root-of-tongue sound
    "j", "q", "x",  # blade-of-tongue sound
    "r", "z", "c", "s",  # other
    "y", "w",  # zero-initial marker
]


class PinyinConverter(KeywordConverter):
    def __init__(self):
        self._pypinyin = None
        self._style = None

    def _ensure_pypinyin(self):
        if self._pypinyin is None:
            try:
                from pypinyin import Style, lazy_pinyin
                self._pypinyin = lazy_pinyin
                self._style = Style.TONE
            except ImportError:
                raise ImportError(
                    "pypinyin is required for Chinese wake word conversion. "
                    "Install it with: pip install pypinyin"
                )

    @property
    def language(self) -> str:
        return "zh"

    @property
    def model_path(self) -> str:
        return "models/zh"

    def can_convert(self, text: str) -> bool:
        chinese_pattern = re.compile(r"[\u4e00-\u9fff]")
        return bool(chinese_pattern.search(text))

    def _split_pinyin(self, pinyin: str) -> List[str]:
        if not pinyin:
            return []

        pinyin_lower = pinyin.lower()

        for initial in INITIALS:
            if pinyin_lower.startswith(initial):
                final = pinyin[len(initial):]
                if final:
                    return [initial, final]
                else:
                    return [initial]

        return [pinyin]

    def convert(self, text: str) -> str:
        self._ensure_pypinyin()

        pinyin_list = self._pypinyin(text, style=self._style)

        split_parts = []
        for pinyin in pinyin_list:
            parts = self._split_pinyin(pinyin)
            split_parts.extend(parts)

        pinyin_str = " ".join(split_parts)
        return f"{pinyin_str} @{text}"
