# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")

from src.utils.bidi_text import to_visual, contains_rtl

s = "مرحبا بالعالم"
print("logical:", s)
print("visual :", to_visual(s))
print("rtl?   :", contains_rtl(s))
print("latin  :", to_visual("hello world"))
print("mixed  :", to_visual("abc مرحبا 123"))
print("num    :", to_visual("رقم 123 و 456"))
