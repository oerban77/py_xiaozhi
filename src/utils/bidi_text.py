"""Bidirectional (RTL) text support for terminal / TUI rendering.

The GUI (Qt/QML) applies the Unicode Bidirectional Algorithm (UBA) natively, so
Arabic / Hebrew text is displayed right-to-left automatically. Rich and Textual
do NOT apply the UBA, so the same text appears left-to-right (reversed) in the
TUI. This module provides a self-contained, dependency-free implementation of
the parts of the UBA needed to render RTL scripts correctly in a terminal:

1. ``bidi_visual`` — reorder a logical string into visual order (RTL runs are
   reversed, embedded LTR runs such as numbers / Latin are kept in order).
2. ``reshape_arabic`` — convert Arabic letters to their contextual joining
   forms (isolated / initial / medial / final) so the terminal font connects
   them correctly.
3. ``to_visual`` — convenience wrapper that reshapes + reorders a string.

The implementation is intentionally pragmatic: it targets the common case of
Arabic / Hebrew text (possibly mixed with numbers and Latin) and does not
implement the full UBA (no explicit directional isolates, no mirroring of
brackets beyond the standard set, no paragraph-level embedding levels). It is
good enough for chat / log lines and is fully deterministic.
"""

from __future__ import annotations

import unicodedata

# ---------------------------------------------------------------------------
# Character classification
# ---------------------------------------------------------------------------

# Unicode ranges for RTL scripts we care about.
_RTL_RANGES = (
    (0x0590, 0x08FF),  # Hebrew + Arabic + Syriac + Arabic Supplement
    (0xFB1D, 0xFB4F),  # Hebrew presentation forms
    (0xFB50, 0xFDFF),  # Arabic presentation forms-A
    (0xFE70, 0xFEFF),  # Arabic presentation forms-B
    (0x10800, 0x10CFF),  # Syriac / Arabic extended
    (0x1E800, 0x1EEFF),  # Mende / Arabic mathematical
)

# Characters that are "neutral" but should be treated as part of the
# surrounding RTL run (Arabic-Indic digits, common punctuation).
_NEUTRAL_ALWAYS_RTL = set("،؛؟")


def _is_rtl_char(ch: str) -> bool:
    """Return True if ``ch`` is a right-to-left character."""
    cp = ord(ch)
    for start, end in _RTL_RANGES:
        if start <= cp <= end:
            return True
    return False


def _is_ltr_char(ch: str) -> bool:
    """Return True if ``ch`` is a strong left-to-right character."""
    return unicodedata.bidirectional(ch) in ("L", "LRE", "LRO")


def _is_number_char(ch: str) -> bool:
    """Return True if ``ch`` is a digit (European or Arabic-Indic)."""
    return ch.isdigit() or unicodedata.bidirectional(ch) in ("EN", "AN")


def _is_weak_or_neutral(ch: str) -> bool:
    """Return True if ``ch`` is neither strong RTL nor strong LTR."""
    return not (_is_rtl_char(ch) or _is_ltr_char(ch))


# ---------------------------------------------------------------------------
# Bidi reordering (visual order)
# ---------------------------------------------------------------------------

def _split_runs(text: str) -> list[tuple[str, str]]:
    """Split ``text`` into runs tagged with their direction.

    Returns a list of ``(direction, chunk)`` where ``direction`` is one of
    ``"rtl"``, ``"ltr"`` or ``"neutral"``. Neutral characters are folded into
    the neighbouring run so punctuation stays attached to its word.
    """
    runs: list[tuple[str, str]] = []
    current_dir: str | None = None
    current: list[str] = []

    def flush() -> None:
        nonlocal current_dir, current
        if current:
            runs.append((current_dir or "neutral", "".join(current)))
            current = []
            current_dir = None

    for ch in text:
        if _is_rtl_char(ch) or ch in _NEUTRAL_ALWAYS_RTL:
            if current_dir == "ltr":
                flush()
            current_dir = "rtl"
            current.append(ch)
        elif _is_ltr_char(ch):
            if current_dir == "rtl":
                flush()
            current_dir = "ltr"
            current.append(ch)
        else:
            # Weak / neutral: attach to the current run if any, else defer.
            if current_dir is None:
                current_dir = "neutral"
            current.append(ch)

    flush()
    return runs


def bidi_visual(text: str) -> str:
    """Reorder ``text`` into visual (display) order for RTL scripts.

    RTL runs are reversed so they read right-to-left; LTR runs (numbers, Latin)
    are kept in their natural order. The overall line is laid out so that the
    first RTL run appears at the right edge.
    """
    if not text:
        return text

    runs = _split_runs(text)

    # Build the visual line. We process runs from the end to the start so that
    # the first logical RTL run ends up on the right.
    visual_parts: list[str] = []
    for direction, chunk in reversed(runs):
        if direction == "rtl":
            visual_parts.append(chunk[::-1])
        else:
            visual_parts.append(chunk)

    return "".join(visual_parts)


# ---------------------------------------------------------------------------
# Arabic reshaping (contextual joining forms)
# ---------------------------------------------------------------------------

# Arabic letters that have contextual forms, mapped from their isolated code
# point to (isolated, initial, medial, final) presentation forms.
_ARABIC_JOINING = {
    0x0622: (0xFE81, 0xFE81, 0xFE82, 0xFE82),  # آ
    0x0623: (0xFE83, 0xFE83, 0xFE84, 0xFE84),  # أ
    0x0624: (0xFE85, 0xFE85, 0xFE86, 0xFE86),  # ؤ
    0x0626: (0xFE87, 0xFE87, 0xFE88, 0xFE88),  # ئ
    0x0627: (0xFE8D, 0xFE8D, 0xFE8E, 0xFE8E),  # ا
    0x0628: (0xFE8F, 0xFE91, 0xFE92, 0xFE90),  # ب
    0x0629: (0xFE93, 0xFE93, 0xFE94, 0xFE94),  # ة
    0x062A: (0xFE95, 0xFE97, 0xFE98, 0xFE96),  # ت
    0x062B: (0xFE99, 0xFE9B, 0xFE9C, 0xFE9A),  # ث
    0x062C: (0xFE9D, 0xFE9F, 0xFEA0, 0xFE9E),  # ج
    0x062D: (0xFEA1, 0xFEA3, 0xFEA4, 0xFEA2),  # ح
    0x062E: (0xFEA5, 0xFEA7, 0xFEA8, 0xFEA6),  # خ
    0x062F: (0xFEA9, 0xFEA9, 0xFEAA, 0xFEAA),  # د
    0x0630: (0xFEAB, 0xFEAB, 0xFEAC, 0xFEAC),  # ذ
    0x0631: (0xFEAD, 0xFEAD, 0xFEAE, 0xFEAE),  # ر
    0x0632: (0xFEAF, 0xFEAF, 0xFEB0, 0xFEB0),  # ز
    0x0633: (0xFEB1, 0xFEB3, 0xFEB4, 0xFEB2),  # س
    0x0634: (0xFEB5, 0xFEB7, 0xFEB8, 0xFEB6),  # ش
    0x0635: (0xFEB9, 0xFEBB, 0xFEBC, 0xFEBA),  # ص
    0x0636: (0xFEBD, 0xFEBF, 0xFEC0, 0xFEBE),  # ض
    0x0637: (0xFEC1, 0xFEC3, 0xFEC4, 0xFEC2),  # ط
    0x0638: (0xFEC5, 0xFEC7, 0xFEC8, 0xFEC6),  # ظ
    0x0639: (0xFEC9, 0xFECB, 0xFECC, 0xFECA),  # ع
    0x063A: (0xFECD, 0xFECF, 0xFED0, 0xFECE),  # غ
    0x0641: (0xFED1, 0xFED3, 0xFED4, 0xFED2),  # ف
    0x0642: (0xFED5, 0xFED7, 0xFED8, 0xFED6),  # ق
    0x0643: (0xFED9, 0xFEDB, 0xFEDC, 0xFEDA),  # ك
    0x0644: (0xFEDD, 0xFEDF, 0xFEE0, 0xFEDE),  # ل
    0x0645: (0xFEE1, 0xFEE3, 0xFEE4, 0xFEE2),  # م
    0x0646: (0xFEE5, 0xFEE7, 0xFEE8, 0xFEE6),  # ن
    0x0647: (0xFEE9, 0xFEEB, 0xFEEC, 0xFEEA),  # ه
    0x0648: (0xFEED, 0xFEED, 0xFEEE, 0xFEEE),  # و
    0x0649: (0xFEEF, 0xFEEF, 0xFEF0, 0xFEF0),  # ى
    0x064A: (0xFEF1, 0xFEF3, 0xFEF4, 0xFEF2),  # ي
    0x064B: (0xFE70, 0xFE70, 0xFE71, 0xFE71),  # ً
    0x064C: (0xFE72, 0xFE72, 0xFE72, 0xFE72),  # ٌ
    0x064D: (0xFE74, 0xFE74, 0xFE74, 0xFE74),  # ٍ
    0x064E: (0xFE76, 0xFE76, 0xFE77, 0xFE77),  # َ
    0x064F: (0xFE78, 0xFE78, 0xFE79, 0xFE79),  # ُ
    0x0650: (0xFE7A, 0xFE7A, 0xFE7B, 0xFE7B),  # ِ
    0x0651: (0xFE7C, 0xFE7C, 0xFE7D, 0xFE7D),  # ّ
    0x0652: (0xFE7E, 0xFE7E, 0xFE7F, 0xFE7F),  # ْ
}

# Letters that never join to the following letter (right-joining only).
_NON_JOINING = {0x0622, 0x0623, 0x0624, 0x0627, 0x0629, 0x062F, 0x0630,
                0x0631, 0x0632, 0x0648, 0x0649}


def _is_arabic_letter(ch: str) -> bool:
    return ord(ch) in _ARABIC_JOINING


def reshape_arabic(text: str) -> str:
    """Convert Arabic letters in ``text`` to their contextual joining forms.

    The input is expected to be in logical order (as typed). The output uses
    Arabic presentation forms so a terminal font renders connected glyphs.
    Non-Arabic characters are passed through unchanged.
    """
    if not text:
        return text

    chars = list(text)
    n = len(chars)
    out: list[str] = []

    for i, ch in enumerate(chars):
        cp = ord(ch)
        if cp not in _ARABIC_JOINING:
            out.append(ch)
            continue

        # Determine whether the previous / next characters allow joining.
        prev_joins = False
        if i > 0:
            prev_cp = ord(chars[i - 1])
            prev_joins = (
                prev_cp in _ARABIC_JOINING
                and prev_cp not in _NON_JOINING
            )

        next_joins = False
        if i + 1 < n:
            next_cp = ord(chars[i + 1])
            next_joins = (
                next_cp in _ARABIC_JOINING
                and cp not in _NON_JOINING
            )

        isolated, initial, medial, final = _ARABIC_JOINING[cp]

        if prev_joins and next_joins:
            out.append(chr(medial))
        elif prev_joins and not next_joins:
            out.append(chr(final))
        elif not prev_joins and next_joins:
            out.append(chr(initial))
        else:
            out.append(chr(isolated))

    return "".join(out)


# ---------------------------------------------------------------------------
# Public convenience API
# ---------------------------------------------------------------------------

def to_visual(text: str) -> str:
    """Reshape + reorder ``text`` for correct RTL display in a terminal.

    This is the function TUI widgets should call before rendering a string that
    may contain Arabic / Hebrew. It is a no-op for pure LTR text.
    """
    if not text:
        return text
    # Only bother when the string actually contains RTL characters.
    if not any(_is_rtl_char(ch) for ch in text):
        return text
    reshaped = reshape_arabic(text)
    return bidi_visual(reshaped)


def contains_rtl(text: str) -> bool:
    """Return True if ``text`` contains any right-to-left character."""
    return any(_is_rtl_char(ch) for ch in text)
