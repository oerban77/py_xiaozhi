"""Bidirectional (RTL) text support for terminal / TUI rendering.

The GUI (Qt/QML) applies the Unicode Bidirectional Algorithm (UBA) natively, so
Arabic / Hebrew text is displayed right-to-left automatically. Rich and Textual
do NOT apply the UBA, so the same text appears left-to-right (reversed) in the
TUI. This module uses ``arabic-reshaper`` for Arabic contextual forms and a
pragmatic run reordering implementation for mixed-direction terminal text:

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

from arabic_reshaper import ArabicReshaper

_ARABIC_RESHAPER = ArabicReshaper(
    configuration={
        "delete_harakat": False,
        "shift_harakat_position": False,
        "support_ligatures": False,
    }
)

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


def _is_combining_mark(ch: str) -> bool:
    """Return True for Unicode combining and spacing marks."""
    return unicodedata.category(ch) in ("Mn", "Mc", "Me")


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
                trailing_space: list[str] = []
                while current and current[-1].isspace():
                    trailing_space.insert(0, current.pop())
                flush()
                if trailing_space:
                    runs.append(("ltr", "".join(trailing_space)))
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
    """Reorder ``text`` into visual (display) order for mixed-direction text.

    The key rule here is: do not reverse the whole line. We preserve the logical
    order of left-to-right runs, and only reverse the strong RTL runs in-place.
    This keeps a leading prefix such as ``Conversation:`` on the left, while
    Arabic/Hebrew content still renders right-to-left.
    """
    if not text:
        return text

    runs = _split_runs(text)
    if not runs:
        return text

    visual_parts: list[str] = []
    for direction, chunk in runs:
        if direction == "rtl":
            # Arabic and Hebrew display requires both shaping and visual
            # reordering. Shape the run first so the glyph sequence matches the
            # terminal font's contextual forms, then reverse grapheme clusters
            # so combining marks remain attached to their base characters.
            shaped = reshape_arabic(chunk)
            visual_parts.append("".join(reversed(_grapheme_clusters(shaped))))
        else:
            visual_parts.append(chunk)

    return "".join(visual_parts)


def _grapheme_clusters(text: str) -> list[str]:
    """Group combining marks with the preceding base character."""
    clusters: list[str] = []
    for ch in text:
        if clusters and _is_combining_mark(ch):
            clusters[-1] += ch
        else:
            clusters.append(ch)
    return clusters


def bidi_positions(text: str) -> tuple[list[int], list[int]]:
    """Return visual character positions and cursor boundaries for logical text."""
    character_positions = [0] * len(text)
    boundary_positions = [0] * (len(text) + 1)
    boundary_priorities = [-1] * (len(text) + 1)
    logical_offset = 0
    visual_offset = 0

    for direction, chunk in _split_runs(text):
        length = len(chunk)
        rtl = direction == "rtl"
        priority = 2 if rtl else 1
        spans: list[tuple[int, int]] = []
        for offset, ch in enumerate(chunk):
            if spans and _is_combining_mark(ch):
                start, _ = spans[-1]
                spans[-1] = (start, offset + 1)
            else:
                spans.append((offset, offset + 1))

        visual_run_offset = 0
        visual_spans = reversed(spans) if rtl else iter(spans)
        for start, end in visual_spans:
            cluster_length = end - start
            cluster_visual_start = visual_offset + visual_run_offset
            for offset in range(start, end):
                character_positions[logical_offset + offset] = (
                    cluster_visual_start + offset - start
                )
            for offset in range(cluster_length + 1):
                logical_boundary = logical_offset + start + offset
                if offset == 0 and rtl:
                    visual_boundary = cluster_visual_start + cluster_length
                elif offset == cluster_length and rtl:
                    visual_boundary = cluster_visual_start
                else:
                    visual_boundary = cluster_visual_start + offset
                if priority >= boundary_priorities[logical_boundary]:
                    boundary_positions[logical_boundary] = visual_boundary
                    boundary_priorities[logical_boundary] = priority
            visual_run_offset += cluster_length
        logical_offset += length
        visual_offset += length

    return character_positions, boundary_positions


# ---------------------------------------------------------------------------
# Arabic reshaping (contextual joining forms)
# ---------------------------------------------------------------------------

def reshape_arabic(text: str) -> str:
    """Convert Arabic letters in ``text`` to their contextual joining forms.

    The input is in logical order. Harakat are preserved, while ligatures are
    disabled so each output character still maps to its input cursor position.
    """
    return _ARABIC_RESHAPER.reshape(text)


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
    return bidi_visual(text)


def contains_rtl(text: str) -> bool:
    """Return True if ``text`` contains any right-to-left character."""
    return any(_is_rtl_char(ch) for ch in text)
